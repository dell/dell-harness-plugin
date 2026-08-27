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


def test_update_code_is_validated_before_the_pipeline_is_fetched(pipeline, call_tool, monkeypatch):
    """A syntax error in replacement code shouldn't cost a pipeline fetch to discover."""
    spec = CodeNodeSpec(node_type="code", name="filter", code=CODE)
    pipeline.nodes.add(pipeline_nodes.build_node(pipeline, spec))

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("get_pipeline was called before code was validated")

    monkeypatch.setattr(pipeline_nodes, "get_pipeline", fail_if_called)

    result = call_tool(
        "pipeline_node_update",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "node_name": "filter",
            "code": "def a():\n    pass\n\n\ndef b():\n    pass\n",
            "confirm_code": True,
        },
    )

    assert result.is_error is True
    assert "exactly one top-level function" in result.content[0].text


def test_update_requires_a_change(pipeline, call_tool):
    pipeline.nodes.add(dataset_node("ingest"))

    result = call_tool(
        "pipeline_node_update",
        {"project_id": "proj-1", "pipeline_id": "pipe-1", "node_name": "ingest"},
    )

    assert result.is_error is True
    assert "nothing to update" in result.content[0].text


# --- retry-until-visible (docs/eval.md's agentic-rag race, 2026-08-26) -----------------


def test_add_node_survives_one_stale_read(monkeypatch):
    """A verification read that misses the write we just made must not be trusted as final.

    Simulates the real defect: dtlpy's Pipelines.update() PATCHes the platform with this
    process's entire local node list, so an independent re-read taken before that write has
    propagated can come back without the node that was just added. The retry must not treat
    that as failure to add the node -- only as failure to *see* it yet.
    """
    live = build_pipeline()
    get = flaky_get_pipeline(live, stale_on=2)  # stale on the verification read after the first write
    monkeypatch.setattr(pipeline_nodes, "get_pipeline", get)
    monkeypatch.setattr(pipeline_nodes.time, "sleep", lambda _seconds: None)

    spec = DatasetNodeSpec(node_type="dataset", name="ingest", dataset_id="ds-1")
    result = pipeline_nodes.add_node("proj-1", "pipe-1", spec)

    assert result["node"]["name"] == "ingest"
    assert [n.name for n in live.nodes] == ["ingest"], "must not have re-added the node a second time"
    # Without this the test passes against a naive no-retry add_node, which never takes the stale
    # read at all: it is the re-read, not the outcome, that distinguishes the fix.
    assert get.calls["n"] >= 3, "must have re-read after the stale verification read"


def test_add_node_reports_the_verified_read_not_the_locally_mutated_copy(monkeypatch):
    """The returned graph must be the independent re-read, including changes we did not make.

    `_retry_until_visible` deliberately returns the *verification* read rather than the object it
    mutated locally (or the one `update()` echoed back), so the caller sees what the platform
    actually holds. Here another writer's node exists on the platform but not in our local copy:
    reporting the local copy would tell the caller the graph is smaller than it is.
    """
    local = build_pipeline()
    platform = build_pipeline()
    platform.nodes.add(dataset_node("ingest"))
    platform.nodes.add(dataset_node("someone-elses", "ds-9"))
    reads = iter([local, platform, platform])
    monkeypatch.setattr(pipeline_nodes, "get_pipeline", lambda project_id, pipeline_id: next(reads))
    monkeypatch.setattr(pipeline_nodes.time, "sleep", lambda _seconds: None)

    spec = DatasetNodeSpec(node_type="dataset", name="ingest", dataset_id="ds-1")
    result = pipeline_nodes.add_node("proj-1", "pipe-1", spec)

    assert result["pipeline"]["node_count"] == 2, (
        "reported the locally mutated copy instead of the independently verified read"
    )


def test_add_node_builds_the_node_once_across_retries(monkeypatch):
    """A retry must re-send the *same* node, not build a fresh one with a new node_id.

    `build_node` mints a new `node_id` every call. If a retry rebuilds, and attempt 1's write had
    actually landed, the platform ends up with a same-named node under a different id -- and any
    connection it already held referencing the old id is left dangling. That is the "node missing,
    connections duplicated" shape from the live agentic-rag run. Building once also avoids repeating
    build_node's own platform calls (`services.get`/`datasets.get` for function and task nodes).
    """
    live = build_pipeline()
    get = flaky_get_pipeline(live, stale_on=(2, 3))  # stale verification read, then a stale write read
    monkeypatch.setattr(pipeline_nodes, "get_pipeline", get)
    monkeypatch.setattr(pipeline_nodes.time, "sleep", lambda _seconds: None)

    builds = []
    real_build = pipeline_nodes.build_node

    def counting_build(pipeline, spec):
        built = real_build(pipeline, spec)
        builds.append(built)
        return built

    monkeypatch.setattr(pipeline_nodes, "build_node", counting_build)

    spec = DatasetNodeSpec(node_type="dataset", name="ingest", dataset_id="ds-1")
    result = pipeline_nodes.add_node("proj-1", "pipe-1", spec)

    assert len(builds) == 1, "rebuilt the node on retry, minting a second node_id"
    assert result["node"]["id"] == builds[0].node_id


