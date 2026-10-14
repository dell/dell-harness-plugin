# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Deterministic pipeline construction from a resolved blueprint.

The property under test is the one the plan calls for: when an agent builds a pipeline itself
rather than instantiating a marketplace template, *constructing the same blueprint twice must
produce the same graph* -- byte-identical, down to the ids the platform will store. Everything the
user genuinely chooses (dataset, model, code, node settings) is an input to the blueprint, so it is
pinned before construction starts; nothing downstream of that may vary.

Measured before writing any of this: constructing the same three-node graph twice through the
existing per-node path differs in 18 fields, and every one is a uuid4 `nodeId` or `portId`. Node
types, config, code, positions and edge structure were already stable. So identifier minting is the
whole problem.
"""

import uuid
from types import SimpleNamespace

import dtlpy as dl
import pytest

from doe_mcp import blueprint as bp
from doe_mcp.schemas import CodeNodeSpec, DatasetNodeSpec
from doe_mcp.tools import pipeline_nodes
from tests.test_pipeline_nodes import build_pipeline

CODE = "def run(item):\n    return item\n"


def rag_blueprint(output_dataset: str = "ds-out") -> bp.PipelineBlueprint:
    """A three-stage shape standing in for the RAG chain: ingest -> transform -> store."""
    return bp.PipelineBlueprint(
        name="rag",
        nodes=[
            DatasetNodeSpec(node_type="dataset", name="ingest", dataset_id="ds-1", position=(1, 1)),
            CodeNodeSpec(node_type="code", name="to_text", code=CODE, position=(2, 1)),
            DatasetNodeSpec(node_type="dataset", name="out", dataset_id=output_dataset, position=(3, 1)),
        ],
        edges=[bp.Edge(source="ingest", target="to_text"), bp.Edge(source="to_text", target="out")],
    )


def construct(plan: bp.PipelineBlueprint):
    pipeline = build_pipeline(project=SimpleNamespace(id="proj-1", name="proj"))
    bp.build_graph(pipeline, plan)
    return pipeline


# --- the headline property ---------------------------------------------------------------


def test_building_the_same_blueprint_twice_gives_an_identical_graph():
    plan = rag_blueprint()

    first = construct(plan).to_json()
    second = construct(plan).to_json()

    assert first == second


def test_ids_are_stable_across_processes_not_just_within_one():
    """Derived from the blueprint's content, so they survive a restart, a redeploy, another machine.

    Pinned as literals: a hash of in-memory state (or anything seeded from the clock or address
    space) would satisfy the test above while still differing between runs, which is the failure
    this whole module exists to prevent.
    """
    graph = construct(rag_blueprint()).to_json()

    assert [node["id"] for node in graph["nodes"]] == [
        "12f1833a-dfa0-5016-8e32-f0e52c13fc37",
        "6b2e034b-3632-54e2-b225-b952160e74e6",
        "56c5adf0-3ba5-5ffa-b759-1909c133e411",
    ]


def test_a_different_choice_gives_a_different_graph():
    """Determinism must not collapse into "always the same": the user's choices still drive it."""
    default = construct(rag_blueprint()).to_json()
    other = construct(rag_blueprint(output_dataset="ds-somewhere-else")).to_json()

    assert default != other
    assert [n["id"] for n in default["nodes"]] != [n["id"] for n in other["nodes"]]


def test_editing_one_node_does_not_change_an_unrelated_nodes_id():
    """Regression: deriving every node's id from a digest of the *whole* plan meant editing any
    one node -- or adding/removing an unrelated one -- also changed every other node's id, which
    defeated "referential integrity across a rebuild": a connection recorded against a node id
    would stop meaning the same node the moment the blueprint was edited at all. Each node's id
    must depend only on that node's own spec.
    """
    default = construct(rag_blueprint()).to_json()
    edited = construct(rag_blueprint(output_dataset="ds-somewhere-else")).to_json()

    by_name_default = {n["name"]: n["id"] for n in default["nodes"]}
    by_name_edited = {n["name"]: n["id"] for n in edited["nodes"]}

    # "out" is the node whose dataset_id changed, so only its id may change.
    assert by_name_default["ingest"] == by_name_edited["ingest"]
    assert by_name_default["to_text"] == by_name_edited["to_text"]
    assert by_name_default["out"] != by_name_edited["out"]


