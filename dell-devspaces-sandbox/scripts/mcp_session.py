#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Retain one MCP connection for agent-controlled JSON requests and owned tunnels."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    temporary.replace(path)


async def serve(folder):
    folder.mkdir(parents=True, exist_ok=False)
    params = StdioServerParameters(command=sys.executable,
                                  args=[str(Path(__file__).with_name('connector_mcp_server.py'))],
                                  env=os.environ.copy())
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            write_json(folder / 'status.json', {'state': 'ready', 'pid': os.getpid(), 'tools': sorted(names)})
            try:
                while True:
                    for request in sorted(folder.glob('request-*.json')):
                        if not re.fullmatch(r'request-[a-f0-9-]{1,64}\.json', request.name):
                            continue
                        response = request.with_name(request.name.replace('request-', 'response-', 1))
                        try:
                            call = json.loads(request.read_text(encoding='utf-8-sig'))
                            if call.get('tool') == 'close':
                                return
                            if call.get('tool') not in names:
                                raise ValueError('Unknown tool')
                            result = await session.call_tool(call['tool'], call.get('arguments', {}))
                            # Plugin tools provide sanitized structured results. Never log raw
                            # terminal output, client exceptions, environment, or request contents.
                            write_json(response, {'is_error': bool(result.isError),
                                                  'result': result.structuredContent if not result.isError else None})
                        except Exception:
                            write_json(response, {'is_error': True, 'result': None})
                        finally:
                            request.unlink(missing_ok=True)
                    await asyncio.sleep(0.5)
            finally:
                write_json(folder / 'status.json', {'state': 'closed', 'pid': os.getpid()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-dir', required=True, type=Path)
    args = parser.parse_args()
    asyncio.run(serve(args.session_dir.resolve()))


if __name__ == '__main__':
    main()