def test_retry_backs_off_before_rewriting_not_after(monkeypatch):
    """The delay must precede the next write, not merely the next verification read.

    The failure being handled is a write the platform has not propagated yet. Re-reading and
    immediately re-PATCHing the whole node list from a possibly-still-stale snapshot is the
    riskiest step in the loop, so it is the one that must wait.
    """
    live = build_pipeline()
    events = []

    def get(project_id, pipeline_id):
        events.append("read")
        return build_pipeline() if len(events) == 2 else live

    monkeypatch.setattr(pipeline_nodes, "get_pipeline", get)
    monkeypatch.setattr(pipeline_nodes.time, "sleep", lambda _seconds: events.append("sleep"))

    spec = DatasetNodeSpec(node_type="dataset", name="ingest", dataset_id="ds-1")
    pipeline_nodes.add_node("proj-1", "pipe-1", spec)

    # attempt 1: write read, stale verify read -> sleep -> attempt 2: write read, verify read
    assert events == ["read", "read", "sleep", "read", "read"]


def test_connect_nodes_distinguishes_edges_by_action(monkeypatch, call_tool):
    """Two edges between the same ports but with different actions are distinct edges.

    DOE models conditional routing as several connections off one output port, differing only
    by `action`. Matching on ports alone made the second call a no-op that reported success without
    creating the edge -- and made `verify` unable to tell whether the requested action landed.
    """
    live = build_pipeline()
    live.nodes.add(dataset_node("ingest"))
    live.nodes.add(dataset_node("archive", "ds-2"))
    monkeypatch.setattr(pipeline_nodes, "get_pipeline", lambda project_id, pipeline_id: live)
    monkeypatch.setattr(pipeline_nodes.time, "sleep", lambda _seconds: None)
    args = {
        "project_id": "proj-1",
        "pipeline_id": "pipe-1",
        "source_node_name": "ingest",
        "target_node_name": "archive",
    }

    first = call_tool("pipeline_connect_nodes", {**args, "action": "complete"})
    second = call_tool("pipeline_connect_nodes", {**args, "action": "discard"})
    repeat = call_tool("pipeline_connect_nodes", {**args, "action": "discard"})

    assert first.is_error is False
    assert second.is_error is False
    assert repeat.is_error is False
    actions = sorted(c.action for c in live.connections)
    assert actions == ["complete", "discard"], "a differing action must add an edge, a repeat must not"


def test_add_node_still_rejects_a_genuine_duplicate_name(monkeypatch):
    """A name collision that was already there before this call must fail immediately, not retry."""
    live = build_pipeline()
    live.nodes.add(dataset_node("ingest"))
    monkeypatch.setattr(pipeline_nodes, "get_pipeline", lambda project_id, pipeline_id: live)

    slept = []
    monkeypatch.setattr(pipeline_nodes.time, "sleep", slept.append)

    spec = DatasetNodeSpec(node_type="dataset", name="ingest", dataset_id="ds-2")
    with pytest.raises(ValueError, match="already has a node named"):
        pipeline_nodes.add_node("proj-1", "pipe-1", spec)

    assert slept == [], "a genuine conflict must fail on the first attempt, never retry"


def test_add_node_gives_up_after_persistent_staleness(monkeypatch):
    """If every independent re-read keeps missing the write, say so plainly instead of hanging."""
    # Every fetch returns a *fresh, empty* copy, so the node this call adds is never the one a
    # later verification read sees -- worst-case persistent staleness.
    monkeypatch.setattr(pipeline_nodes, "get_pipeline", lambda project_id, pipeline_id: build_pipeline())
    monkeypatch.setattr(pipeline_nodes.time, "sleep", lambda _seconds: None)

    spec = DatasetNodeSpec(node_type="dataset", name="ingest", dataset_id="ds-1")
    with pytest.raises(RuntimeError, match="still isn't visible on an independent re-read"):
        pipeline_nodes.add_node("proj-1", "pipe-1", spec)


