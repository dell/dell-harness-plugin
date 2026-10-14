#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Create or inspect the user-scoped Dataloop MCP configuration."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import stat
import sys
from pathlib import Path


def default_config_path() -> Path:
    value = os.environ.get("DATALOOP_CONFIG_FILE")
    return (
        Path(value).expanduser()
        if value
        else Path.home() / ".config" / "dell-mcp" / "dataloop.json"
    )


def check(path: Path) -> int:
    if not path.is_file():
        print(f"configuration=missing path={path}")
        return 1
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"configuration=invalid path={path} error={exc}")
        return 1
    mode = stat.S_IMODE(path.stat().st_mode)
    command = value.get("command") or os.environ.get("DATALOOP_MCP_COMMAND")
    profiles = value.get("profiles") or "plugin-v1"
    print(f"configuration=present path={path} mode={mode:04o}")
    print(f"api_key={'present' if value.get('api_key') else 'missing'}")
    print(f"state_path={value.get('state_path', 'missing')}")
    print(f"gate_url={value.get('gate_url', 'Dataloop cloud default')}")
    print(f"verify_ssl={value.get('verify_ssl', 'true')}")
    print(f"profiles={profiles}")
    if command:
        usable = os.path.isabs(str(command)) and os.access(str(command), os.X_OK)
        print(f"command={command} absolute_executable={'yes' if usable else 'no'}")
    else:
        bundled = Path(__file__).resolve().parents[1] / "mcp-server" / "src"
        print(f"command=bundled-python-module resolved={'yes' if bundled.is_dir() else 'no'}")
    private = os.name != "posix" or mode & 0o077 == 0
    return 0 if value.get("api_key") and value.get("state_path") and private else 1


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
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)
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
    return (
        check(args.config.expanduser())
        if args.check
        else configure(args.config.expanduser(), args)
    )


if __name__ == "__main__":
    raise SystemExit(main())
