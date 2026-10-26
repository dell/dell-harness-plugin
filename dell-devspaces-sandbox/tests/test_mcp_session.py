# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import asyncio
from contextlib import asynccontextmanager
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

PATH = Path(__file__).parents[1] / 'scripts/mcp_session.py'
SPEC = importlib.util.spec_from_file_location('retained_client', PATH)
CLIENT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLIENT)


def test_retained_client_requests_results_and_shutdown(monkeypatch, tmp_path):
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
        CLIENT.write_json(folder / 'request-3.json', {'tool': 'close'})
        await asyncio.wait_for(task, 3)
        assert json.loads((folder / 'status.json').read_text())['state'] == 'closed'
    asyncio.run(scenario())
