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


def error_category(result):
    """Keep actionable failure classes without persisting provider error text."""
    text = ' '.join(getattr(item, 'text', '') for item in (result.content or [])).lower()
    for marker, category in (
        ('certificate verification failed', 'certificate_verification_failed'),
        ('identity changed', 'workspace_identity_changed'),
        ('deletion requires', 'delete_gates_disabled'),
        ('devspaces_enable_writes', 'writes_disabled'),
        ('devspaces_enable_delete', 'delete_disabled'),
        ('outside devspaces_allowed_namespaces', 'namespace_out_of_scope'),
        ('session cluster does not match', 'cluster_mismatch'),
        ('set devspaces_cluster_url', 'cluster_not_configured'),
        ('connector --list failed', 'authentication_or_connectivity'),
        ('timed out', 'timeout_recheck_state'),
        ('input validation', 'invalid_arguments'),
    ):
        if marker in text:
            return category
    return 'tool_failed'


async def serve(folder):
    folder.mkdir(parents=True, exist_ok=False)
    try:
        await run_session(folder)
    except Exception:
        write_json(folder / 'status.json', {'state': 'failed', 'pid': os.getpid(),
                                          'error': 'mcp_initialization_or_transport_failed'})
        raise RuntimeError('MCP session initialization or transport failed; inspect configured runtime and tool access.') from None


async def run_session(folder):
    params = StdioServerParameters(command=sys.executable,
                                  args=[str(Path(__file__).with_name('connector_mcp_server.py'))],
                                  env=os.environ.copy())
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            names = {tool.name for tool in (await asyncio.wait_for(session.list_tools(), timeout=30)).tools}
            write_json(folder / 'status.json', {'state': 'ready', 'pid': os.getpid(), 'tools': sorted(names)})
            try:
                while True:
                    for request in sorted(folder.glob('request-*.json')):
                        if not re.fullmatch(r'request-[a-f0-9-]{1,64}\.json', request.name):
                            continue
                        response = request.with_name(request.name.replace('request-', 'response-', 1))
                        try:
                            if response.exists():
                                write_json(response, {'is_error': True, 'result': None, 'error': 'duplicate_request_id'})
                                continue
                            call = json.loads(request.read_text(encoding='utf-8-sig'))
                            if call.get('tool') == 'close':
                                return
                            if call.get('tool') not in names:
                                raise ValueError('Unknown tool')
                            write_json(folder / 'status.json', {'state': 'busy', 'pid': os.getpid(), 'active_tool': call['tool']})
                            result = await asyncio.wait_for(session.call_tool(call['tool'], call.get('arguments', {})), timeout=900)
                            # Plugin tools provide sanitized structured results. Never log raw
                            # terminal output, client exceptions, environment, or request contents.
                            answer = {'is_error': bool(result.isError),
                                      'result': result.structuredContent if not result.isError else None}
                            if result.isError:
                                answer['error'] = error_category(result)
                            write_json(response, answer)
                        except asyncio.TimeoutError:
                            write_json(response, {'is_error': True, 'result': None, 'error': 'timeout_recheck_state'})
                        except Exception:
                            write_json(response, {'is_error': True, 'result': None, 'error': 'request_failed'})
                        finally:
                            request.unlink(missing_ok=True)
                            write_json(folder / 'status.json', {'state': 'ready', 'pid': os.getpid(), 'tools': sorted(names)})
                    await asyncio.sleep(0.5)
            finally:
                write_json(folder / 'status.json', {'state': 'closed', 'pid': os.getpid()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-dir', required=True, type=Path)
    args = parser.parse_args()
    try:
        asyncio.run(serve(args.session_dir.resolve()))
    except (OSError, RuntimeError):
        print('Devspaces retained client failed; read sanitized status.json and inspect the configured runtime.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
