# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The RAG skill: the five-stage chain built in one deterministic pass.

The claim being tested is a port, not a rewrite: `rag_pipeline_build` must produce exactly the
graph the six `rag_add_*_node` tools produce one at a time. If those ever diverge, the skill is a
second, subtly different RAG shape rather than a cheaper route to the same one.
"""

import re
from types import SimpleNamespace

import dtlpy as dl
import pytest

from doe_mcp import blueprint as bp
from doe_mcp.tools import rag, skills
from tests.test_pipeline_nodes import build_pipeline, owning_project

CHOICES = dict(
    name="rag",
    source_dataset_id="ds-src",
    output_dataset_id="ds-out",
    model_id="model-1",
    chunk_size=512,
    chunk_overlap=50,
    load_existing=False,
)


def build(**overrides):
    pipeline = build_pipeline(project=SimpleNamespace(id="proj-1", name="proj"))
    bp.build_graph(pipeline, skills.rag_blueprint(**{**CHOICES, **overrides}))
    return pipeline


# --- determinism ---------------------------------------------------------------------------


def test_the_same_choices_build_the_same_five_node_graph():
    assert build().to_json() == build().to_json()


def test_every_user_choice_changes_the_graph():
    """Determinism must not mean insensitivity: each gated choice has to reach the output.

    A blueprint that ignored an argument would be perfectly deterministic and completely wrong,
    and the fingerprint would hide it -- so this asserts on the built graph, not the fingerprint.
    """
    baseline = build().to_json()

    for field, value in (
        ("source_dataset_id", "ds-other"),
        ("output_dataset_id", "ds-elsewhere"),
        ("model_id", "model-2"),
        ("chunk_size", 1024),
        ("chunk_overlap", 0),
        ("load_existing", True),
    ):
        assert build(**{field: value}).to_json() != baseline, f"{field} does not affect the graph"


def test_the_chain_is_wired_end_to_end():
    graph = build().to_json()
    by_id = {node["id"]: node["name"] for node in graph["nodes"]}

    wired = [(by_id[c["src"]["nodeId"]], by_id[c["tgt"]["nodeId"]]) for c in graph["connections"]]

    assert wired == [
        ("pdf_filter", "pdf_to_text"),
        ("pdf_to_text", "text_to_chunks"),
        ("text_to_chunks", "generate_embeddings"),
        ("generate_embeddings", "output_storage"),
    ]
    assert len(graph["nodes"]) == 5


# --- it is a port of the existing tools, not a new shape -------------------------------------


def test_the_skill_builds_what_the_six_rag_tools_build(monkeypatch, call_tool):
    """Drives the real rag_add_*_node tools against one pipeline and compares canonical form."""
    incremental = build_pipeline(project=owning_project())
    monkeypatch.setattr(
        "doe_mcp.tools.pipeline_nodes.get_pipeline", lambda project_id, pipeline_id: incremental
    )
    monkeypatch.setattr("doe_mcp.tools.pipeline_nodes.time.sleep", lambda _s: None)
    args = {"project_id": "proj-1", "pipeline_id": "pipe-1"}

    steps = [
        ("rag_add_pdf_filter_node", {"source_dataset_id": CHOICES["source_dataset_id"]}),
        ("rag_add_pdf_to_text_node", {"output_dataset_id": CHOICES["output_dataset_id"]}),
        (
            "rag_add_text_to_chunks_node",
            {
                "output_dataset_id": CHOICES["output_dataset_id"],
                "chunk_size": CHOICES["chunk_size"],
                "chunk_overlap": CHOICES["chunk_overlap"],
            },
        ),
        (
            "rag_add_embedding_node",
            {"output_dataset_id": CHOICES["output_dataset_id"], "model_id": CHOICES["model_id"]},
        ),
        ("rag_add_output_node", {"output_dataset_id": CHOICES["output_dataset_id"]}),
    ]
    for tool, extra in steps:
        result = call_tool(tool, {**args, **extra})
        assert result.is_error is False, result.content[0].text

    names = [n.name for n in incremental.nodes]
    for source, target in zip(names, names[1:], strict=False):
        connected = call_tool(
            "pipeline_connect_nodes",
            {**args, "source_node_name": source, "target_node_name": target},
        )
        assert connected.is_error is False

    assert bp.canonical_graph(incremental) == bp.canonical_graph(build())


def deployed_code() -> str:
    """Every code node's deployed source. `config` is None on dataset nodes, hence the guard."""
    return "".join((node.config or {}).get("package", {}).get("code", "") for node in build().nodes)


@pytest.mark.parametrize("template_name", ["PDF_TO_TEXT_CODE", "TEXT_TO_CHUNKS_CODE", "EMBEDDING_CODE"])
def test_the_skill_deploys_the_same_code_templates(template_name):
    """The skill must not carry its own copy of the runner code that could drift from rag.py's."""
    marker = getattr(rag, template_name).split("\n")[1].strip()

    assert marker in deployed_code()


def test_no_placeholder_survives_into_the_deployed_code():
    """Every all-caps template placeholder must be substituted before deployment."""
    deployed = deployed_code()

    assert re.findall(r"__[A-Z][A-Z0-9_]*__", deployed) == []


# --- one write ---------------------------------------------------------------------------


