"""Offline checks for live smoke control flow."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from tests.conftest import run

spec = importlib.util.spec_from_file_location(
    "live_smoke", Path(__file__).resolve().parents[1] / "scripts" / "live_smoke.py"
)
live_smoke = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(live_smoke)


class FakeClient:
    def __init__(self, _server):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def call_tool(self, tool, args=None):
        self.calls.append((tool, args or {}))
        if tool == "project_get":
            return SimpleNamespace(is_error=False, structured_content={"id": "proj-1"})
        if tool == "dataset_create":
            return SimpleNamespace(is_error=True, content=[SimpleNamespace(text="created then failed")])
        if tool == "dataset_list":
            return SimpleNamespace(
                is_error=False,
                structured_content={"items": [{"id": "ds-1", "name": args["name"]}]},
            )
        if tool == "dataset_delete":
            return SimpleNamespace(is_error=False)
        if tool == "marketplace_search" and args.get("name") == "definitely-not-a-real-package-xyz":
            return SimpleNamespace(is_error=False, structured_content={"items": [], "total": 0})
        if tool == "marketplace_search" and args.get("name") == "yolo":
            return SimpleNamespace(is_error=False, structured_content={"items": [{"name": "yolov8"}]})
        if tool == "marketplace_search" and args.get("templates_only"):
            return SimpleNamespace(
                is_error=False,
                structured_content={
                    "items": [{"id": "template-dpk-1", "provides_pipeline_templates": True}],
                    "total": 1,
                },
            )
        if tool == "marketplace_get_dpk":
            return SimpleNamespace(
                is_error=False,
                structured_content={"pipeline_templates": [{"name": "RAG Template"}]},
            )
        if tool == "marketplace_search":
            return SimpleNamespace(
                is_error=False,
                structured_content={"items": [{"id": "dpk-1", "provides_models": True}], "total": 1},
            )
        if tool == "trigger_list":
            return SimpleNamespace(is_error=False, structured_content={"items": [], "total": 0})
        if tool == "trigger_create":
            # Unconfirmed: the smoke run expects a refusal, not a created trigger.
            return SimpleNamespace(is_error=True, content=[SimpleNamespace(text="Refusing to create")])
        raise AssertionError(tool)


def test_live_smoke_cleans_up_partial_create_by_name(monkeypatch):
    client = FakeClient(None)
    monkeypatch.setattr(live_smoke, "Client", lambda server: client)
    monkeypatch.setattr(live_smoke, "build_server", lambda: object())
    live_smoke.failures.clear()

    run(live_smoke.run("proj-1"))

    assert ("dataset_delete", {"project_id": "proj-1", "dataset_id": "ds-1", "confirm": True}) in client.calls
    assert ("marketplace_search", {"templates_only": True, "page_size": 5}) in client.calls
    assert ("marketplace_get_dpk", {"dpk_id": "template-dpk-1"}) in client.calls
    assert any(
        tool == "dataset_list" and args["name"].startswith("live-smoke-") for tool, args in client.calls
    )
    # A failed dataset_create leaves no dataset, so the dataset-dependent phases must be skipped
    # rather than run against a None dataset id. Graph editing builds dataset nodes, so it counts.
    assert not any(tool.startswith(("item_", "annotation_", "pii_", "pipeline_")) for tool, _ in client.calls)


class GraphClient(FakeClient):
    """A client where dataset_create succeeds, so the graph-editing phase actually runs.

    Models a *well-behaved* platform: every write is immediately visible to the next read. The
    point of the assertions below is that the smoke script reads the graph back and counts it,
    rather than trusting the mutating calls' own success -- that is the check that would have
    caught the connect/disconnect retry being inert.
    """

    def __init__(self, _server):
        super().__init__(_server)
        self.nodes: list[str] = []
        self.connections: list[dict] = []

    async def call_tool(self, tool, args=None):
        args = args or {}
        if tool == "dataset_create":
            self.calls.append((tool, args))
            return SimpleNamespace(is_error=False, structured_content={"id": "ds-1"})
        if tool.startswith("pipeline_"):
            self.calls.append((tool, args))
            if tool == "pipeline_create":
                return SimpleNamespace(is_error=False, structured_content={"id": "pipe-1"})
            if tool == "pipeline_node_add":
                self.nodes.append(args["node"]["name"])
            if tool == "pipeline_connect_nodes":
                self.connections.append({"source": args["source_node_name"]})
            if tool == "pipeline_disconnect_nodes":
                self.connections.clear()
            if tool == "pipeline_get":
                return SimpleNamespace(
                    is_error=False,
                    structured_content={
                        "nodes": [{"name": n} for n in self.nodes],
                        "connections": list(self.connections),
                    },
                )
            return SimpleNamespace(is_error=False, structured_content={})
        try:
            return await super().call_tool(tool, args)
        except AssertionError:
            # Everything downstream of a successful dataset_create that this test does not care
            # about. An error result makes each of those phases take its own bail-out branch,
            # keeping this test scoped to the graph sequence.
            self.calls.append((tool, args))
            return SimpleNamespace(is_error=True, content=[SimpleNamespace(text="not stubbed")])


def test_live_smoke_reads_the_graph_back_after_rapid_edits(monkeypatch):
    """The graph phase must verify by independent read, not by trusting the mutating calls."""
    client = GraphClient(None)
    # The elicitation phase builds its own Client with an elicitation_callback, so accept kwargs.
    monkeypatch.setattr(live_smoke, "Client", lambda server, **kwargs: client)
    monkeypatch.setattr(live_smoke, "build_server", lambda: object())
    live_smoke.failures.clear()

    run(live_smoke.run("proj-1"))

    tools = [tool for tool, _ in client.calls]
    assert tools.count("pipeline_node_add") == 2
    assert "pipeline_connect_nodes" in tools
    # Adds and the connect happen with no intervening read: that unpaced sequence is the shape
    # that lost graph state live, and pacing it would defeat the purpose of the check.
    first_read = tools.index("pipeline_get")
    assert tools.index("pipeline_connect_nodes") < first_read
    assert "pipeline_node_update" in tools and "pipeline_disconnect_nodes" in tools
    assert ("pipeline_delete", {"project_id": "proj-1", "pipeline_id": "pipe-1", "confirm": True}) in (
        client.calls
    )
    # The graph checks specifically must pass: nodes survived, exactly one edge, stable afterwards.
    graph_checks = [
        "both nodes survived the rapid sequence",
        "exactly one connection, not duplicated",
        "graph is stable after update and disconnect",
    ]
    assert [name for name in graph_checks if name in live_smoke.failures] == []
