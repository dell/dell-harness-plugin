#!/usr/bin/env python3
"""Provision a private DATALOOP_PATH directory for one Dataloop deployment."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

DEFAULT_BASE = Path.home() / ".doe-mcp"
MARKER_NAME = ".gate-url"
DIR_MODE = 0o700
MARKER_MODE = 0o600
_UNSAFE = re.compile(r"[^a-z0-9-]+")


def slugify(alias: str) -> str:
    """Return a safe directory-name form of a deployment alias."""
    slug = _UNSAFE.sub("-", alias.strip().lower()).strip("-")
    if not slug:
        raise ValueError(f"{alias!r} has no usable characters for a directory name.")
    return slug


def state_dir_for(alias: str, *, base: Path = DEFAULT_BASE) -> Path:
    return base / f"dataloop-{slugify(alias)}"


def provision(alias: str, gate_url: str | None, *, base: Path = DEFAULT_BASE) -> Path:
    """Create or repair one deployment-specific state directory."""
    if base.is_symlink():
        raise ValueError(
            f"{base} is a symlink; refusing to redirect bearer-token state."
        )
    base_existed = base.exists()
    directory = state_dir_for(alias, base=base)
    if directory.is_symlink():
        raise ValueError(
            f"{directory} is a symlink; refusing to redirect bearer-token state."
        )
    if directory.exists() and not directory.is_dir():
        raise ValueError(f"{directory} exists but is not a directory.")

    directory.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    if os.name == "posix":
        # Tighten the helper-owned default base, or a base created by this call.
        # Never chmod an arbitrary pre-existing custom base such as /tmp.
        if base == DEFAULT_BASE or not base_existed:
            os.chmod(base, DIR_MODE)
        os.chmod(directory, DIR_MODE)
    marker = directory / MARKER_NAME
    recorded = marker.read_text(encoding="utf-8").strip() if marker.exists() else None

    if gate_url is None:
        if recorded is None:
            print(
                f"warning: {directory} has no recorded gate URL; pass --gate-url "
                "once to bind this alias to a deployment.",
                file=sys.stderr,
            )
        return directory

    normalized = gate_url.strip().rstrip("/")
    if recorded is None:
        marker.write_text(normalized + "\n", encoding="utf-8")
        if os.name == "posix":
            os.chmod(marker, MARKER_MODE)
    elif recorded != normalized:
        raise ValueError(
            f"alias {alias!r} is already provisioned for {recorded!r}, not "
            f"{normalized!r}; use another alias."
        )
    return directory


def list_aliases(
    base: Path = DEFAULT_BASE,
) -> list[tuple[str, Path, str | None]]:
    if not base.is_dir():
        return []
    rows: list[tuple[str, Path, str | None]] = []
    for entry in sorted(base.iterdir()):
        if (
            entry.is_symlink()
            or not entry.is_dir()
            or not entry.name.startswith("dataloop-")
        ):
            continue
        marker = entry / MARKER_NAME
        gate_url = (
            marker.read_text(encoding="utf-8").strip() if marker.exists() else None
        )
        rows.append((entry.name.removeprefix("dataloop-"), entry, gate_url))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "alias", nargs="?", help="Short deployment name, e.g. cloud or cto-lab."
    )
    parser.add_argument("--gate-url", help="DATALOOP_GATE_URL bound to this alias.")
    parser.add_argument("--base", default=str(DEFAULT_BASE))
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    base = Path(args.base).expanduser().absolute()

    if args.list:
        rows = list_aliases(base)
        if not rows:
            print(f"No aliases provisioned under {base}.")
            return 0
        for alias, directory, gate_url in rows:
            print(f"{alias}\t{directory}\t{gate_url or '(no gate URL recorded)'}")
        return 0
    if not args.alias:
        parser.error("alias is required unless --list is given.")

    try:
        directory = provision(args.alias, args.gate_url, base=base)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