def flaky_get_pipeline(live, stale_on):
    """A `get_pipeline` returning an empty snapshot on the given call numbers, `live` otherwise.

    Models the platform serving a read taken before a very recent write propagated — the exact
    condition `_retry_until_visible` exists to survive. `calls` is exposed so a test can assert
    the retry actually re-read rather than passing for some other reason.
    """
    stale = {stale_on} if isinstance(stale_on, int) else set(stale_on)
    calls = {"n": 0}

    def _get(project_id, pipeline_id):
        calls["n"] += 1
        return build_pipeline() if calls["n"] in stale else live

    _get.calls = calls
    return _get


def test_connect_nodes_retries_when_the_verification_read_is_stale(monkeypatch, call_tool):
    """A stale verification read must drive a retry, not surface as "no node named X".

    Regression: `verify` resolved ports through `_require_node`, which *raises* when a node is
    absent — precisely what a stale snapshot looks like. The exception escaped the retry loop, so
    the tool aborted after two reads with a flatly false error about a node it had just used. This
    is the same operation (connect, right after add) that lost graph state live, so the retry
    failing to engage here defeats the fix on the path that motivated it.
    """
    live = build_pipeline()
    live.nodes.add(dataset_node("ingest"))
    live.nodes.add(dataset_node("archive", "ds-2"))
    get = flaky_get_pipeline(live, stale_on=2)
    monkeypatch.setattr(pipeline_nodes, "get_pipeline", get)
    monkeypatch.setattr(pipeline_nodes.time, "sleep", lambda _seconds: None)

    result = call_tool(
        "pipeline_connect_nodes",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "source_node_name": "ingest",
            "target_node_name": "archive",
        },
    )

    assert result.is_error is False
    assert len(live.connections) == 1, "must not have duplicated the edge across attempts"
    assert get.calls["n"] >= 3, "must have re-read after the stale verification read"


def test_disconnect_nodes_retries_when_the_verification_read_is_stale(monkeypatch, call_tool):
    """Same stale-read defect as connect, on the destructive side."""
    live = build_pipeline()
    live.nodes.add(dataset_node("ingest"))
    live.nodes.add(dataset_node("archive", "ds-2"))
    source = live.nodes.get(node_name="ingest")
    source.connect(node=live.nodes.get(node_name="archive"))
    get = flaky_get_pipeline(live, stale_on=2)
    monkeypatch.setattr(pipeline_nodes, "get_pipeline", get)
    monkeypatch.setattr(pipeline_nodes.time, "sleep", lambda _seconds: None)

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

    assert result.is_error is False
    assert live.connections == []
    assert get.calls["n"] >= 3, "must have re-read after the stale verification read"


def test_node_update_survives_one_stale_read(monkeypatch, call_tool):
    """pipeline_node_update must re-read and verify like every other graph mutation.

    Regression: it was the one write path left as a bare read-modify-write, so a rename racing a
    recent node add would PATCH the platform with a stale full node list -- silently dropping the
    new node -- and then report success from `update()`'s own echo.
    """
    live = build_pipeline()
    live.nodes.add(dataset_node("ingest"))
    get = flaky_get_pipeline(live, stale_on=2)
    monkeypatch.setattr(pipeline_nodes, "get_pipeline", get)
    monkeypatch.setattr(pipeline_nodes.time, "sleep", lambda _seconds: None)

    result = call_tool(
        "pipeline_node_update",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "node_name": "ingest",
            "new_name": "landing",
        },
    )

    assert result.is_error is False
    assert [n.name for n in live.nodes] == ["landing"]
    assert get.calls["n"] >= 3, "must have re-read after the stale verification read"


def test_connect_nodes_retries_when_a_later_mutate_read_is_stale(monkeypatch, call_tool):
    """Staleness on a *write* read, not just a verification read, must also retry rather than raise.

    `attempt == 1` gates the strict re-resolution precisely so that a node missing from a fresh
    first read raises the real reason, while the same shape on a later attempt is treated as a
    stale snapshot. Without the gate the loop raises "no node named X" mid-retry.
    """
    live = build_pipeline()
    live.nodes.add(dataset_node("ingest"))
    live.nodes.add(dataset_node("archive", "ds-2"))
    get = flaky_get_pipeline(live, stale_on=(2, 3))  # verification read, then the next write read
    monkeypatch.setattr(pipeline_nodes, "get_pipeline", get)
    monkeypatch.setattr(pipeline_nodes.time, "sleep", lambda _seconds: None)

    result = call_tool(
        "pipeline_connect_nodes",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "source_node_name": "ingest",
            "target_node_name": "archive",
        },
    )

    assert result.is_error is False
    assert len(live.connections) == 1


