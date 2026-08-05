import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SERVER_PATH = Path(__file__).parents[1] / "scripts" / "devspaces_mcp_server.py"
SPEC = importlib.util.spec_from_file_location("devspaces_mcp_server", SERVER_PATH)
SERVER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SERVER
SPEC.loader.exec_module(SERVER)


def settings(**overrides):
    values = {
        "kubeconfig": None,
        "requested_context": None,
        "allowed_contexts": (),
        "allowed_namespaces": ("team-a",),
        "enable_writes": False,
        "enable_delete": False,
        "instances_file": str(
            Path(__file__).parents[1] / "config" / "instances.json"
        ),
        "default_instance": None,
        "oc_command": "oc",
    }
    values.update(overrides)
    return SERVER.Settings(**values)


def bare_client(**overrides):
    adapter = object.__new__(SERVER.DevSpacesClient)
    adapter.settings = settings(**overrides)
    adapter.namespaces = adapter.settings.allowed_namespaces
    return adapter


def test_defaults_are_read_only(monkeypatch):
    for name in (
        "DEVSPACES_ENABLE_WRITES",
        "DEVSPACES_ENABLE_DELETE",
        "DEVSPACES_ALLOWED_NAMESPACES",
    ):
        monkeypatch.delenv(name, raising=False)
    actual = SERVER.Settings.from_env()
    assert actual.enable_writes is False
    assert actual.enable_delete is False
    assert actual.allowed_namespaces == ()


def instance(name="primary", default=True):
    return SERVER.DevSpacesInstance(
        name=name,
        display_name=name.title(),
        api_url=f"https://api.{name}.example:6443",
        ui_url=f"https://ui.{name}.example",
        default=default,
    )


def test_registry_resolves_named_and_default_instances():
    registry = SERVER.InstanceRegistry(
        (instance("primary", True), instance("secondary", False))
    )
    assert registry.resolve().name == "primary"
    assert registry.resolve("secondary").api_url == "https://api.secondary.example:6443"


def test_registry_rejects_untrusted_instance_url():
    with pytest.raises(ValueError, match="credential-free HTTPS"):
        SERVER.DevSpacesInstance.from_dict(
            {
                "name": "bad",
                "display_name": "Bad",
                "api_url": "http://user:password@example.invalid",
                "ui_url": "https://ui.example.invalid",
            }
        )


def test_authentication_status_returns_identity_but_no_command_output(monkeypatch):
    registry = SERVER.InstanceRegistry((instance(),))
    manager = SERVER.AuthenticationManager(settings(), registry)
    responses = {
        ("whoami", "--show-server"): subprocess.CompletedProcess(
            [], 0, "https://api.primary.example:6443\n", ""
        ),
        ("whoami",): subprocess.CompletedProcess([], 0, "user@example.com\n", ""),
        ("config", "current-context"): subprocess.CompletedProcess(
            [], 0, "primary/user\n", ""
        ),
    }
    monkeypatch.setattr(manager, "_oc_path", lambda: "oc")
    monkeypatch.setattr(
        manager, "_run", lambda arguments, timeout=30: responses[tuple(arguments)]
    )
    result = manager.status()
    assert result["authenticated"] is True
    assert result["username"] == "user@example.com"
    assert result["context"] == "primary/user"
    assert "token" not in str(result).lower()


def test_authentication_initiates_registered_web_login(monkeypatch):
    registry = SERVER.InstanceRegistry((instance(),))
    manager = SERVER.AuthenticationManager(settings(), registry)
    monkeypatch.setattr(
        manager,
        "status",
        lambda instance_name=None: {
            "authenticated": False,
            "instance": instance().public(),
        },
    )
    monkeypatch.setattr(manager, "_oc_path", lambda: "/managed/bin/oc")
    calls = []

    class Process:
        def __init__(self, arguments, **kwargs):
            calls.append((arguments, kwargs))

        def poll(self):
            return None

    monkeypatch.setattr(SERVER.subprocess, "Popen", Process)
    result = manager.authenticate()
    assert calls[0][0] == [
        "/managed/bin/oc",
        "login",
        "https://api.primary.example:6443",
        "--web",
    ]
    assert "shell" not in calls[0][1]
    assert result["login_started"] is True
    assert result["login_state"] == "waiting_for_browser"


def test_workspace_summary_excludes_spec_and_secrets():
    workspace = {
        "metadata": {
            "name": "sample",
            "namespace": "team-a",
            "creationTimestamp": "2026-08-05T12:00:00Z",
        },
        "spec": {"started": True, "sensitive": "must-not-leak"},
        "status": {"phase": "Running", "mainUrl": "https://example.invalid"},
    }
    result = SERVER.DevSpacesClient.summarize_workspace(workspace)
    assert set(result) == {
        "name",
        "namespace",
        "started",
        "phase",
        "message",
        "main_url",
        "created",
    }
    assert "must-not-leak" not in str(result)


def test_write_gate_fails_before_api_call():
    adapter = bare_client()
    with pytest.raises(PermissionError, match="writes are disabled"):
        adapter.set_started("team-a", "sample", True)


def test_delete_requires_exact_confirmation_before_api_call():
    adapter = bare_client(enable_delete=True)
    with pytest.raises(ValueError, match="exactly match"):
        adapter.delete_workspace("team-a", "sample", "different")


def test_namespace_boundary_is_exact():
    adapter = bare_client()
    adapter._authorize_namespace("team-a")
    with pytest.raises(PermissionError, match="not authorized"):
        adapter._authorize_namespace("team-b")


def test_workspace_pod_discovery_uses_only_workspace_labels():
    adapter = bare_client()
    calls = []
    pod = SimpleNamespace(metadata=SimpleNamespace(name="sample-pod"))

    class Core:
        def list_namespaced_pod(self, namespace, label_selector):
            calls.append((namespace, label_selector))
            return SimpleNamespace(items=[pod])

    adapter.core = Core()
    result = adapter._workspace_pods("team-a", "sample")
    assert [item.metadata.name for item in result] == ["sample-pod"]
    assert calls == [
        ("team-a", f"{label}=sample") for label in SERVER.WORKSPACE_POD_LABELS
    ]
