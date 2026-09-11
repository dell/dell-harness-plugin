#!/usr/bin/env python3
"""Launch the bundled Dataloop MCP server with user-scoped configuration."""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from pathlib import Path

ALLOWED_PROFILES = {"discovery", "pipelines", "models", "plugin-v1", "rag"}
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
BUNDLED_SERVER_SRC = PLUGIN_ROOT / "mcp-server" / "src"
CONFIG_KEYS = {
    "api_key": "DATALOOP_API_KEY",
    "state_path": "DATALOOP_PATH",
    "gate_url": "DATALOOP_GATE_URL",
    "verify_ssl": "DATALOOP_VERIFY_SSL",
    "profiles": "DATALOOP_MCP_PROFILES",
    "command": "DATALOOP_MCP_COMMAND",
}


class ConfigurationError(ValueError):
    """Raised when local Dataloop configuration is missing or unsafe."""


def config_path(environ: dict[str, str]) -> Path:
    configured = environ.get("DATALOOP_CONFIG_FILE")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".config" / "dell-mcp" / "dataloop.json"


def load_config(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    if path.is_symlink():
        raise ConfigurationError(
            f"Dataloop configuration cannot be a symbolic link: {path}"
        )
    mode = stat.S_IMODE(path.stat().st_mode)
    if os.name == "posix" and mode & 0o077:
        raise ConfigurationError(
            f"Dataloop configuration must be user-only (chmod 600): {path}"
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(
            f"Cannot read Dataloop configuration {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise ConfigurationError("Dataloop configuration must be a JSON object")
    return value


def merged_environment(
    environ: dict[str, str], config: dict[str, object]
) -> dict[str, str]:
    result = dict(environ)
    for key, env_name in CONFIG_KEYS.items():
        if env_name not in result and config.get(key) not in (None, ""):
            result[env_name] = str(config[key])
    return result


def profiles_from(value: str | None) -> list[str]:
    if value is None or not value.strip():
        return ["plugin-v1"]
    if value.strip().lower() == "all":
        return []
    profiles = [part.strip() for part in value.split(",") if part.strip()]
    unknown = sorted(set(profiles) - ALLOWED_PROFILES)
    if unknown:
        raise ConfigurationError(
            "Unknown Dataloop MCP profile(s): "
            + ", ".join(unknown)
            + ". Allowed: "
            + ", ".join(sorted(ALLOWED_PROFILES))
        )
    return list(dict.fromkeys(profiles))


def build_command(environ: dict[str, str]) -> list[str]:
    requested = environ.get("DATALOOP_MCP_COMMAND")
    if requested:
        executable = shutil.which(requested)
        if executable is None:
            raise ConfigurationError(f"Cannot find configured MCP command '{requested}'.")
        command = [executable]
    else:
        server_module = BUNDLED_SERVER_SRC / "doe_mcp" / "server.py"
        if not server_module.is_file():
            raise ConfigurationError(
                f"Bundled Dataloop MCP server is missing: {server_module}"
            )
        existing_pythonpath = environ.get("PYTHONPATH")
        environ["PYTHONPATH"] = (
            str(BUNDLED_SERVER_SRC)
            if not existing_pythonpath
            else str(BUNDLED_SERVER_SRC) + os.pathsep + existing_pythonpath
        )
        command = [sys.executable, "-m", "doe_mcp.server"]
    for profile in profiles_from(environ.get("DATALOOP_MCP_PROFILES")):
        command.extend(["--profile", profile])
    return command


def validate_environment(environ: dict[str, str]) -> None:
    missing = [
        name for name in ("DATALOOP_API_KEY", "DATALOOP_PATH") if not environ.get(name)
    ]
    if missing:
        raise ConfigurationError(
            "Missing required Dataloop setting(s): "
            + ", ".join(missing)
            + ". Run configure_dataloop.py or set them in the process environment."
        )
    state_path = Path(environ["DATALOOP_PATH"]).expanduser()
    if state_path.is_symlink():
        raise ConfigurationError(
            f"DATALOOP_PATH cannot be a symbolic link: {state_path}"
        )
    state_path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not state_path.is_dir():
        raise ConfigurationError(f"DATALOOP_PATH is not a directory: {state_path}")
    if os.name == "posix":
        # Repair state directories created by an older or more permissive
        # setup. mkdir(mode=...) does not affect an existing directory.
        state_path.chmod(0o700)
    state_mode = stat.S_IMODE(state_path.stat().st_mode)
    if os.name == "posix" and state_mode & 0o077:
        raise ConfigurationError(
            f"DATALOOP_PATH must be user-only (chmod 700): {state_path}"
        )
    environ["DATALOOP_PATH"] = str(state_path)


def main() -> int:
    try:
        path = config_path(os.environ)
        config = load_config(path)
        environment = merged_environment(dict(os.environ), config)
        validate_environment(environment)
        command = build_command(environment)
    except ConfigurationError as exc:
        print(f"dell-dataloop configuration error: {exc}", file=sys.stderr)
        return 2

    os.execvpe(command[0], command, environment)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