def test_node_update_does_not_rename_a_node_that_reused_the_old_name(monkeypatch, call_tool):
    """After a rename lands, a node that has since taken the old name must be left alone.

    This is the concurrent case the `final_name`-first lookup in `target_node` exists for: our
    rename to `landing` landed, another writer created a fresh `ingest`, and a retry that looked up
    the old name first would rename *their* node too, silently clobbering it.
    """
    live = build_pipeline()
    live.nodes.add(dataset_node("ingest"))
    calls = {"n": 0}

    def get(project_id, pipeline_id):
        calls["n"] += 1
        if calls["n"] == 2:
            return build_pipeline()  # stale verification read, forcing a retry
        if calls["n"] == 3:  # someone else re-created the old name in the meantime
            live.nodes.add(dataset_node("ingest", "ds-other"))
        return live

    monkeypatch.setattr(pipeline_nodes, "get_pipeline", get)
    monkeypatch.setattr(pipeline_nodes.time, "sleep", lambda _seconds: None)

    result = call_tool(
        "pipeline_node_update",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "node_name": "ingest",
            "new_name": "landing",
        },
    )

    assert result.is_error is False
    assert sorted(n.name for n in live.nodes) == ["ingest", "landing"], (
        "the retry renamed the other writer's node instead of recognising its own landed rename"
    )


def test_node_update_finds_a_landed_rename_under_its_new_name(monkeypatch, call_tool):
    """Repeated staleness after a rename has landed must still converge, not time out.

    Once attempt 1's rename is on the platform, the node no longer answers to `node_name`. If a
    later attempt looks it up only under the old name it finds nothing, does nothing, and — if the
    verification read is stale again — the loop exhausts its attempts and raises about a change
    that has demonstrably landed. Hence the `final_name`-first lookup in `target_node`.
    """
    live = build_pipeline()
    live.nodes.add(dataset_node("ingest"))
    get = flaky_get_pipeline(live, stale_on=(2, 4))  # both verification reads lag
    monkeypatch.setattr(pipeline_nodes, "get_pipeline", get)
    monkeypatch.setattr(pipeline_nodes.time, "sleep", lambda _seconds: None)

    result = call_tool(
        "pipeline_node_update",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "node_name": "ingest",
            "new_name": "landing",
        },
    )

    assert result.is_error is False
    assert [n.name for n in live.nodes] == ["landing"], "must not have renamed or re-added twice"
    assert result.structured_content["node"]["name"] == "landing"


def test_node_update_reports_the_independently_verified_graph(monkeypatch, call_tool):
    """The returned payload must come from a fresh read, never from update()'s echo.

    `Pipelines.update` builds its return value from the response to its own PATCH, so it reflects
    what this process sent rather than what the platform now holds. Reporting it back is how a
    lost update gets reported as a success.
    """
    live = build_pipeline()
    live.nodes.add(dataset_node("ingest"))
    echo = build_pipeline()
    echo.nodes.add(dataset_node("ingest"))
    echo.nodes.add(dataset_node("ghost", "ds-9"))  # only ever in update()'s echo, never on "the platform"
    live.update = lambda: echo
    monkeypatch.setattr(pipeline_nodes, "get_pipeline", lambda project_id, pipeline_id: live)
    monkeypatch.setattr(pipeline_nodes.time, "sleep", lambda _seconds: None)

    result = call_tool(
        "pipeline_node_update",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "node_name": "ingest",
            "position": [2, 3],
        },
    )

    assert result.is_error is False
    assert result.structured_content["pipeline"]["node_count"] == 1, (
        "reported update()'s 2-node echo instead of the platform's actual 1-node graph"
    )


def test_dtlpy_position_setter_scales_grid_coordinates():
    """Pin the upstream behaviour `_stored_position` mirrors.

    `PipelineNode.position`'s setter scales grid coords to canvas pixels rather than storing them,
    so what reads back is not what was assigned and assigning twice compounds. If dtlpy ever
    changes the factors — or starts storing the value verbatim — this fails, rather than leaving
    `_stored_position` silently wrong and every position verification failing into a retry timeout.
    """
    node = dataset_node("ingest")
    node.position = (2, 3)

    assert tuple(node.position) == pipeline_nodes._stored_position((2, 3))
    assert tuple(node.position) != (2, 3), "if this ever holds, the scaling workaround is obsolete"

    node.position = (2, 3)
    assert tuple(node.position) == pipeline_nodes._stored_position((2, 3)), (
        "setter must be idempotent for a re-applied position, or retries drift the node"
    )


