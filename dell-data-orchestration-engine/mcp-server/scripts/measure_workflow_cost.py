#!/usr/bin/env python
"""Measure real per-workflow turn and token counts for the three `docs/guides/*.md` walkthroughs.

Phase 0 exit criterion (docs/plans/AGENT_OPTIMIZATION_HARNESS_SLM_PLAN.md §4): every number used
to justify a later phase must be measured, not estimated. This drives one representative
happy-path tool-call sequence per guide through the real in-memory MCP client — the same
`Client(mcp)` a host uses — with dtlpy mocked at the seams (`dl.projects.get/list`,
`dl.repositories.Dpks`, `ensure_authenticated`), the same seams `tests/conftest.py` mocks.

Each sequence is deliberately the *read-mostly, happy-path* subset of its guide: the branches,
partial approvals and error paths a real conversation might take are out of scope for a token/turn
baseline and would make the number depend on which branch was picked. See the docstring on each
`run_*` function for exactly which phases it covers and why it stops where it does.

Usage:
    python scripts/measure_workflow_cost.py [--json]
"""

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

os.environ.setdefault("DATALOOP_API_KEY", "placeholder-not-a-real-key")

import dtlpy as dl  # noqa: E402
from mcp import Client  # noqa: E402

from doe_mcp import context  # noqa: E402
from doe_mcp.server import build_server  # noqa: E402
from doe_mcp.tools import projects  # noqa: E402

CHARS_PER_TOKEN = 4


# --- fakes: the same dtlpy seams tests/conftest.py mocks, extended enough to answer a full ------
# --- happy-path conversation rather than one tool call. -----------------------------------------


def _page(items: list[Any], items_count: int | None = None, has_next_page: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        items=items,
        items_count=items_count if items_count is not None else len(items),
        has_next_page=has_next_page,
    )


def _fake_item(item_id: str, name: str, mimetype: str, annotated: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=item_id,
        name=name,
        dir="/",
        type="file",
        mimetype=mimetype,
        filename=name,
        dataset_id="ds-1",
        annotated=annotated,
        annotations_count=0,
        created_at="2026-01-01",
        metadata={"user": {}},
        platform_url=f"https://example/items/{item_id}",
    )


def _fake_dpk(name: str, *, models: bool, templates: bool) -> SimpleNamespace:
    components = SimpleNamespace(
        models=[
            {"name": f"{name}-model", "description": "d", "moduleName": "m", "status": "ok", "labels": []}
        ]
        if models
        else [],
        pipeline_templates=[
            {"name": f"{name}-template", "description": "d", "nodes": [{"name": "n", "type": "t"}]}
        ]
        if templates
        else [],
    )
    return SimpleNamespace(
        id=f"dpk-{name}",
        base_id=f"dpk-{name}",
        name=name,
        display_name=name,
        version="1.0.0",
        description="A marketplace package.",
        scope="public",
        attributes={},
        tags=[],
        creator="publisher@example.com",
        components=components,
    )


class FakeItemsRepo:
    def __init__(self, items: list[Any]):
        self._items = items

    def list(self, filters=None) -> SimpleNamespace:
        return _page(list(self._items))

    def get(self, item_id: str) -> Any:
        return next(item for item in self._items if item.id == item_id)


class FakeDataset(SimpleNamespace):
    pass


class FakeDatasetsRepo:
    def __init__(self, datasets: dict[str, FakeDataset]):
        self._datasets = datasets

    def list(self, name: str | None = None) -> list[FakeDataset]:
        return list(self._datasets.values())

    def get(self, dataset_id: str) -> FakeDataset:
        return self._datasets[dataset_id]

    def create(self, dataset_name: str, **_: Any) -> FakeDataset:
        dataset = FakeDataset(
            id=f"ds-{dataset_name}",
            name=dataset_name,
            items_count=0,
            annotations_count=0,
            readonly=False,
            index_driver="v2",
            creator="me@example.com",
            created_at="2026-01-01",
            updated_at="2026-01-01",
            url=f"https://example/ds-{dataset_name}",
            items=FakeItemsRepo([]),
        )
        self._datasets[dataset.id] = dataset
        return dataset


class FakePipelineStats(SimpleNamespace):
    pass


