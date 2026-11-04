#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Launch the bundled Dataloop MCP server with user-scoped configuration."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import time
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
    status = path.stat()
    mode = stat.S_IMODE(status.st_mode)
    if os.name == "posix" and mode & 0o077:
        raise ConfigurationError(
            f"Dataloop configuration must be user-only (chmod 600): {path}"
        )
    if os.name == "posix" and status.st_uid != os.getuid():
        raise ConfigurationError(
            f"Dataloop configuration must be owned by the current user: {path}"
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


def trusted_executable(requested: str) -> str:
    """Validate a configured MCP command before it is executed.

    Only an absolute path is accepted, so the command cannot be redirected through PATH. On
    POSIX neither the resolved file nor its directory may be writable by group or others. The
    resolved path is what gets executed, so a symlink cannot be swapped after the check.
    """
    path = Path(requested).expanduser()
    if not path.is_absolute():
        raise ConfigurationError(
            "DATALOOP_MCP_COMMAND must be an absolute path to an executable, not a name "
            "resolved through PATH."
        )
    resolved = path.resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ConfigurationError(f"Configured MCP command is not an executable file: {requested}")
    if os.name == "posix":
        for target in (resolved, resolved.parent):
            if stat.S_IMODE(target.stat().st_mode) & 0o022:
                raise ConfigurationError(
                    f"Configured MCP command and its directory must not be group- or "
                    f"world-writable: {target}"
                )
    return str(resolved)


def bundled_pythonpath(environ: dict[str, str]) -> str:
    existing = environ.get("PYTHONPATH")
    return str(BUNDLED_SERVER_SRC) if not existing else str(BUNDLED_SERVER_SRC) + os.pathsep + existing


def server_importable(python: Path, environ: dict[str, str]) -> bool:
    # Same PYTHONPATH the server will run with, so the check cannot disagree with the launch.
    env = {**environ, "PYTHONPATH": bundled_pythonpath(environ)}
    try:
        return subprocess.run(
            [str(python), "-c", "import doe_mcp.server"], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def runtime_python_path(environ: dict[str, str]) -> Path:
    """Where the isolated runtime's interpreter lives, keyed to this Python and pyproject."""
    pyproject = (BUNDLED_SERVER_SRC.parent / "pyproject.toml").read_bytes()
    identity = hashlib.sha256(sys.executable.encode() + sys.version.encode() + pyproject).hexdigest()[:16]
    base = Path(environ.get("DATALOOP_RUNTIME_DIR") or
                Path(environ.get("LOCALAPPDATA", str(Path.home() / ".cache"))) / "Dell" / "Dataloop" / "runtimes")
    return base / identity / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def runtime_python(environ: dict[str, str]) -> Path:
    """Return an interpreter that can run the bundled server.

    Reuses the launching interpreter when it already satisfies the bundled
    dependencies; otherwise prepares a reusable venv outside the plugin cache, so
    the pinned mcp 2.0 line never conflicts with other plugins sharing `python`.
    """
    import tomllib

    if server_importable(Path(sys.executable), environ):
        return Path(sys.executable)
    pyproject = (BUNDLED_SERVER_SRC.parent / "pyproject.toml").read_bytes()
    requirements = tomllib.loads(pyproject.decode("utf-8"))["project"]["dependencies"]
    python = runtime_python_path(environ)
    target = python.parents[1]
    if server_importable(python, environ):
        return python
    target.parent.mkdir(parents=True, exist_ok=True)
    # Atomic directory lock coordinates simultaneous plugin starts; a stale lock
    # is reported rather than deleted while another bootstrap could be active.
    lock = target.with_suffix(".lock")
    for attempt in range(300):
        try:
            lock.mkdir()
            break
        except FileExistsError:
            if server_importable(python, environ):
                return python
            if attempt == 0:
                print("dell-dataloop: waiting for the active runtime preparation.", file=sys.stderr, flush=True)
            time.sleep(1)
    else:
        raise ConfigurationError(
            f"Runtime preparation is still active or was interrupted. If no other plugin start is "
            f"preparing it, remove {lock} and run this launcher with --prepare-only."
        )
    try:
        if server_importable(python, environ):
            return python
        print("dell-dataloop: preparing isolated Python runtime.", file=sys.stderr, flush=True)
        subprocess.run([sys.executable, "-m", "venv", str(target)], check=True,
                       stdout=sys.stderr, stderr=sys.stderr, timeout=120)
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", *requirements],
                       check=True, stdout=sys.stderr, stderr=sys.stderr, timeout=900)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConfigurationError(
            "Could not prepare the isolated runtime; check Python venv support, package-network "
            "access, and approved CA trust."
        ) from exc
    finally:
        lock.rmdir()
    if not server_importable(python, environ):
        raise ConfigurationError("Prepared runtime cannot import the bundled MCP server.")
    return python


def build_command(environ: dict[str, str], python: Path | str = sys.executable) -> list[str]:
    requested = environ.get("DATALOOP_MCP_COMMAND")
    if requested:
        command = [trusted_executable(requested)]
    else:
        server_module = BUNDLED_SERVER_SRC / "doe_mcp" / "server.py"
        if not server_module.is_file():
            raise ConfigurationError(
                f"Bundled Dataloop MCP server is missing: {server_module}"
            )
        environ["PYTHONPATH"] = bundled_pythonpath(environ)
        command = [str(python), "-m", "doe_mcp.server"]
    for profile in profiles_from(environ.get("DATALOOP_MCP_PROFILES")):
        command.extend(["--profile", profile])
    return command


def require_settings(environ: dict[str, str]) -> Path:
    """Check the required settings without changing anything; return the state path."""
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
    if state_path.exists() and not state_path.is_dir():
        raise ConfigurationError(f"DATALOOP_PATH is not a directory: {state_path}")
    if os.name == "posix" and state_path.exists() and state_path.stat().st_uid != os.getuid():
        raise ConfigurationError(f"DATALOOP_PATH must be owned by the current user: {state_path}")
    if not state_path.exists():
        parent = next(p for p in state_path.parents if p.exists())
        if not os.access(parent, os.W_OK | os.X_OK):
            raise ConfigurationError(f"Cannot create DATALOOP_PATH {state_path}: {parent} is not writable")
    return state_path


def validate_environment(environ: dict[str, str]) -> None:
    state_path = require_settings(environ)
    try:
        state_path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            # Repair state directories created by an older or more permissive
            # setup. mkdir(mode=...) does not affect an existing directory.
            state_path.chmod(0o700)
    except OSError as exc:
        raise ConfigurationError(f"Cannot prepare DATALOOP_PATH {state_path}: {exc.strerror}") from exc
    state_mode = stat.S_IMODE(state_path.stat().st_mode)
    if os.name == "posix" and state_mode & 0o077:
        raise ConfigurationError(
            f"DATALOOP_PATH must be user-only (chmod 700): {state_path}"
        )
    environ["DATALOOP_PATH"] = str(state_path)


def effective_settings(environ: dict[str, str]) -> tuple[Path, dict[str, str], dict[str, str]]:
    """Resolve the configuration exactly as launch does.

    Returns the configuration path, the merged environment (process environment wins over the
    file), and where each setting came from: ``env``, ``file`` or ``missing``.
    """
    path = config_path(environ)
    merged = merged_environment(environ, load_config(path))
    sources = {
        name: "env" if environ.get(name) else "file" if merged.get(name) else "missing"
        for name in CONFIG_KEYS.values()
    }
    return path, merged, sources


def planned_runtime(environ: dict[str, str]) -> tuple[Path, bool]:
    """The interpreter launch would run the bundled server with, and whether it is ready.

    Same choice as `runtime_python`, without preparing anything.
    """
    if server_importable(Path(sys.executable), environ):
        return Path(sys.executable), True
    python = runtime_python_path(environ)
    return python, server_importable(python, environ)


def preflight(environ: dict[str, str]) -> dict[str, object]:
    """Everything launch would decide, in launch's order, without changing anything.

    Nothing is created, repaired or prepared and a configured command is never run; without
    one, the same import probe launch uses checks the bundled server.

    Raises the same `ConfigurationError` launch would. `configure_dataloop.py --check` reports
    this, so the check cannot disagree with what actually starts.
    """
    path, merged, sources = effective_settings(environ)
    state_path = require_settings(merged)
    runtime_ready = True
    if merged.get("DATALOOP_MCP_COMMAND"):
        command = build_command(dict(merged))
    else:
        python, runtime_ready = planned_runtime(merged)
        command = build_command(dict(merged), python)
    return {
        "config_path": path,
        "environment": merged,
        "sources": sources,
        "state_path": state_path,
        "profiles": profiles_from(merged.get("DATALOOP_MCP_PROFILES")),
        "command": command,
        "runtime_ready": runtime_ready,
    }


def main() -> int:
    if sys.version_info < (3, 11):
        print("dell-dataloop: Python 3.11 or newer is required.", file=sys.stderr)
        return 2
    if sys.argv[1:] == ["--prepare-only"]:
        # Prepare the isolated runtime ahead of the first plugin start, which can take
        # longer than an MCP client's startup timeout. Needs no Dataloop credentials.
        try:
            print(json.dumps({"state": "ready", "python": str(runtime_python(dict(os.environ)))}))
        except ConfigurationError as exc:
            print(f"dell-dataloop runtime error: {exc}", file=sys.stderr)
            return 1
        return 0
    try:
        environment = effective_settings(dict(os.environ))[1]
        validate_environment(environment)
        if environment.get("DATALOOP_MCP_COMMAND"):
            command = build_command(environment)
        else:
            command = build_command(environment, runtime_python(environment))
    except ConfigurationError as exc:
        print(f"dell-dataloop configuration error: {exc}", file=sys.stderr)
        return 2

    # command[0] is always absolute (validated or a resolved interpreter), so no PATH search occurs.
    if os.name == "nt":
        # Windows execve detaches the replacement from some process supervisors and
        # breaks inherited stdio; keep a waiting parent instead.
        return subprocess.call(command, env=environment)
    os.execve(command[0], command, environment)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
