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
    # A failed dataset_create leaves no dataset, so the item-dependent phases must be skipped
    # rather than run against a None dataset id.
    assert not any(tool.startswith(("item_", "annotation_", "pii_")) for tool, _ in client.calls)
