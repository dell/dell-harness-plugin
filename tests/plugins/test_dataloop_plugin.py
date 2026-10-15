# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import os
import secrets
import stat
import subprocess
import sys
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

    requirements = (ROOT / "requirements.toml").read_text(encoding="utf-8")
    assert "[mcp_servers.dell_dataloop]" in requirements
    assert 'identity = { command = "python" }' in requirements


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