def test_two_pipelines_with_the_same_nodes_do_not_share_ids():
    """The other half of the property above, and the one it was accidentally traded for.

    Scoping the digest to a node's own spec stopped a sibling's edit from moving this node's id --
    but it also dropped the pipeline from the hash entirely, so two *different* pipelines built
    from the same choices produced byte-identical node and port ids. `skills.rag_blueprint`
    derives its graph from the datasets/model/chunking, so "rag-v1" and "rag-v2" over the same
    inputs collided on all of them.
    """
    v1 = construct(rag_blueprint()).to_json()
    other = rag_blueprint()
    v2 = construct(other.model_copy(update={"name": "rag-v2"})).to_json()

    v1_nodes = {node["id"] for node in v1["nodes"]}
    v2_nodes = {node["id"] for node in v2["nodes"]}
    v1_ports = {p["portId"] for n in v1["nodes"] for p in n["inputs"] + n["outputs"]}
    v2_ports = {p["portId"] for n in v2["nodes"] for p in n["inputs"] + n["outputs"]}

    assert v1_nodes.isdisjoint(v2_nodes), "two pipelines must not share node ids"
    assert v1_ports.isdisjoint(v2_ports), "two pipelines must not share port ids"


def test_identifiers_are_unique_within_a_graph():
    """Derivation must not collide two nodes or two ports onto one id."""
    graph = construct(rag_blueprint()).to_json()

    node_ids = [node["id"] for node in graph["nodes"]]
    port_ids = [port["portId"] for node in graph["nodes"] for port in node["inputs"] + node["outputs"]]

    assert len(set(node_ids)) == len(node_ids)
    assert len(set(port_ids)) == len(port_ids)


# --- the graph is actually correct, not just stable ---------------------------------------


def test_edges_connect_the_named_nodes():
    graph = construct(rag_blueprint()).to_json()
    by_id = {node["id"]: node["name"] for node in graph["nodes"]}

    wired = [(by_id[c["src"]["nodeId"]], by_id[c["tgt"]["nodeId"]]) for c in graph["connections"]]

    assert wired == [("ingest", "to_text"), ("to_text", "out")]


def test_the_first_node_becomes_the_start_node():
    pipeline = construct(rag_blueprint())

    assert pipeline.start_nodes, "a pipeline with no start node cannot be installed"


def test_an_edge_naming_an_unknown_node_is_rejected_before_any_write():
    plan = bp.PipelineBlueprint(
        name="broken",
        nodes=[DatasetNodeSpec(node_type="dataset", name="ingest", dataset_id="ds-1")],
        edges=[bp.Edge(source="ingest", target="nope")],
    )

    with pytest.raises(ValueError, match="edge 1 targets unknown node 'nope'"):
        construct(plan)


def test_duplicate_node_names_are_rejected():
    """Names are the blueprint's addressing scheme, so a collision is unresolvable."""
    with pytest.raises(ValueError, match="duplicate node name"):
        bp.PipelineBlueprint(
            name="dupes",
            nodes=[
                DatasetNodeSpec(node_type="dataset", name="a", dataset_id="ds-1"),
                DatasetNodeSpec(node_type="dataset", name="a", dataset_id="ds-2"),
            ],
            edges=[],
        )


# --- one write, not one per node ----------------------------------------------------------


def test_the_whole_graph_is_written_in_a_single_update():
    """Every extra PATCH is another window for the lost-update race (techspec A.4).

    The per-node path spends one whole-graph PATCH per node *and* per edge -- eleven for the RAG
    chain. Building locally and writing once removes ten of those windows outright, which is why
    this is a determinism property and not just a speed one: a lost update means the same blueprint
    produced a different graph.
    """
    pipeline = build_pipeline(project=SimpleNamespace(id="proj-1", name="proj"))
    writes = []
    pipeline.update = lambda: writes.append(1) or pipeline

    bp.build_graph(pipeline, rag_blueprint(), write=True)

    assert len(writes) == 1