def test_the_whole_chain_is_written_in_one_patch():
    """Eleven whole-graph PATCHes through the per-node path; one here.

    Each PATCH sends the entire node list (techspec A.4), so each is a lost-update window -- and a
    lost update means the same choices produced a different graph.
    """
    pipeline = build_pipeline(project=SimpleNamespace(id="proj-1", name="proj"))
    writes = []
    pipeline.update = lambda: writes.append(1) or pipeline

    bp.build_graph(pipeline, skills.rag_blueprint(**CHOICES), write=True)

    assert len(writes) == 1


# --- binding condition: reuse a matching pipeline instead of building a duplicate ------------


class FakePipelinesRepo:
    """Stands in for `project.pipelines`: a name lookup plus a create, both recorded."""

    def __init__(self, existing=None):
        self.existing = existing
        self.created: dict | None = None

    def get(self, pipeline_name=None, pipeline_id=None):
        if self.existing is not None and pipeline_name == self.existing.name:
            return self.existing
        raise dl.exceptions.NotFound(status_code="404", message=f"Pipeline not found: {pipeline_name}")

    def create(self, name=None, project_id=None):
        self.created = {"name": name, "project_id": project_id}
        return SimpleNamespace(id="new-pipe", name=name)


def test_apply_blueprint_reuses_a_pipeline_whose_graph_already_matches(monkeypatch):
    """The binding condition: an existing, correct pipeline must be adopted, not duplicated."""
    plan = skills.rag_blueprint(**CHOICES)
    existing = build()
    existing.id, existing.name = "pipe-1", plan.name
    repo = FakePipelinesRepo(existing=existing)
    project = SimpleNamespace(id="proj-1", name="proj", pipelines=repo)
    monkeypatch.setattr(skills, "get_project", lambda project_id: project)
    monkeypatch.setattr(skills, "get_pipeline", lambda project_id, pipeline_id: existing)

    result = skills.apply_blueprint("proj-1", plan)

    assert result["reused_existing_pipeline"] is True
    assert result["pipeline"]["id"] == "pipe-1"
    assert repo.created is None, "a matching pipeline exists; nothing should have been created"


def test_apply_blueprint_does_not_reuse_a_same_named_pipeline_with_a_different_graph(monkeypatch):
    """Existence of the name is not enough: a stale same-named pipeline must not be adopted."""
    plan = skills.rag_blueprint(**CHOICES)
    stale = build(output_dataset_id="ds-somewhere-else")
    stale.id, stale.name = "pipe-1", plan.name
    repo = FakePipelinesRepo(existing=stale)
    project = SimpleNamespace(id="proj-1", name="proj", pipelines=repo)
    monkeypatch.setattr(skills, "get_project", lambda project_id: project)
    monkeypatch.setattr(skills, "get_pipeline", lambda project_id, pipeline_id: stale)

    result = skills.apply_blueprint("proj-1", plan)

    assert result["reused_existing_pipeline"] is False
    assert repo.created == {"name": plan.name, "project_id": "proj-1"}


def test_apply_blueprint_builds_from_scratch_when_no_pipeline_exists_yet(monkeypatch):
    """No same-named pipeline at all: nothing to compare against, so a fresh build proceeds.

    The first `get_pipeline` call stands in for fetching the just-created, empty pipeline to
    build the graph into; the second, for the independent re-read `apply_blueprint` verifies
    against -- modelled here as a fully built pipeline, standing in for what the platform holds
    once the PATCH lands.
    """
    plan = skills.rag_blueprint(**CHOICES)
    repo = FakePipelinesRepo(existing=None)
    project = SimpleNamespace(id="proj-1", name="proj", pipelines=repo)
    monkeypatch.setattr(skills, "get_project", lambda project_id: project)

    empty = build_pipeline(project=SimpleNamespace(id="proj-1", name="proj"))
    reads = iter([empty, build()])
    monkeypatch.setattr(skills, "get_pipeline", lambda project_id, pipeline_id: next(reads))

    result = skills.apply_blueprint("proj-1", plan)

    assert result["reused_existing_pipeline"] is False
    assert repo.created == {"name": plan.name, "project_id": "proj-1"}


# --- registration ---------------------------------------------------------------------------


def test_the_skill_is_reachable_only_through_its_profile():
    """It must not join the default surface: a skill replaces the node tools, it does not add to them."""
    import asyncio

    from doe_mcp.server import build_server

    def names(profiles=None):
        return {tool.name for tool in asyncio.run(build_server(profiles=profiles).list_tools())}

    assert "rag_pipeline_build" in names(["skills"])
    assert "rag_pipeline_build" not in names()


def test_a_profile_only_module_is_not_silently_dropped():
    """Regression: resolve_modules filtered by ALL_MODULES, so a profile-only module vanished.

    The profile resolved, the server started, and the tool simply was not registered -- no error
    anywhere. Found while measuring the skills profile.
    """
    from doe_mcp.server import ALL_MODULES, PROFILES, resolve_modules

    for profile, modules in PROFILES.items():
        resolved = resolve_modules([profile])
        for module in modules:
            assert module in resolved, f"profile {profile!r} silently dropped {module.__name__}"
        if any(module not in ALL_MODULES for module in modules):
            assert len(resolved) == len(set(resolved))