class FakePipelinesRepo:
    def __init__(self) -> None:
        self._pipelines: dict[str, dl.entities.Pipeline] = {}
        # Set once the owning FakeProject exists (see FakeProject.__post_init__): build_node
        # reads `pipeline.project.id`/`.name`, and pipeline_install/pipeline_stats read
        # `pipeline.project.pipelines`, so the entity must point back at the same repo.
        self.project: FakeProject | None = None

    def create(self, name: str, project_id: str) -> dl.entities.Pipeline:
        pipeline = dl.entities.Pipeline.from_json(
            _json={
                "id": f"pipe-{name}",
                "name": name,
                "projectId": project_id,
                "nodes": [],
                "connections": [],
                "startNodes": [],
                "variables": [],
            },
            client_api=dl.client_api,
            project=self.project,
            is_fetched=False,
        )
        # Keep the graph local, exactly as tests/test_rag.py's mock_pipeline does: the real
        # add-node/connect machinery runs, but update() never PATCHes a platform.
        pipeline.update = lambda: pipeline
        self._pipelines[pipeline.id] = pipeline
        return pipeline

    def update(self, pipeline: dl.entities.Pipeline) -> dl.entities.Pipeline:
        return pipeline

    def get(self, pipeline_id: str) -> dl.entities.Pipeline:
        return self._pipelines[pipeline_id]

    def list(self, filters=None) -> SimpleNamespace:
        return _page(list(self._pipelines.values()))

    def install(
        self, pipeline: dl.entities.Pipeline, resume_option: str | None = None
    ) -> dl.entities.Pipeline:
        return pipeline

    def stats(self, pipeline: dl.entities.Pipeline) -> FakePipelineStats:
        return FakePipelineStats(
            pipeline_counters=[SimpleNamespace(status="success", count=0)],
            pipeline_averages=SimpleNamespace(avg_time_per_execution=0.0, avg_execution_per_day=0.0),
            node_counters=[],
        )


class FakeAppsRepo:
    def list(self, filters=None) -> SimpleNamespace:
        return _page([])


class FakeModelsRepo:
    def list(self, filters=None) -> SimpleNamespace:
        return _page([])


class FakeDpksRepo:
    def __init__(self, dpks: list[Any]):
        self._dpks = dpks

    def list(self, filters=None) -> SimpleNamespace:
        return _page(list(self._dpks))

    def get(self, dpk_id=None, dpk_name=None, dpk_version=None) -> Any:
        return next(dpk for dpk in self._dpks if dpk.id == dpk_id or dpk.name == dpk_name)


@dataclass
class FakeProject:
    id: str
    name: str
    datasets: FakeDatasetsRepo
    pipelines: FakePipelinesRepo = field(default_factory=FakePipelinesRepo)
    apps: FakeAppsRepo = field(default_factory=FakeAppsRepo)
    models: FakeModelsRepo = field(default_factory=FakeModelsRepo)
    created_at: str = "2026-01-01"
    creator: str = "me@example.com"
    role: str = "owner"
    archived: bool = False

    def __post_init__(self) -> None:
        self.pipelines.project = self


def build_fixture() -> tuple[FakeProject, FakeDpksRepo]:
    """One project with a document-style dataset, plus a marketplace with a template and a model."""
    items = [
        _fake_item("item-1", "invoice-001.pdf", "application/pdf"),
        _fake_item("item-2", "invoice-002.pdf", "application/pdf"),
        _fake_item("item-3", "readme.txt", "text/plain", annotated=True),
    ]
    datasets = {
        "ds-1": FakeDataset(
            id="ds-1",
            name="documents",
            items_count=len(items),
            annotations_count=1,
            readonly=False,
            index_driver="v2",
            creator="me@example.com",
            created_at="2026-01-01",
            updated_at="2026-01-01",
            url="https://example/ds-1",
            items=FakeItemsRepo(items),
        ),
        "ds-2": FakeDataset(
            id="ds-2",
            name="videos",
            items_count=0,
            annotations_count=0,
            readonly=False,
            index_driver="v2",
            creator="me@example.com",
            created_at="2026-01-01",
            updated_at="2026-01-01",
            url="https://example/ds-2",
            items=FakeItemsRepo([]),
        ),
    }
    project = FakeProject(id="proj-1", name="demo-project", datasets=FakeDatasetsRepo(datasets))
    dpks = FakeDpksRepo(
        [
            _fake_dpk("presidio-pii", models=False, templates=True),
            _fake_dpk("rag-preprocessing", models=False, templates=True),
            _fake_dpk("clip-embedder", models=True, templates=False),
        ]
    )
    return project, dpks


