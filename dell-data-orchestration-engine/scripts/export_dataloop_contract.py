#!/usr/bin/env python3
"""Print a sanitized doe-mcp tools/list snapshot for contract review."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

BUNDLED_SERVER_SRC = Path(__file__).resolve().parents[1] / "mcp-server" / "src"
sys.path.insert(0, str(BUNDLED_SERVER_SRC))

from doe_mcp.server import PROFILES, build_server


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=[*sorted(PROFILES), "all"],
        default="plugin-v1",
        help="Tool profile to export. Defaults to the plugin's reviewed surface.",
    )
    args = parser.parse_args()
    profiles = None if args.profile == "all" else [args.profile]
    tools = asyncio.run(build_server(profiles=profiles).list_tools())
    snapshot = {
        "profile": args.profile,
        "tools": [tool.model_dump(by_alias=True, exclude_none=True) for tool in tools],
    }
    print(json.dumps(snapshot, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
