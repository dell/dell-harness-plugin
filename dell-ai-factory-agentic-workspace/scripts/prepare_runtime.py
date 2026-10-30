#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Prepare a reusable isolated MCP runtime, keeping protocol stdout clean."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENT = 'mcp==1.30.0'
IMPORT_CHECK = "import importlib.metadata; import mcp.client.stdio; assert importlib.metadata.version('mcp') == '1.30.0'"


def usable(python):
    try:
        return subprocess.run([str(python), '-c', IMPORT_CHECK], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def prepare():
    if sys.version_info < (3, 11):
        raise RuntimeError('Python 3.11 or newer is required.')
    if usable(sys.executable):
        return Path(sys.executable)
    identity = hashlib.sha256((sys.executable + sys.version + REQUIREMENT).encode()).hexdigest()[:16]
    base = Path(os.getenv('DEVSPACES_RUNTIME_DIR') or
                (Path(os.getenv('LOCALAPPDATA', str(Path.home() / '.cache'))) / 'Dell' / 'Devspaces' / 'runtimes'))
    target = base / identity
    python = target / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if usable(python):
        return python
    target.parent.mkdir(parents=True, exist_ok=True)
    # Atomic directory lock coordinates simultaneous plugin starts. A stale lock is
    # reported, never deleted speculatively while another bootstrap could be active.
    lock = target.with_suffix('.lock')
    for attempt in range(60):
        try:
            lock.mkdir()
            break
        except FileExistsError:
            if usable(python):
                return python
            if attempt == 0:
                print('Devspaces: waiting for the active runtime preparation.', file=sys.stderr, flush=True)
            time.sleep(1)
    else:
        raise RuntimeError('Runtime preparation is still active or its lock remains; inspect the prior bootstrap before retrying.')
    try:
        print('Devspaces: preparing isolated Python MCP runtime.', file=sys.stderr, flush=True)
        subprocess.run([sys.executable, '-m', 'venv', str(target)], check=True,
                       stdout=sys.stderr, stderr=sys.stderr, timeout=120)
        subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', REQUIREMENT],
                       check=True, stdout=sys.stderr, stderr=sys.stderr, timeout=300)
        if not usable(python):
            raise RuntimeError('Prepared Python runtime failed the MCP stdio import check.')
        return python
    finally:
        lock.rmdir()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--client', action='store_true', help='Run the bundled retained MCP client')
    args, rest = parser.parse_known_args()
    try:
        python = prepare()
        if args.prepare_only:
            print(json.dumps({'state': 'ready', 'python': str(python)}))
            return 0
        script = 'mcp_session.py' if args.client else 'connector_mcp_server.py'
        # Windows execv detaches the replacement from some process supervisors.
        # Retain a waiting parent with inherited stdio so the client/server and
        # its owned tunnels remain part of the launched process lifetime.
        return subprocess.call([str(python), str(ROOT / 'scripts' / script), *rest])
    except (OSError, RuntimeError, subprocess.SubprocessError):
        print('Devspaces runtime preparation failed. Check Python/venv, package-network access, approved CA trust, and the runtime-cache permissions; no plugin-cache edits or policy changes were made.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
