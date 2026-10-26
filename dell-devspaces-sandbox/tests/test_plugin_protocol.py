# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Real MCP initialize/discovery/calls/output-schema tests with synthetic backend."""
import asyncio
import json
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

import jsonschema
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FIXTURE = Path(__file__).parent / 'fixtures' / 'protocol_server.py'


async def session_run(scenario):
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path),
           'DEVSPACES_TEST_SCENARIO': scenario,
           'DEVSPACES_CLUSTER_URL': 'https://devspaces.example.com',
           'DEVSPACES_ALLOWED_NAMESPACES': 'review-team',
           'DEVSPACES_ENABLE_WRITES': 'false' if scenario == 'gated' else 'true',
           'DEVSPACES_ENABLE_DELETE': 'true'}
    params = StdioServerParameters(command=sys.executable, args=[str(FIXTURE)], env=env)
    transcript = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as session:
            init = await session.initialize()
            assert init.serverInfo.name == 'Dell Redhat Devspaces AI Factory Skill'
            tools = (await session.list_tools()).tools
            schemas = {tool.name: tool.outputSchema for tool in tools}
            assert len(tools) == 14
            for tool in tools:
                assert tool.description and tool.inputSchema and tool.outputSchema
                jsonschema.Draft202012Validator.check_schema(tool.inputSchema)
                jsonschema.Draft202012Validator.check_schema(tool.outputSchema)
                annotations = tool.annotations.model_dump()
                assert all(type(annotations[key]) is bool for key in
                           ('readOnlyHint', 'destructiveHint', 'openWorldHint', 'idempotentHint'))

            async def call(name, args=None, error=False):
                started = time.monotonic()
                result = await session.call_tool(name, args or {})
                assert bool(result.isError) is error, result
                if not error:
                    jsonschema.validate(result.structuredContent, schemas[name])
                transcript.append({'tool': name, 'arguments': args or {},
                                   'is_error': bool(result.isError),
                                   'error_text': [item.text for item in result.content if hasattr(item, 'text')] if result.isError else [],
                                   'structured_result': result.structuredContent,
                                   'latency_ms': round((time.monotonic() - started) * 1000, 2)})
                return result.structuredContent

            target = dict(namespace='review-team', name='alpha', expected_workspace_id='workspace-alpha')
            if scenario == 'wrong_cluster':
                await call('list_devspaces', error=True)
                await call('start_devspace', target, error=True)
            elif scenario == 'empty':
                result = await call('get_authentication_status')
                assert result['cluster_verified'] is False
                assert (await call('list_devspaces'))['total'] == 0
            elif scenario == 'gated':
                await call('start_devspace', target, error=True)
                await call('delete_devspace', {**target, 'confirm_name': 'alpha'}, error=True)
                assert (await call('get_devspace_status', dict(namespace='review-team', name='alpha')))['phase'] == 'Stopped'
            else:
                await call('get_runtime_configuration')
                assert (await call('get_devspaces_authentication_progress'))['state'] == 'not_started'
                assert (await call('begin_devspaces_authentication'))['state'] == 'verified'
                assert (await call('get_devspaces_authentication_instructions'))['interactive_required']
                assert (await call('get_authentication_status'))['cluster_verified']
                first = await call('list_devspaces', {'limit': 1})
                assert first['next_offset'] == 1 and first['workspaces'][0]['name'] == 'alpha'
                second = await call('list_devspaces', {'limit': 1, 'offset': first['next_offset']})
                assert second['next_offset'] is None and second['workspaces'][0]['name'] == 'beta'
                await call('list_devspaces', {'limit': 0}, error=True)
                await call('start_devspace', {**target, 'timeout': 301}, error=True)
                await call('start_devspace', {**target, 'name': '--delete'}, error=True)
                await call('start_devspace', {**target, 'expected_workspace_id': 'replaced'}, error=True)
                await call('start_devspace', {**target, 'namespace': 'another'}, error=True)
                assert (await call('start_devspace', target))['phase'] == 'Running'
                assert (await call('restart_devspace', target))['phase'] == 'Running'
                await call('connect_devspace', target)
                connection = None
                for _ in range(20):
                    connection = await call('get_connection_status', dict(namespace='review-team', name='alpha'))
                    if connection['state'] == 'ready': break
                    await asyncio.sleep(0.01)
                assert connection['state'] == 'ready'
                assert (await call('get_devspace_status', dict(namespace='review-team', name='alpha')))['connection']['state'] == 'ready'
                await call('disconnect_devspace', dict(namespace='review-team', name='alpha'))
                assert (await call('stop_devspace', target))['phase'] == 'Stopped'
                await call('delete_devspace', {**target, 'confirm_name': 'wrong'}, error=True)
                assert (await call('delete_devspace', {**target, 'confirm_name': 'alpha'}))['deletion_requested']
                assert (await call('list_devspaces', {'name': 'alpha'}))['total'] == 0
    if os.getenv('DEVSPACES_TEST_EVIDENCE_DIR'):
        output = Path(os.environ['DEVSPACES_TEST_EVIDENCE_DIR'])
        output.mkdir(parents=True, exist_ok=True)
        (output / 'tool-contract.json').write_text(json.dumps([tool.model_dump(mode='json') for tool in tools], indent=2), encoding='utf-8')
    return transcript


@pytest.mark.parametrize('scenario', ['normal', 'wrong_cluster', 'empty', 'gated'])
def test_real_stdio_contract(scenario):
    transcript = asyncio.run(session_run(scenario))
    evidence = os.getenv('DEVSPACES_TEST_EVIDENCE_DIR')
    if evidence:
        path = Path(evidence)
        path.mkdir(parents=True, exist_ok=True)
        (path / f'protocol-{scenario}.json').write_text(json.dumps({
            'evidence_type': 'deterministic MCP protocol test; synthetic backend; not agent/live evidence',
            'scenario': scenario, 'passed': True, 'calls': transcript,
        }, indent=2), encoding='utf-8')