def test_node_update_does_not_drift_position_across_retries(monkeypatch, call_tool):
    """A retry must not re-apply a position that already landed.

    dtlpy's position setter compounds its scaling, so re-assigning on attempt 2 would move the node
    twice as far. Guarded by `applied()` comparing against the stored form.
    """
    live = build_pipeline()
    live.nodes.add(dataset_node("ingest"))
    get = flaky_get_pipeline(live, stale_on=2)
    monkeypatch.setattr(pipeline_nodes, "get_pipeline", get)
    monkeypatch.setattr(pipeline_nodes.time, "sleep", lambda _seconds: None)

    result = call_tool(
        "pipeline_node_update",
        {"project_id": "proj-1", "pipeline_id": "pipe-1", "node_name": "ingest", "position": [2, 3]},
    )

    assert result.is_error is False
    assert get.calls["n"] >= 3, "must have retried past the stale read"
    node = live.nodes.get(node_name="ingest")
    assert tuple(node.position) == pipeline_nodes._stored_position((2, 3))


def test_node_update_still_reports_a_missing_node(monkeypatch, call_tool):
    """A node that genuinely isn't there must fail fast, not retry into a timeout."""
    monkeypatch.setattr(pipeline_nodes, "get_pipeline", lambda project_id, pipeline_id: build_pipeline())
    slept = []
    monkeypatch.setattr(pipeline_nodes.time, "sleep", slept.append)

    result = call_tool(
        "pipeline_node_update",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "node_name": "nope",
            "new_name": "x",
        },
    )

    assert result.is_error is True
    assert "has no node named" in result.content[0].text
    assert slept == [], "a genuine missing node must fail on the first attempt, never retry"


def test_connect_nodes_is_idempotent(pipeline, call_tool):
    """Calling connect twice for the same ports must not create a second, duplicate edge."""
    pipeline.nodes.add(dataset_node("ingest"))
    pipeline.nodes.add(dataset_node("archive", "ds-2"))
    args = {
        "project_id": "proj-1",
        "pipeline_id": "pipe-1",
        "source_node_name": "ingest",
        "target_node_name": "archive",
    }

    first = call_tool("pipeline_connect_nodes", args)
    second = call_tool("pipeline_connect_nodes", args)

    assert first.is_error is False
    assert second.is_error is False
    assert len(pipeline.connections) == 1


# --- upstream dtlpy behaviour these workarounds depend on -------------------------------


def test_dtlpy_update_patches_the_whole_graph_not_a_diff(monkeypatch):
    """Pin the defect the entire retry-until-visible mitigation exists to work around.

    `Pipelines.update` builds its request body as `pipeline.to_json()` and PATCHes the lot, so a
    read that raced a recent write overwrites the platform's node list with a stale one. Every
    other test in this file stubs `update()` out, which means nothing would otherwise notice if
    dtlpy switched to a diff-based PATCH or grew a version precondition -- and the retry loop would
    quietly become redundant (or, worse, wrong) with no failing test to say so.

    Drives the *real* `Pipelines.update` with only the HTTP layer faked.
    """
    captured = {}

    def fake_gen_request(req_type, path, json_req=None, **kwargs):
        captured["req_type"] = req_type
        captured["path"] = path
        captured["json"] = json_req
        response = SimpleNamespace(
            json=lambda: {"id": "pipe-1", "name": "p", "projectId": "proj-1", "nodes": [], "connections": []}
        )
        return True, response

    pipeline = build_pipeline()
    del pipeline.update  # drop the fixture's stub; we want dtlpy's real method
    pipeline.nodes.add(dataset_node("ingest"))
    pipeline.nodes.add(dataset_node("archive", "ds-2"))

    repo = dl.repositories.Pipelines(client_api=dl.client_api)
    monkeypatch.setattr(dl.client_api, "gen_request", fake_gen_request)
    monkeypatch.setattr(repo._client_api, "check_response", lambda *a, **k: True)

    repo.update(pipeline=pipeline)

    assert captured["req_type"] == "patch"
    assert captured["json"] == pipeline.to_json(), "update() no longer sends the whole to_json()"
    sent_nodes = [n["name"] for n in captured["json"]["nodes"]]
    assert sent_nodes == ["ingest", "archive"], (
        "update() sends the caller's entire node list, which is why a stale read loses updates"
    )
    assert not {"version", "etag", "ifMatch"} & set(captured["json"]), (
        "update() gained a version/ETag field -- the race may now be closable properly"
    )
