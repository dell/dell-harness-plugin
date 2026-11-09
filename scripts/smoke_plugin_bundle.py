#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Extract a bundled plugin ZIP and complete an MCP handshake with its server, offline.

Starts the server exactly as root ``mcp.json`` declares it (bundled ``./runtime`` interpreter,
``${PLUGIN_ROOT}`` expanded, plugin root as cwd) with a minimal environment, so a dependency
missing from the bundle fails here rather than on a user's desktop. Requires the ``mcp`` package
in the interpreter running this script.

    python scripts/smoke_plugin_bundle.py dist/dell-data-orchestration-engine-0.6.0-linux_x86_64.zip
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import time
import zipfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def extract(archive: Path, destination: Path) -> Path:
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            path = Path(bundle.extract(info, destination))
            mode = (info.external_attr >> 16) & 0o777
            if mode:
                path.chmod(mode)
        (top,) = {Path(name).parts[0] for name in bundle.namelist()}
    return destination / top


async def handshake(root: Path, home: Path) -> dict:
    (name, server), = json.loads((root / "mcp.json").read_text(encoding="utf-8"))["mcpServers"].items()
    assert server["command"].startswith("./runtime/"), "bundled mcp.json must launch the bundled runtime"
    assert (root / "BUNDLE.json").is_file()
    expand = lambda value: value.replace("${PLUGIN_ROOT}", str(root))  # noqa: E731
    env = {"HOME": str(home), "USERPROFILE": str(home), "PATH": os.defpath, "PLUGIN_ROOT": str(root),
           **{key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ},
           **{key: expand(value) for key, value in server.get("env", {}).items()}}
    params = StdioServerParameters(
        command=str(root / server["command"][2:]), args=[expand(arg) for arg in server["args"]],
        cwd=str(root), env=env,
    )
    started = time.monotonic()
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
        await asyncio.wait_for(session.initialize(), 120)
        tools = (await session.list_tools()).tools
    return {"server": name, "tools": len(tools), "startup_seconds": round(time.monotonic() - started, 1)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("archives", nargs="+", type=Path)
    args = parser.parse_args()
    for archive in args.archives:
        with tempfile.TemporaryDirectory(prefix="bundle-smoke-") as scratch:
            work = Path(scratch)
            home = work / "home"
            (home / ".config" / "dell-mcp").mkdir(parents=True)
            # The DOE launcher needs settings to start; a placeholder key is never sent anywhere
            # because no tool is called.
            config = home / ".config" / "dell-mcp" / "dataloop.json"
            config.write_text(json.dumps({"api_key": "placeholder", "state_path": str(home / "state")}))
            config.chmod(0o600)
            root = extract(archive, work / "plugin")
            before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
            result = asyncio.run(handshake(root, home))
            after = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
            result["plugin_files_written"] = sorted(set(after) - set(before))
            print(json.dumps({"archive": archive.name, **result}), flush=True)
            if not result["tools"] or result["plugin_files_written"]:
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
