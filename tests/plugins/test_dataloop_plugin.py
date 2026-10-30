# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "dell-data-orchestration-engine"
EVAL_SCRIPT = ROOT / "scripts" / "aidp_eval.py"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_launcher():
    path = PLUGIN / "scripts" / "dataloop_mcp_launcher.py"
    spec = importlib.util.spec_from_file_location("dataloop_mcp_launcher", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_manifest_mcp_marketplace_and_governance_are_registered():
    manifest = load_json(PLUGIN / ".codex-plugin" / "plugin.json")
    assert manifest["name"] == "dell-data-orchestration-engine"
    assert manifest["version"] == "0.4.0"
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert manifest["homepage"].startswith("https://")
    assert manifest["author"]["url"].startswith("https://")
    for field in ("websiteURL", "privacyPolicyURL", "termsOfServiceURL"):
        assert manifest["interface"][field].startswith("https://")
    for field in ("composerIcon", "logo"):
        assert (PLUGIN / manifest["interface"][field]).is_file()
    assert manifest["interface"]["capabilities"] == ["Read", "Write"]
    assert len(manifest["interface"]["defaultPrompt"]) == 3

    mcp = load_json(PLUGIN / ".mcp.json")["mcpServers"]["dell_dataloop"]
    assert mcp["command"] == "python"
    assert mcp["args"] == ["${PLUGIN_ROOT}/scripts/dataloop_mcp_launcher.py"]
    assert "DATALOOP_API_KEY" not in json.dumps(mcp)
    bundled = PLUGIN / "mcp-server"
    assert (bundled / "pyproject.toml").is_file()
    assert (bundled / "src" / "doe_mcp" / "server.py").is_file()
    assert (bundled / "tests" / "test_plugin_contract.py").is_file()

    marketplace = load_json(ROOT / ".agents" / "plugins" / "marketplace.json")
    entries = {item["name"]: item for item in marketplace["plugins"]}
    entry = entries["dell-data-orchestration-engine"]
    assert entry["source"] == {
        "source": "local",
        "path": "./dell-data-orchestration-engine",
    }
    assert entry["policy"] == {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }

    requirements = tomllib.loads((ROOT / "requirements.toml").read_text(encoding="utf-8"))
    identity = requirements["plugins"]["dell-data-orchestration-engine"]["mcp_servers"]["dell_dataloop"]["identity"]
    assert identity["command"]["executable"] == "python"
    [rule] = identity["command"]["args"]
    assert rule["match"] == "regex"
    assert re.fullmatch(rule["value"], str(PLUGIN / "scripts" / "dataloop_mcp_launcher.py"))
    assert not re.fullmatch(rule["value"], "/tmp/other.py")


def test_six_outcome_skills_have_metadata_and_no_placeholders():
    skills = sorted((PLUGIN / "skills").glob("*/SKILL.md"))
    assert len(skills) == 6
    for path in skills:
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---\nname:")
        assert "description:" in text
        assert "[TODO:" not in text
        metadata = path.parent / "agents" / "openai.yaml"
        metadata_text = metadata.read_text(encoding="utf-8")
        assert "display_name:" in metadata_text
        assert "short_description:" in metadata_text
        assert f"${path.parent.name}" in metadata_text


def test_launcher_requires_private_config_and_never_serializes_secret(tmp_path):
    launcher = load_launcher()
    # Random per run: no credential-shaped literal in source, and no coincidental match.
    api_key = secrets.token_urlsafe(16)
    config = tmp_path / "dataloop.json"
    config.write_text(
        json.dumps(
            {
                "api_key": api_key,
                "state_path": str(tmp_path / "state"),
                "profiles": "discovery,models",
            }
        ),
        encoding="utf-8",
    )
    config.chmod(0o600)

    loaded = launcher.load_config(config)
    environment = launcher.merged_environment({}, loaded)
    launcher.validate_environment(environment)
    assert environment["DATALOOP_API_KEY"] == api_key
    assert environment["DATALOOP_PATH"] == str(tmp_path / "state")
    assert launcher.profiles_from(environment["DATALOOP_MCP_PROFILES"]) == [
        "discovery",
        "models",
    ]
    assert launcher.profiles_from(None) == ["plugin-v1"]
    assert launcher.profiles_from("all") == []
    assert api_key not in json.dumps(load_json(PLUGIN / ".mcp.json"))

    config.chmod(0o644)
    try:
        launcher.load_config(config)
    except launcher.ConfigurationError as exc:
        assert "chmod 600" in str(exc)
        assert api_key not in str(exc)
    else:
        raise AssertionError("group-readable credentials must be rejected")

    config.unlink()
    state = tmp_path / "state"
    state.chmod(0o755)
    launcher.validate_environment(environment)
    if os.name == "posix":
        assert stat.S_IMODE(state.stat().st_mode) == 0o700


def test_launcher_builds_profiled_command_and_rejects_unknown_profile(tmp_path):
    launcher = load_launcher()
    bundled_environment = {"DATALOOP_MCP_PROFILES": "plugin-v1"}
    assert launcher.build_command(bundled_environment) == [
        sys.executable,
        "-m",
        "doe_mcp.server",
        "--profile",
        "plugin-v1",
    ]
    assert bundled_environment["PYTHONPATH"].split(os.pathsep)[0] == str(
        PLUGIN / "mcp-server" / "src"
    )

    executable = tmp_path / "doe-mcp"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    assert launcher.build_command(
        {
            "DATALOOP_MCP_COMMAND": str(executable),
            "DATALOOP_MCP_PROFILES": "rag,discovery,rag",
        }
    ) == [str(executable), "--profile", "rag", "--profile", "discovery"]

    try:
        launcher.profiles_from("discovery,administrator")
    except launcher.ConfigurationError as exc:
        assert "administrator" in str(exc)
    else:
        raise AssertionError("unknown profiles must be rejected")


def test_dataloop_evaluation_definition_and_contract_inventory_validate():
    completed = subprocess.run(
        [sys.executable, str(EVAL_SCRIPT), "validate"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    summary = json.loads(completed.stdout)["plugins"]["dell-data-orchestration-engine"]
    assert summary["workflows"] == 8
    assert summary["submission_cases"]["positive"] >= 5
    assert summary["submission_cases"]["negative"] >= 3

    definition = load_json(ROOT / "evals" / "cases" / "dataloop.json")
    assert len(definition["contract_tools"]) == 42
    assert definition["contract_tools"] == definition["declared_tools"]
    assert len(definition["upstream_tools"]) == 70
    assert len(set(definition["upstream_tools"])) == 70
    assert set(definition["contract_tools"]) <= set(definition["upstream_tools"])

    fixture = subprocess.run(
        [sys.executable, str(EVAL_SCRIPT), "fixture", "dell-data-orchestration-engine"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    fixture_summary = json.loads(fixture.stdout)
    assert fixture_summary["passed"] is True
    assert fixture_summary["data_classification"] == "synthetic_non_customer"
    assert fixture_summary["positive_cases"] >= 5
    assert fixture_summary["negative_cases"] >= 3


def test_configuration_check_does_not_print_api_key(tmp_path):
    api_key = secrets.token_urlsafe(16)
    config = tmp_path / "dataloop.json"
    config.write_text(
        json.dumps(
            {
                "api_key": api_key,
                "state_path": str(tmp_path / "state"),
                "command": sys.executable,
            }
        ),
        encoding="utf-8",
    )
    config.chmod(0o600)
    completed = subprocess.run(
        [
            sys.executable,
            str(PLUGIN / "scripts" / "configure_dataloop.py"),
            "--check",
            "--config",
            str(config),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "api_key=present" in completed.stdout
    assert api_key not in completed.stdout


def test_launcher_rejects_a_command_resolved_through_path():
    launcher = load_launcher()
    try:
        launcher.build_command({"DATALOOP_MCP_COMMAND": "python3"})
    except launcher.ConfigurationError as exc:
        assert "absolute path" in str(exc)
    else:
        raise AssertionError("a bare command name must not be resolved through PATH")


def test_launcher_rejects_a_world_writable_command(tmp_path):
    if os.name != "posix":
        return
    launcher = load_launcher()
    executable = tmp_path / "doe-mcp"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o777)
    try:
        launcher.build_command({"DATALOOP_MCP_COMMAND": str(executable)})
    except launcher.ConfigurationError as exc:
        assert "world-writable" in str(exc)
    else:
        raise AssertionError("a tamperable executable must be rejected")

    executable.chmod(0o755)
    command = launcher.build_command({"DATALOOP_MCP_COMMAND": str(executable)})
    assert command[0] == str(executable)
    assert command[1:] == ["--profile", "plugin-v1"]


def test_launcher_rejects_a_world_writable_directory_and_runs_the_resolved_target(tmp_path):
    if os.name != "posix":
        return
    launcher = load_launcher()
    shared = tmp_path / "shared"
    shared.mkdir()
    executable = shared / "doe-mcp"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    shared.chmod(0o777)
    try:
        launcher.build_command({"DATALOOP_MCP_COMMAND": str(executable)})
    except launcher.ConfigurationError as exc:
        assert "directory" in str(exc)
    else:
        raise AssertionError("an executable in a world-writable directory must be rejected")

    shared.chmod(0o755)
    link = tmp_path / "doe-mcp-link"
    link.symlink_to(executable)
    command = launcher.build_command({"DATALOOP_MCP_COMMAND": str(link)})
    # The checked file is the one executed, so the link cannot be retargeted afterwards.
    assert command[0] == str(executable.resolve())


def test_runtime_reuses_launching_interpreter_when_server_imports(monkeypatch):
    launcher = load_launcher()
    monkeypatch.setattr(launcher, "server_importable", lambda python, environ: True)
    assert launcher.runtime_python({}) == Path(sys.executable)


def test_runtime_prepares_isolated_venv_with_bundled_requirements(tmp_path, monkeypatch):
    launcher = load_launcher()
    calls = []
    prepared = {"done": False}

    def importable(python, environ):
        return prepared["done"] and str(python).startswith(str(tmp_path))

    def run(command, **kwargs):
        calls.append(command)
        if "pip" in command:
            prepared["done"] = True
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(launcher, "server_importable", importable)
    monkeypatch.setattr(launcher.subprocess, "run", run)
    python = launcher.runtime_python({"DATALOOP_RUNTIME_DIR": str(tmp_path)})
    assert str(python).startswith(str(tmp_path))
    assert calls[0][:3] == [sys.executable, "-m", "venv"]
    assert any(arg.startswith("mcp>=2.0") for arg in calls[1])
    assert not list(tmp_path.glob("*.lock"))


def test_runtime_preparation_failure_is_a_configuration_error(tmp_path, monkeypatch):
    launcher = load_launcher()
    monkeypatch.setattr(launcher, "server_importable", lambda python, environ: False)

    def run(command, **kwargs):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(launcher.subprocess, "run", run)
    try:
        launcher.runtime_python({"DATALOOP_RUNTIME_DIR": str(tmp_path)})
    except launcher.ConfigurationError as exc:
        assert "isolated runtime" in str(exc)
    else:
        raise AssertionError("failed preparation must be reported")
    assert not list(tmp_path.glob("*.lock"))


def test_windows_launch_keeps_waiting_parent(tmp_path, monkeypatch):
    launcher = load_launcher()
    monkeypatch.setenv("DATALOOP_CONFIG_FILE", str(tmp_path / "absent.json"))
    monkeypatch.setenv("DATALOOP_API_KEY", secrets.token_urlsafe(8))
    monkeypatch.setenv("DATALOOP_PATH", str(tmp_path / "state"))
    monkeypatch.delenv("DATALOOP_MCP_COMMAND", raising=False)
    monkeypatch.setattr(launcher, "runtime_python", lambda environ: Path(sys.executable))
    calls = []
    monkeypatch.setattr(launcher.subprocess, "call", lambda command, env: calls.append(command) or 7)
    monkeypatch.setattr(launcher.os, "execve", lambda *a: (_ for _ in ()).throw(AssertionError("execve on nt")))
    monkeypatch.setattr(launcher.os, "name", "nt")
    assert launcher.main() == 7
    assert calls[0][1:3] == ["-m", "doe_mcp.server"]


def load_configurer():
    path = PLUGIN / "scripts" / "configure_dataloop.py"
    spec = importlib.util.spec_from_file_location("configure_dataloop", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_configure_writes_key_user_only_from_creation(tmp_path, monkeypatch):
    configurer = load_configurer()
    api_key = secrets.token_urlsafe(16)
    monkeypatch.setattr(configurer.getpass, "getpass", lambda prompt: api_key)
    modes = []
    real_replace = os.replace

    def replace(source, target):
        modes.append(stat.S_IMODE(os.stat(source).st_mode))
        real_replace(source, target)

    monkeypatch.setattr(configurer.os, "replace", replace)
    config = tmp_path / "cfg" / "dataloop.json"
    config.parent.mkdir()
    config.write_text("{}", encoding="utf-8")
    config.chmod(0o644)
    args = configurer.argparse.Namespace(
        state_path=str(tmp_path / "state"), no_verify_ssl=False, gate_url=None,
        profiles=None, command=None,
    )
    assert configurer.configure(config, args) == 0
    if os.name == "posix":
        assert modes == [0o600]
        assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert json.loads(config.read_text(encoding="utf-8"))["api_key"] == api_key
    assert [p.name for p in config.parent.iterdir()] == ["dataloop.json"]


def test_configure_rejects_all_mixed_with_other_profiles(tmp_path):
    completed = subprocess.run(
        [sys.executable, str(PLUGIN / "scripts" / "configure_dataloop.py"),
         "--config", str(tmp_path / "c.json"), "--profiles", "all", "rag"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )
    assert completed.returncode == 2
    assert "cannot be combined" in completed.stderr
    assert not (tmp_path / "c.json").exists()