class WorkflowHarness:
    """Patches the same seams tests/conftest.py mocks, plus the module-level `dl.projects`
    repository that `project_list`/`project_get` and marketplace browsing go through."""

    def __init__(self) -> None:
        self.project, self.dpks = build_fixture()

    def __enter__(self) -> "WorkflowHarness":
        self._patches: list[Any] = [
            patch.object(context, "ensure_authenticated", lambda: None),
            # projects.py imports ensure_authenticated directly rather than through context.*,
            # so it needs its own patch target too.
            patch.object(projects, "ensure_authenticated", lambda: None),
            patch.object(dl.projects, "get", lambda project_id=None, **_: self.project),
            patch.object(dl.projects, "list", lambda: [self.project]),
            patch.object(dl.repositories, "Dpks", lambda **_: self.dpks),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        for p in reversed(self._patches):
            p.stop()


# --- turn/token measurement -----------------------------------------------------------------


@dataclass
class WorkflowResult:
    name: str
    turns: int
    request_chars: int
    response_chars: int

    @property
    def total_chars(self) -> int:
        return self.request_chars + self.response_chars

    @property
    def total_tokens_approx(self) -> int:
        return self.total_chars // CHARS_PER_TOKEN


async def _drive(name: str, calls: list[tuple[str, dict[str, Any]]]) -> WorkflowResult:
    request_chars = 0
    response_chars = 0
    async with Client(build_server()) as client:
        for tool_name, arguments in calls:
            request_chars += len(
                json.dumps({"tool": tool_name, "arguments": arguments}, separators=(",", ":"))
            )
            result = await client.call_tool(tool_name, arguments)
            if result.is_error:
                raise RuntimeError(f"{name}: {tool_name} failed: {result.content}")
            payload = result.structured_content if result.structured_content is not None else {}
            response_chars += len(json.dumps(payload, separators=(",", ":"), default=str))
    return WorkflowResult(
        name=name, turns=len(calls), request_chars=request_chars, response_chars=response_chars
    )


def run_dataset_exploration() -> WorkflowResult:
    """docs/guides/dataset-exploration-workflow.md, Phases 1-3.

    Stops at the executive summary, which the guide itself calls the point "where the workflow
    should stop" for most datasets — Phase 4 (model inference) is optional, billable, and mutates
    the dataset, so it is not part of a baseline "explore my data" turn count.
    """
    with WorkflowHarness():
        calls: list[tuple[str, dict[str, Any]]] = [
            ("project_list", {}),
            ("dataset_list", {"project_id": "proj-1"}),
            ("dataset_get", {"project_id": "proj-1", "dataset_id": "ds-1"}),
            ("dataset_profile", {"project_id": "proj-1", "dataset_id": "ds-1"}),
        ]
        return asyncio.run(_drive("dataset-exploration", calls))


def run_pii_detection() -> WorkflowResult:
    """docs/guides/pii-detection-workflow.md, Phases 0, 1 and 4.

    Covers the read-only planning grounding (0.3), establishing the marker convention (Phase 1),
    and answering the PII question (Phase 4). Phases 2/3/5/6/7 create and run a pipeline, which
    needs the platform's template validator (`pipelines.validate`) — out of scope for an offline
    token/turn baseline, so this sequence measures the part every session goes through regardless
    of which downstream action is chosen.
    """
    with WorkflowHarness():
        calls: list[tuple[str, dict[str, Any]]] = [
            ("project_list", {}),
            ("dataset_list", {"project_id": "proj-1"}),
            ("pipeline_list", {"project_id": "proj-1"}),
            ("marketplace_search", {"name": "presidio", "templates_only": True}),
            ("item_list", {"project_id": "proj-1", "dataset_id": "ds-1"}),
            ("item_get", {"project_id": "proj-1", "dataset_id": "ds-1", "item_id": "item-1"}),
            (
                "pii_report",
                {
                    "project_id": "proj-1",
                    "dataset_id": "ds-1",
                    "marker": {"entity_types": ["US_SSN", "EMAIL_ADDRESS"]},
                },
            ),
        ]
        return asyncio.run(_drive("pii-detection", calls))


def run_rag_pipeline() -> WorkflowResult:
    """docs/guides/agentic-rag-workflow.md, Phases 0, 1 and 3.5 (direct RAG tools, the
    recommended path per the guide's own Phase 3.5 heading).

    Skips the marketplace-template fallback (Phase 4) and hand-built pipeline (Phase 5): the
    guide recommends the direct `rag_*` tools "for most use cases", so that is the representative
    happy path, not the fallback.
    """
    with WorkflowHarness():
        calls: list[tuple[str, dict[str, Any]]] = [
            ("project_list", {}),
            ("dataset_list", {"project_id": "proj-1"}),
            ("marketplace_search", {"templates_only": True}),
            ("app_list", {"project_id": "proj-1"}),
            ("model_list", {"project_id": "proj-1"}),
            ("dataset_get", {"project_id": "proj-1", "dataset_id": "ds-1"}),
            (
                "rag_pipeline_create",
                {
                    "project_id": "proj-1",
                    "name": "rag-pipeline",
                    "source_dataset_id": "ds-1",
                    "output_dataset_id": "ds-2",
                },
            ),
            (
                "rag_add_pdf_filter_node",
                {"project_id": "proj-1", "pipeline_id": "pipe-rag-pipeline", "source_dataset_id": "ds-1"},
            ),
            (
                "rag_add_pdf_to_text_node",
                {"project_id": "proj-1", "pipeline_id": "pipe-rag-pipeline", "output_dataset_id": "ds-2"},
            ),
            (
                "rag_add_text_to_chunks_node",
                {"project_id": "proj-1", "pipeline_id": "pipe-rag-pipeline", "output_dataset_id": "ds-2"},
            ),
            (
                "rag_add_embedding_node",
                {
                    "project_id": "proj-1",
                    "pipeline_id": "pipe-rag-pipeline",
                    "output_dataset_id": "ds-2",
                    "model_id": "model-1",
                },
            ),
            (
                "rag_add_output_node",
                {"project_id": "proj-1", "pipeline_id": "pipe-rag-pipeline", "output_dataset_id": "ds-2"},
            ),
            (
                "pipeline_connect_nodes",
                {
                    "project_id": "proj-1",
                    "pipeline_id": "pipe-rag-pipeline",
                    "source_node_name": "pdf_filter",
                    "target_node_name": "pdf_to_text",
                },
            ),
            (
                "pipeline_connect_nodes",
                {
                    "project_id": "proj-1",
                    "pipeline_id": "pipe-rag-pipeline",
                    "source_node_name": "pdf_to_text",
                    "target_node_name": "text_to_chunks",
                },
            ),
            (
                "pipeline_connect_nodes",
                {
                    "project_id": "proj-1",
                    "pipeline_id": "pipe-rag-pipeline",
                    "source_node_name": "text_to_chunks",
                    "target_node_name": "generate_embeddings",
                },
            ),
            (
                "pipeline_connect_nodes",
                {
                    "project_id": "proj-1",
                    "pipeline_id": "pipe-rag-pipeline",
                    "source_node_name": "generate_embeddings",
                    "target_node_name": "output_storage",
                },
            ),
            ("pipeline_install", {"project_id": "proj-1", "pipeline_id": "pipe-rag-pipeline"}),
            ("pipeline_stats", {"project_id": "proj-1", "pipeline_id": "pipe-rag-pipeline"}),
        ]
        return asyncio.run(_drive("agentic-rag", calls))


WORKFLOWS = {
    "dataset-exploration": run_dataset_exploration,
    "pii-detection": run_pii_detection,
    "agentic-rag": run_rag_pipeline,
}


def measure_all() -> dict[str, WorkflowResult]:
    return {name: runner() for name, runner in WORKFLOWS.items()}


def print_report(results: dict[str, WorkflowResult]) -> None:
    for result in results.values():
        print(
            f"{result.name:20s} {result.turns:3d} turns  {result.total_chars:6d} chars  "
            f"~{result.total_tokens_approx:5d} tok  "
            f"(request {result.request_chars} / response {result.response_chars})"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON instead")
    args = parser.parse_args()

    results = measure_all()
    if args.json:
        print(json.dumps({name: vars(r) for name, r in results.items()}, indent=2))
    else:
        print_report(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
