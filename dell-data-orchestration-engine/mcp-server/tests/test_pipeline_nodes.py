"""Graph editing.

These use a real ``Pipeline`` entity built from JSON, so the dtlpy graph semantics under
test (start-node bookkeeping, cascading connection removal, port matching) are the genuine
ones. Only the final ``update()`` round trip is stubbed out.
"""

from types import SimpleNamespace

import dtlpy as dl
import pytest

from doe_mcp.schemas import CodeNodeSpec, DatasetNodeSpec, TaskNodeSpec
from doe_mcp.tools import pipeline_nodes

CODE = "def run(item):\n    return item\n"


def build_pipeline(project=None):
    pipeline = dl.entities.Pipeline.from_json(
        _json={
            "id": "pipe-1",
            "name": "p",
            "projectId": "proj-1",
            "nodes": [],
            "connections": [],
            "startNodes": [],
            "variables": [],
        },
        client_api=dl.client_api,
        project=project,
        is_fetched=False,
    )
    # Keep the graph local: update() would otherwise PATCH the platform.
    pipeline.update = lambda: pipeline
    return pipeline


def dataset_node(name, dataset_id="ds-1"):
    return dl.DatasetNode(name=name, project_id="proj-1", dataset_id=dataset_id)


@pytest.fixture
def pipeline(monkeypatch):
    project = SimpleNamespace(id="proj-1", name="proj")
    pipe = build_pipeline(project=project)
    monkeypatch.setattr(pipeline_nodes, "get_pipeline", lambda project_id, pipeline_id: pipe)
    return pipe


# --- building nodes --------------------------------------------------------------------


def test_build_dataset_node(pipeline):
    spec = DatasetNodeSpec(node_type="dataset", name="ingest", dataset_id="ds-1", dataset_folder="/incoming")

    node = pipeline_nodes.build_node(pipeline, spec)

    assert node.node_type == dl.entities.PipelineNodeType.STORAGE
    assert node.dataset_id == "ds-1"
    assert node.dataset_folder == "/incoming"


def test_build_dataset_node_with_data_filters(pipeline):
    """data_filters scopes load_existing_data without needing a separate cloned dataset.

    Regression: DatasetNodeSpec used to drop this dtlpy-supported parameter entirely, forcing
    a clone-to-a-new-dataset workaround for anything narrower than "the whole dataset".
    """
    spec = DatasetNodeSpec(
        node_type="dataset",
        name="ingest",
        dataset_id="ds-1",
        load_existing_data=True,
        data_filters={"filter": {"$and": [{"metadata.system.mimetype": {"$eq": "application/pdf"}}]}},
    )

    node = pipeline_nodes.build_node(pipeline, spec)

    assert node.load_existing_data is True
    filter_json = node.metadata["triggerToPipeline"]["filter"]
    assert '"application/pdf"' in filter_json


def test_build_dataset_node_without_data_filters_leaves_it_unset(pipeline):
    spec = DatasetNodeSpec(node_type="dataset", name="ingest", dataset_id="ds-1", load_existing_data=True)

    node = pipeline_nodes.build_node(pipeline, spec)

    assert node.metadata["triggerToPipeline"]["filter"] is None


def test_build_code_node(pipeline):
    spec = CodeNodeSpec(node_type="code", name="filter", code=CODE)

    node = pipeline_nodes.build_node(pipeline, spec)

    assert node.node_type == dl.entities.PipelineNodeType.CODE
    assert node.config["package"]["name"] == "run"


def test_task_node_without_a_recipe_is_reported_clearly(pipeline):
    """A dataset with no recipe cannot back a task node; say which argument fixes it."""
    dataset = SimpleNamespace(id="ds-1", get_recipe_ids=lambda: [])
    spec = TaskNodeSpec(
        node_type="task",
        name="label",
        dataset_id="ds-1",
        task_owner="me@example.com",
        workload=[{"assignee_id": "me@example.com", "load": 100}],
    )

    with pytest.raises(ValueError, match="explicit recipe_id"):
        pipeline_nodes._resolve_recipe(dataset, spec)


# --- node lookup and ports -------------------------------------------------------------


def test_missing_node_error_lists_what_is_there(pipeline):
    pipeline.nodes.add(dataset_node("ingest"))

    with pytest.raises(ValueError, match="Existing nodes: ingest"):
        pipeline_nodes._require_node(pipeline, "nope")


def test_port_defaults_to_the_first_one(pipeline):
    node = dataset_node("ingest")

    assert pipeline_nodes._require_port(node, None, "output") is node.outputs[0]
    assert pipeline_nodes._require_port(node, None, "input") is node.inputs[0]


def test_unknown_port_is_rejected(pipeline):
    node = dataset_node("ingest")

    with pytest.raises(ValueError, match="has no output port"):
        pipeline_nodes._require_port(node, "not-a-port", "output")


# --- tools ------------------------------------------------------------------------------


