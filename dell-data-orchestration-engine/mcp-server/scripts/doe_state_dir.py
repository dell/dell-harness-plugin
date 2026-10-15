#!/usr/bin/env python
# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Provision a private `DATALOOP_PATH` state directory, one per DOE deployment.

`dtlpy`'s state directory is a single cookie file covering *every* environment it has ever been
pointed at (see README "Pointing at a non-default DOE deployment"): each gate URL keeps its own
token inside that cookie, so two deployments sharing one `DATALOOP_PATH` do not actually clobber
each other's credentials -- but they do share one file, one `.lock`, and one "currently selected"
pointer, which is needless coupling between otherwise-unrelated sessions (e.g. an interactive shell
against the cloud and an MCP client against a lab instance).

This gives every deployment its own directory instead: `<base>/dataloop-<alias>`, created on first
use. A small `.gate-url` marker is written inside it recording which gate URL the alias was
provisioned for, so reusing an alias for a *different* deployment later fails loudly instead of
silently mixing sessions in what looks like a dedicated directory.

Usage:
    # First use: also records the gate URL the alias is for.
    python scripts/doe_state_dir.py cloud --gate-url https://gate.dataloop.ai/api/v1

    # Later uses: just resolves the existing directory. Safe to call every time you source an
    # env file or generate an MCP client config -- it only ever creates, never deletes.
    python scripts/doe_state_dir.py cloud

    # From a sourced env file, so DATALOOP_PATH always resolves to the right directory:
    export DATALOOP_PATH="$(python scripts/doe_state_dir.py cloud)"

    # What aliases exist already, and which deployment each is for:
    python scripts/doe_state_dir.py --list

Only the resolved directory path goes to stdout, so `$(...)` command substitution stays clean;
everything else (warnings, the --list table) goes to stderr or is explicitly requested.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

DEFAULT_BASE = Path.home() / ".doe-mcp"
MARKER_NAME = ".gate-url"
_UNSAFE = re.compile(r"[^a-z0-9-]+")

#: Owner-only. `dtlpy` writes `cookie.json` -- which holds the bearer token -- into this directory
#: with a plain `open(path, "w")` and never chmods it, so the directory mode is the only thing
#: protecting the token at rest. 0o755 (the default under the usual umask) means every local user
#: can read it.
DIR_MODE = 0o700
MARKER_MODE = 0o600


def slugify(alias: str) -> str:
    """A filesystem- and directory-name-safe form of an alias, e.g. 'Private Lab' -> 'private-lab'."""
    slug = _UNSAFE.sub("-", alias.strip().lower()).strip("-")
    if not slug:
        raise ValueError(f"{alias!r} has no usable characters for a directory name.")
    return slug


def state_dir_for(alias: str, *, base: Path = DEFAULT_BASE) -> Path:
    """The `<base>/dataloop-<slug>` directory for `alias`, unrelated to whether it exists yet."""
    return base / f"dataloop-{slugify(alias)}"


def provision(alias: str, gate_url: str | None, *, base: Path = DEFAULT_BASE) -> Path:
    """Create (if needed) and return the state directory for `alias`.

    Raises `ValueError` if `alias` already names a directory provisioned for a *different*
    `gate_url` -- reusing an alias across deployments would silently recreate the single-shared-
    cookie coupling this script exists to avoid, just one level up.
    """
    directory = state_dir_for(alias, base=base)
    if directory.is_symlink():
        raise ValueError(
            f"{directory} is a symlink. Refusing to use it: the DOE token would be written "
            f"wherever it points, which is not visibly the directory this prints. Remove it, or "
            f"pick a different alias."
        )
    if directory.exists() and not directory.is_dir():
        raise ValueError(f"{directory} exists but is not a directory, so it cannot hold dtlpy state.")

    directory.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    # chmod as well as mkdir(mode=): mkdir's mode is masked by the umask, and ignored entirely
    # when the directory already exists -- which is the usual case, and exactly when a previously
    # world-readable directory needs tightening. Applied to `base` too, since `parents=True` gives
    # it the same default treatment.
    os.chmod(base, DIR_MODE)
    os.chmod(directory, DIR_MODE)
    marker = directory / MARKER_NAME

    recorded = marker.read_text().strip() if marker.exists() else None
    if gate_url is None:
        if recorded is None:
            print(
                f"warning: {directory} has no recorded gate URL yet (first call did not pass "
                "--gate-url). Pass --gate-url once to record which deployment this alias is for.",
                file=sys.stderr,
            )
        return directory

    normalized = gate_url.strip().rstrip("/")
    if recorded is None:
        marker.write_text(normalized + "\n")
        os.chmod(marker, MARKER_MODE)
    elif recorded != normalized:
        raise ValueError(
            f"alias {alias!r} is already provisioned for {recorded!r}, not {normalized!r}. "
            f"Pick a different alias for the new deployment -- reusing this one would put two "
            f"deployments' sessions back in the same directory, which is exactly what per-alias "
            f"directories exist to avoid."
        )
    return directory


def list_aliases(base: Path = DEFAULT_BASE) -> list[tuple[str, Path, str | None]]:
    """Every alias provisioned under `base`, its directory, and its recorded gate URL (if any)."""
    if not base.is_dir():
        return []
    rows = []
    for entry in sorted(base.iterdir()):
        # is_symlink first: is_dir() follows the link, so a symlink would otherwise be listed as
        # a normal state directory while pointing somewhere else entirely.
        if entry.is_symlink() or not entry.is_dir() or not entry.name.startswith("dataloop-"):
            continue
        marker = entry / MARKER_NAME
        gate_url = marker.read_text().strip() if marker.exists() else None
        rows.append((entry.name.removeprefix("dataloop-"), entry, gate_url))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("alias", nargs="?", help="Short name for the deployment, e.g. 'cloud', 'privatelab'.")
    parser.add_argument("--gate-url", help="DATALOOP_GATE_URL this alias is for. Recorded on first use.")
    parser.add_argument(
        "--base", default=str(DEFAULT_BASE), help=f"Parent directory. Default: {DEFAULT_BASE}"
    )
    parser.add_argument("--list", action="store_true", help="List provisioned aliases and exit.")
    args = parser.parse_args()

    # expanduser: a quoted "~/foo" from a config file would otherwise create a literal `~`
    # directory. absolute: the printed path becomes DATALOOP_PATH, which the MCP client resolves
    # against *its* cwd, not the shell's, so a relative one silently points somewhere else.
    base = Path(args.base).expanduser().absolute()

    if args.list:
        rows = list_aliases(base)
        if not rows:
            print(f"No aliases provisioned yet under {base}.")
            return 0
        width = max(len(alias) for alias, _, _ in rows)
        for alias, directory, gate_url in rows:
            print(f"{alias:<{width}}  {directory}  {gate_url or '(no gate URL recorded)'}")
        return 0

    if not args.alias:
        parser.error("alias is required unless --list is given.")

    try:
        directory = provision(args.alias, args.gate_url, base=base)
    except (ValueError, OSError) as exc:
        # OSError too: a file where the directory should be, an unreadable marker, a permission
        # failure. A traceback here would be noise in a shell that is only asking for a path.
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(directory)
    return 0


if __name__ == "__main__":
    sys.exit(main())
