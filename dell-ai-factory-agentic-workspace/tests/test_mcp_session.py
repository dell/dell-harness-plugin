# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import asyncio
from contextlib import asynccontextmanager
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import secrets

import pytest

PATH = Path(__file__).parents[1] / 'scripts/mcp_session.py'
SPEC = importlib.util.spec_from_file_location('retained_client', PATH)
CLIENT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLIENT)


def test_retained_client_requests_results_and_shutdown(monkeypatch, tmp_path):
    calls = []
    @asynccontextmanager
    async def stdio(params):
        yield None, None
    class Session:
        def __init__(self, *args): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def initialize(self): pass
        async def list_tools(self): return SimpleNamespace(tools=[SimpleNamespace(name='list_devspaces')])
        async def call_tool(self, name, arguments):
            calls.append(name)
            return SimpleNamespace(isError=False, structuredContent={'total': 1})
    monkeypatch.setattr(CLIENT, 'stdio_client', stdio)
    monkeypatch.setattr(CLIENT, 'ClientSession', Session)
    async def scenario():
        folder = tmp_path / 'session'
        task = asyncio.create_task(CLIENT.serve(folder))
        for _ in range(100):
            if (folder / 'status.json').exists(): break
            await asyncio.sleep(0.01)
        assert json.loads((folder / 'status.json').read_text())['state'] == 'ready'
        CLIENT.write_json(folder / 'request-1.json', {'tool': 'list_devspaces'})
        for _ in range(100):
            if (folder / 'response-1.json').exists(): break
            await asyncio.sleep(0.01)
        assert json.loads((folder / 'response-1.json').read_text()) == {'is_error': False, 'result': {'total': 1}}
        CLIENT.write_json(folder / 'request-2.json', {'tool': 'unknown'})
        for _ in range(100):
            if (folder / 'response-2.json').exists(): break
            await asyncio.sleep(0.01)
        assert json.loads((folder / 'response-2.json').read_text())['is_error']
        CLIENT.write_json(folder / 'request-1.json', {'tool': 'list_devspaces'})
        for _ in range(100):
            if not (folder / 'request-1.json').exists(): break
            await asyncio.sleep(0.01)
        assert json.loads((folder / 'response-1.json').read_text())['error'] == 'duplicate_request_id'
        assert calls == ['list_devspaces']
        CLIENT.write_json(folder / 'request-3.json', {'tool': 'close'})
        await asyncio.wait_for(task, 3)
        assert json.loads((folder / 'status.json').read_text())['state'] == 'closed'
    asyncio.run(scenario())


@pytest.mark.parametrize('message,category', [('Set DEVSPACES_ENABLE_WRITES=true', 'writes_disabled'),
                                            ('Deletion requires DEVSPACES_ENABLE_WRITES=true and DEVSPACES_ENABLE_DELETE=true', 'delete_gates_disabled'),
                                            ('Connector certificate verification failed', 'certificate_verification_failed'),
                                            ('Start the workspace and verify Running before connecting', 'workspace_not_running'),
                                            ('Workspace identity changed', 'workspace_identity_changed')])
def test_tool_failure_category_never_serializes_raw_error(message, category):
    secret = secrets.token_urlsafe(12)
    result = SimpleNamespace(content=[SimpleNamespace(text=message + secret)])
    assert CLIENT.error_category(result) == category
    assert secret not in CLIENT.error_category(result)


def test_transport_startup_failure_is_observable_without_raw_exception(monkeypatch, tmp_path):
    secret = secrets.token_urlsafe(12)
    @asynccontextmanager
    async def failing_stdio(params):
        raise RuntimeError(secret)
        yield None, None
    monkeypatch.setattr(CLIENT, 'stdio_client', failing_stdio)
    folder = tmp_path / 'session'
    with pytest.raises(RuntimeError) as exc:
        asyncio.run(CLIENT.serve(folder))
    status = json.loads((folder / 'status.json').read_text())
    assert status['state'] == 'failed'
    assert secret not in json.dumps(status) + str(exc.value)