def test_build_graph_does_not_write_unless_asked():
    """Construction is separable from committing it, so a caller can diff before writing."""
    pipeline = build_pipeline(project=SimpleNamespace(id="proj-1", name="proj"))
    writes = []
    pipeline.update = lambda: writes.append(1) or pipeline

    bp.build_graph(pipeline, rag_blueprint())

    assert writes == []


# --- comparing two runs ---------------------------------------------------------------------


def test_canonical_form_ignores_platform_assigned_noise():
    """What "same resulting configuration" means, made checkable.

    Ids are derived and so already stable, but a graph read back from the platform carries fields
    we never sent. The canonical form is what a run should be diffed against.
    """
    plan = rag_blueprint()
    graph = construct(plan)

    canonical = bp.canonical_graph(graph)

    assert canonical["nodes"][0]["name"] == "ingest"
    assert canonical["edges"] == [("ingest", "to_text", None), ("to_text", "out", None)]
    assert "id" not in canonical["nodes"][0]


def test_fingerprint_changes_only_when_a_choice_changes():
    same = bp.fingerprint(rag_blueprint())
    again = bp.fingerprint(rag_blueprint())
    different = bp.fingerprint(rag_blueprint(output_dataset="ds-other"))

    assert same == again
    assert same != different


def test_derived_ids_have_the_same_shape_dtlpy_emits():
    """A derived id has to be indistinguishable from the uuid4 it replaces.

    Caught during the prototype: an earlier version truncated to 34 characters, from misreading a
    debug print that had itself been truncated for display. dtlpy emits `str(uuid.uuid4())`, so a
    34-character id would have been a malformed UUID that only the platform would have rejected.
    """
    native = dl.DatasetNode(name="x", project_id="proj-1", dataset_id="ds-1")
    derived = bp._derived_id("some", "blueprint", "parts")

    assert len(derived) == len(native.node_id) == 36
    assert uuid.UUID(derived).version == 5
    assert uuid.UUID(native.node_id).version == 4


def test_declarative_build_matches_what_the_per_node_path_produces():
    """The deterministic builder must be a drop-in, not a different graph that happens to be stable.

    Builds the same blueprint both ways -- one node at a time as `pipeline_node_add` does, and in
    one pass -- and compares canonical form. If these ever diverge, the declarative path is
    building something subtly different from what the tools users already rely on produce.
    """
    plan = rag_blueprint()

    incremental = build_pipeline(project=SimpleNamespace(id="proj-1", name="proj"))
    by_name = {}
    for spec in plan.nodes:
        node = pipeline_nodes.build_node(incremental, spec)
        by_name[spec.name] = node
        incremental.nodes.add(node=node)
    for edge in plan.edges:
        by_name[edge.source].connect(node=by_name[edge.target], action=edge.action)

    assert bp.canonical_graph(incremental) == bp.canonical_graph(construct(plan))


def test_canonical_form_carries_no_platform_assigned_identifier():
    """Regression: start_nodes leaked raw node ids, so identical graphs compared unequal."""
    canonical = bp.canonical_graph(construct(rag_blueprint()))

    flattened = repr(canonical)
    for node in construct(rag_blueprint()).nodes:
        assert node.node_id not in flattened


# --- expected_canonical_graph: checking reuse without touching the platform ----------------


def test_expected_canonical_graph_matches_a_pipeline_built_from_the_same_plan():
    """What a caller compares a live pipeline against before deciding to reuse it."""
    plan = rag_blueprint()
    project = SimpleNamespace(id="proj-1", name="proj")

    assert bp.expected_canonical_graph(project, plan) == bp.canonical_graph(construct(plan))


def test_expected_canonical_graph_differs_when_a_choice_differs():
    project = SimpleNamespace(id="proj-1", name="proj")

    default = bp.expected_canonical_graph(project, rag_blueprint())
    other = bp.expected_canonical_graph(project, rag_blueprint(output_dataset="ds-somewhere-else"))

    assert default != other


def test_expected_canonical_graph_touches_neither_network_nor_platform():
    """Must be computable from the plan alone -- that is what makes the reuse check cheap."""
    project = SimpleNamespace(id="proj-1", name="proj")

    # A project stub with no repositories attached would blow up on any call that reached for
    # the platform (datasets, services, ...); it not blowing up is the assertion.
    bp.expected_canonical_graph(project, rag_blueprint())
