"""Bulk payloads exposed as MCP Resources (plan §4 Phase 2), so large JSON can live outside the
conversation transcript instead of round-tripping through a tool's structured_content.
"""

import asyncio
import json
from types import SimpleNamespace

from mcp import Client

from doe_mcp.server import build_server
from doe_mcp.tools import datasets, pipeline_nodes


def read_resource(uri):
    async def run():
        async with Client(build_server()) as client:
            return await client.read_resource(uri)

    return run_sync(run())


def run_sync(coro):
    return asyncio.run(coro)


def test_dataset_resource_returns_the_full_dataset_detail(monkeypatch):
    fake = SimpleNamespace(
        id="ds-1",
        name="images",
        items_count=3,
        annotations_count=1,
        readonly=False,
        index_driver="v2",
        creator="me@example.com",
        created_at="2026-01-01",
        updated_at="2026-01-02",
        url="https://example/ds-1",
    )
    monkeypatch.setattr(datasets, "get_dataset", lambda project_id, dataset_id: fake)

    result = read_resource("dataloop://projects/proj-1/datasets/ds-1")

    body = json.loads(result.contents[0].text)
    assert body["id"] == "ds-1"
    assert body["items_count"] == 3


def test_pipeline_graph_resource_returns_the_full_graph(monkeypatch):
    fake_pipeline = SimpleNamespace(
        id="pipe-1",
        name="rag",
        status="installed",
        project_id="proj-1",
        description=None,
        created_at=None,
        updated_at=None,
        url=None,
        nodes=[],
        connections=[],
        start_nodes=[],
    )
    monkeypatch.setattr(pipeline_nodes, "get_pipeline", lambda project_id, pipeline_id: fake_pipeline)

    result = read_resource("dataloop://projects/proj-1/pipelines/pipe-1/graph")

    body = json.loads(result.contents[0].text)
    assert body["id"] == "pipe-1"
    assert body["nodes"] == []
    assert body["connections"] == []
