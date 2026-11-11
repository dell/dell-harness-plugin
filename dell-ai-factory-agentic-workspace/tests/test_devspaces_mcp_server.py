# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

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


class Kubeconfig:
    """Synthetic kubeconfig whose active context can change, as `oc login` does."""

    def __init__(self, active, names=("approved", "other")):
        self.active = active
        self.names = names
        self.loaded = []

    def list_kube_config_contexts(self, config_file=None):
        return [{"name": name} for name in self.names], {"name": self.active}

    def load_kube_config(self, config_file=None, context=None):
        self.loaded.append(context)


def install_kubeconfig(monkeypatch, kubeconfig, **overrides):
    monkeypatch.setattr(SERVER, "config", kubeconfig)
    monkeypatch.setattr(SERVER, "SETTINGS", settings(**overrides))
    monkeypatch.setattr(SERVER, "_client", None)
    monkeypatch.setattr(SERVER, "_session_context", None)
    monkeypatch.setattr(SERVER, "_startup_contexts", None)
    monkeypatch.setattr(SERVER.DevSpacesClient, "__init__", lambda self, effective: setattr(self, "settings", effective))


@pytest.mark.parametrize("configured", [(), ("approved",)])
def test_login_context_outside_allowlist_is_blocked(monkeypatch, configured):
    kubeconfig = Kubeconfig(active="approved")
    install_kubeconfig(monkeypatch, kubeconfig, allowed_contexts=configured)
    assert SERVER.allowed_contexts() == ("approved",)
    # `oc login` to another cluster switches the active kubeconfig context.
    kubeconfig.active = "other"
    SERVER.activate_context("other")
    with pytest.raises(PermissionError, match="outside the allowed contexts"):
        SERVER.get_client()
    assert SERVER.allowed_contexts() == ("approved",)


def test_login_context_inside_allowlist_is_used(monkeypatch):
    install_kubeconfig(monkeypatch, Kubeconfig(active="other"), allowed_contexts=("approved", "other"))
    SERVER.activate_context("approved")
    adapter = SERVER.get_client()
    assert adapter.settings.requested_context == "approved"
    assert adapter.settings.allowed_contexts == ("approved", "other")


def test_authentication_status_flags_disallowed_context(monkeypatch):
    install_kubeconfig(monkeypatch, Kubeconfig(active="approved"))
    SERVER.activate_context('approved')
    original = SERVER.get_client()
    monkeypatch.setattr(SERVER.AUTHENTICATION, "status", lambda instance_name=None: {
        "authenticated": True, "context": "other"})
    result = SERVER.get_authentication_status()
    assert result["context_allowed"] is False
    assert "outside the allowed contexts" in result["reason"]
    assert SERVER.get_client() is original
    assert SERVER._session_context == 'approved'


def test_status_does_not_switch_to_another_allowed_cluster(monkeypatch):
    install_kubeconfig(monkeypatch, Kubeconfig(active='approved'), allowed_contexts=('approved', 'other'))
    SERVER.activate_context('approved')
    original = SERVER.get_client()
    monkeypatch.setattr(SERVER.AUTHENTICATION, 'status', lambda instance_name=None: {
        'authenticated': True, 'context': 'other'})
    assert SERVER.get_authentication_status()['context_allowed'] is True
    assert SERVER._session_context == 'approved'
    assert SERVER.get_client() is original


def test_kubernetes_stop_is_advertised_as_destructive():
    import asyncio
    tools = asyncio.run(SERVER.mcp.list_tools())
    stop = next(tool for tool in tools if tool.name == 'stop_devspace')
    assert stop.annotations.destructiveHint is True
    assert stop.annotations.readOnlyHint is False
    assert stop.annotations.idempotentHint is True


def test_client_constructor_never_widens_explicit_allowlist(monkeypatch):
    kubeconfig = Kubeconfig(active="other")
    monkeypatch.setattr(SERVER, "config", kubeconfig)
    with pytest.raises(PermissionError, match="outside"):
        SERVER.DevSpacesClient(settings(allowed_contexts=("approved",)))
    assert kubeconfig.loaded == []


def test_allowlist_is_captured_at_startup_not_after_login(monkeypatch, tmp_path):
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text(
        "apiVersion: v1\nkind: Config\ncurrent-context: approved\n"
        "clusters: [{name: c, cluster: {server: 'https://api.approved.example:6443'}}]\n"
        "users: [{name: u, user: {}}]\n"
        "contexts: [{name: approved, context: {cluster: c, user: u}},"
        " {name: other, context: {cluster: c, user: u}}]\n"
    )
    monkeypatch.setenv("DEVSPACES_KUBECONFIG", str(kubeconfig))
    monkeypatch.delenv("DEVSPACES_ALLOWED_CONTEXTS", raising=False)
    spec = importlib.util.spec_from_file_location("devspaces_startup", SERVER_PATH)
    fresh = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, fresh)
    spec.loader.exec_module(fresh)
    # A background `oc login` later switches the active context before any tool call.
    kubeconfig.write_text(kubeconfig.read_text().replace("current-context: approved", "current-context: other"))
    assert fresh.allowed_contexts() == ("approved",)
    fresh.activate_context("other")
    with pytest.raises(PermissionError, match="outside the allowed contexts"):
        fresh.get_client()
