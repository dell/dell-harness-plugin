#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Create or inspect the user-scoped Dataloop MCP configuration."""

from __future__ import annotations

import argparse
import getpass
import importlib.util
import json
import os
import stat
import sys
import tempfile
import tomllib
from pathlib import Path


def default_config_path() -> Path:
    value = os.environ.get("DATALOOP_CONFIG_FILE")
    return (
        Path(value).expanduser()
        if value
        else Path.home() / ".config" / "dell-mcp" / "dataloop.json"
    )


def _launcher():
    # Loading the launcher must not leave bytecode in the installed plugin.
    sys.dont_write_bytecode = True
    path = Path(__file__).resolve().with_name("dataloop_mcp_launcher.py")
    spec = importlib.util.spec_from_file_location("dataloop_mcp_launcher", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _bundled_version() -> str:
    pyproject = Path(__file__).resolve().parents[1] / "mcp-server" / "pyproject.toml"
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]


def _command_version(executable: str) -> str | None:
    """The DOE server version an external console script would run, read from package metadata.

    Never executes the command or its interpreter: the shebang only locates the environment,
    whose installed ``*.dist-info/METADATA`` is read as text.
    """
    try:
        shebang = Path(executable).read_bytes()[:256].split(b"\n", 1)[0].decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    interpreter = shebang[2:].strip().split()[0] if shebang.startswith("#!") else ""
    if not os.path.isabs(interpreter):
        return None
    environment = Path(interpreter).parent.parent
    patterns = (
        "lib/python*/site-packages/dell_data_orchestration_engine_mcp-*.dist-info/METADATA",
        "Lib/site-packages/dell_data_orchestration_engine_mcp-*.dist-info/METADATA",
    )
    for pattern in patterns:
        for metadata in sorted(environment.glob(pattern)):
            for line in metadata.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("Version:"):
                    return line.split(":", 1)[1].strip() or None
    return None


def check(path: Path) -> int:
    """Report what the launcher would start, using the launcher's own resolution.

    Changes nothing: no file is created, repaired or prepared, nothing is imported, and a
    configured command is never run. When no command is configured, the launcher's own read-only
    readiness probe runs. Exit 0 only when launch would start without preparing or waiting.
    """
    launcher = _launcher()
    environ = {**os.environ, "DATALOOP_CONFIG_FILE": str(path)}
    if path.is_file() and not path.is_symlink():
        print(f"configuration=present path={path} mode={stat.S_IMODE(path.stat().st_mode):04o}")
    else:
        print(f"configuration={'symlink' if path.is_symlink() else 'missing'} path={path}")
    try:
        plan = launcher.preflight(environ)
    except launcher.ConfigurationError as exc:
        print(f"launch=blocked reason={exc}")
        return 1
    env, sources = plan["environment"], plan["sources"]

    def source(key: str) -> str:
        return sources[launcher.CONFIG_KEYS[key]]

    print(f"api_key=present source={source('api_key')}")
    print(f"state_path={plan['state_path']} source={source('state_path')}")
    print(f"gate_url={env.get('DATALOOP_GATE_URL', 'Dataloop cloud default')} source={source('gate_url')}")
    print(f"verify_ssl={env.get('DATALOOP_VERIFY_SSL', 'true')} source={source('verify_ssl')}")
    print(f"profiles={','.join(plan['profiles']) or 'all'} source={source('profiles')}")
    command = plan["command"]
    bundled = _bundled_version()
    if env.get("DATALOOP_MCP_COMMAND"):
        print(f"command={command[0]} source={source('command')}")
        running = _command_version(command[0])
        print(f"server_version={running or 'unknown'} bundled_version={bundled}")
        if running and running != bundled:
            print(
                f"warning=the configured command runs server {running}, not the bundled {bundled}; "
                "remove 'command' from the configuration to use the bundled server"
            )
    else:
        print(f"command=bundled-python-module runtime={'ready' if plan['runtime_ready'] else 'not-ready'} "
              f"server_version={bundled}")
    print(f"command_line={' '.join(command)}")
    if plan["runtime_blocked"]:
        # Launch would have to prepare or wait first, which can outlast a client's startup timeout.
        print(f"launch=blocked reason={plan['runtime_blocked']}")
        return 1
    print("launch=ready")
    return 0


def configure(path: Path, args: argparse.Namespace) -> int:
    api_key = getpass.getpass("Dataloop API key: ").strip()
    if not api_key:
        print("API key cannot be empty", file=sys.stderr)
        return 2
    state_path = Path(args.state_path).expanduser()
    state_path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        state_path.chmod(0o700)
    payload: dict[str, str] = {
        "api_key": api_key,
        "state_path": str(state_path),
        "verify_ssl": "false" if args.no_verify_ssl else "true",
    }
    if args.gate_url:
        payload["gate_url"] = args.gate_url
    if args.profiles:
        payload["profiles"] = ",".join(args.profiles)
    if args.command:
        if not os.path.isabs(os.path.expanduser(args.command)):
            print("--command must be an absolute path to an executable", file=sys.stderr)
            return 2
        payload["command"] = args.command
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        print(
            f"Refusing to overwrite symbolic-link configuration: {path}",
            file=sys.stderr,
        )
        return 2
    # mkstemp creates the file user-only (0600), so the key is never briefly
    # readable by others; os.replace swaps it in atomically.
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".dataloop-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2) + "\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    print(f"Wrote user-only Dataloop configuration to {path}")
    print("Restart Codex, then verify access with project_list.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="report non-secret configuration status"
    )
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument(
        "--state-path", default=str(Path.home() / ".doe-mcp" / "dataloop")
    )
    parser.add_argument("--gate-url", help="self-hosted Dataloop API gate URL")
    parser.add_argument(
        "--no-verify-ssl",
        action="store_true",
        help="lab-only: disable TLS verification",
    )
    parser.add_argument(
        "--profiles",
        nargs="+",
        choices=("plugin-v1", "discovery", "pipelines", "models", "rag", "all"),
        help="limit the registered MCP tool surface",
    )
    parser.add_argument(
        "--command", help="optional administrator-approved external MCP executable override"
    )
    args = parser.parse_args()
    if args.profiles and "all" in args.profiles and len(args.profiles) > 1:
        parser.error("--profiles all cannot be combined with other profiles")
    return (
        check(args.config.expanduser())
        if args.check
        else configure(args.config.expanduser(), args)
    )


if __name__ == "__main__":
    raise SystemExit(main())