def test_add_node_makes_the_first_node_the_start_node(pipeline, call_tool):
    result = call_tool(
        "pipeline_node_add",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "node": {"node_type": "dataset", "name": "ingest", "dataset_id": "ds-1"},
        },
    )

    assert result.is_error is False
    assert [n.name for n in pipeline.nodes] == ["ingest"]
    assert pipeline.start_nodes[0]["type"] == "root"


def test_add_node_rejects_duplicate_names(pipeline, call_tool):
    pipeline.nodes.add(dataset_node("ingest"))

    result = call_tool(
        "pipeline_node_add",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "node": {"node_type": "dataset", "name": "ingest", "dataset_id": "ds-2"},
        },
    )

    assert result.is_error is True
    assert "already has a node named" in result.content[0].text


def test_add_node_rejects_a_node_type_that_does_not_exist(pipeline, call_tool):
    """The discriminated union rejects this before any platform call happens."""
    result = call_tool(
        "pipeline_node_add",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "node": {"node_type": "ml", "name": "predict", "model_id": "m-1"},
        },
    )

    assert result.is_error is True
    assert pipeline.nodes == []


def test_add_code_node_rejects_multi_function_source(pipeline, call_tool):
    result = call_tool(
        "pipeline_node_add",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "node": {
                "node_type": "code",
                "name": "filter",
                "code": "def a():\n    pass\n\n\ndef b():\n    pass\n",
            },
            "confirm_code": True,
        },
    )

    assert result.is_error is True
    assert "exactly one top-level function" in result.content[0].text


def test_connect_then_disconnect(pipeline, call_tool):
    pipeline.nodes.add(dataset_node("ingest"))
    pipeline.nodes.add(dataset_node("archive", "ds-2"))

    connected = call_tool(
        "pipeline_connect_nodes",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "source_node_name": "ingest",
            "target_node_name": "archive",
        },
    )
    assert connected.is_error is False
    assert len(pipeline.connections) == 1

    disconnected = call_tool(
        "pipeline_disconnect_nodes",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "source_node_name": "ingest",
            "target_node_name": "archive",
            "confirm": True,
        },
    )
    assert disconnected.is_error is False
    assert pipeline.connections == []


def test_disconnecting_a_missing_edge_is_an_error(pipeline, call_tool):
    pipeline.nodes.add(dataset_node("ingest"))
    pipeline.nodes.add(dataset_node("archive", "ds-2"))

    result = call_tool(
        "pipeline_disconnect_nodes",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "source_node_name": "ingest",
            "target_node_name": "archive",
            "confirm": True,
        },
    )

    assert result.is_error is True
    assert "No connection found" in result.content[0].text


def test_removing_a_node_also_drops_its_connections(pipeline, call_tool):
    first, second = dataset_node("ingest"), dataset_node("archive", "ds-2")
    pipeline.nodes.add(first)
    pipeline.nodes.add(second)
    first.connect(node=second, source_port=first.outputs[0], target_port=second.inputs[0])
    assert len(pipeline.connections) == 1

    result = call_tool(
        "pipeline_node_remove",
        {"project_id": "proj-1", "pipeline_id": "pipe-1", "node_name": "ingest", "confirm": True},
    )

    assert result.is_error is False
    assert [n.name for n in pipeline.nodes] == ["archive"]
    assert pipeline.connections == []


def test_node_is_removed_by_name_not_id(pipeline, call_tool):
    """dtlpy's Nodes.remove only accepts a name, so passing an id must not silently pass."""
    node = dataset_node("ingest")
    pipeline.nodes.add(node)

    result = call_tool(
        "pipeline_node_remove",
        {"project_id": "proj-1", "pipeline_id": "pipe-1", "node_name": node.node_id, "confirm": True},
    )

    assert result.is_error is True
    assert [n.name for n in pipeline.nodes] == ["ingest"]


def test_update_code_node_replaces_the_function(pipeline, call_tool):
    spec = CodeNodeSpec(node_type="code", name="filter", code=CODE)
    pipeline.nodes.add(pipeline_nodes.build_node(pipeline, spec))

    result = call_tool(
        "pipeline_node_update",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "node_name": "filter",
            "code": "def keep_images(item):\n    return item\n",
            "confirm_code": True,
        },
    )

    assert result.is_error is False
    node = pipeline.nodes.get(node_name="filter")
    assert node.config["package"]["name"] == "keep_images"
    assert node.namespace.function_name == "keep_images"


def test_code_cannot_be_set_on_a_non_code_node(pipeline, call_tool):
    pipeline.nodes.add(dataset_node("ingest"))

    result = call_tool(
        "pipeline_node_update",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "node_name": "ingest",
            "code": CODE,
        },
    )

    assert result.is_error is True
    assert "can only be set on a code node" in result.content[0].text


def test_update_requires_a_change(pipeline, call_tool):
    pipeline.nodes.add(dataset_node("ingest"))

    result = call_tool(
        "pipeline_node_update",
        {"project_id": "proj-1", "pipeline_id": "pipe-1", "node_name": "ingest"},
    )

    assert result.is_error is True
    assert "nothing to update" in result.content[0].text
