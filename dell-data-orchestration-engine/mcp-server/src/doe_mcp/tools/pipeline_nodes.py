# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Editing a pipeline's node graph.

Two dtlpy constraints shape these signatures:

* ``pipeline.nodes.remove`` looks a node up by **name**, not id, so nodes are addressed by
  name throughout for consistency.
* ``PipelineConnection`` has no id. A connection is identified by its source and target
  ports, which is what ``PipelineNode.disconnect`` matches on.

The SDK also mutates the graph in ways worth knowing about: adding the first node makes it
the start node, connecting to a node adds that node to the pipeline if it is missing, and
removing a node drops every connection touching it and repairs the start nodes.
"""

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Annotated, Any

import dtlpy as dl
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import Field

from .. import code_node, outputs, serialize
from ..context import dataloop_call, get_pipeline, owned
from ..guards import confirm_or_elicit
from ..safety import ADDITIVE, DELETE, OVERWRITE
from ..schemas import (
    CodeNodeSpec,
    DatasetNodeSpec,
    FunctionNodeSpec,
    NodeSpec,
    TaskNodeSpec,
)

_locks_guard = threading.Lock()
_pipeline_locks: dict[str, threading.Lock] = {}


@contextmanager
def pipeline_lock(pipeline_id: str) -> Iterator[None]:
    """Serialize graph mutations against this process's own concurrent tool calls.

    This is an **in-process** lock only, and does not — cannot — protect against the platform
    itself serving a stale read: `dtlpy.repositories.pipelines.Pipelines.update` sends the
    *entire* `pipeline.to_json()` node/connection list as its PATCH payload (verified against
    dtlpy 1.125.6's source), so a `get_pipeline` read that misses a very recently written node
    or connection produces an `update()` call that silently drops it — a classic
    read-modify-write lost update. Live-verified 2026-08-26 (docs/eval.md's agentic-rag run):
    sequential `rag_add_embedding_node` / `pipeline_connect_nodes` calls a few seconds apart,
    each from a separate process (no shared lock to begin with), lost a node and duplicated two
    connections before settling. A second `doe-mcp` process, a retry after a timeout, or even
    this same process racing the platform's own write-propagation delay can all hit this — this
    lock alone cannot catch it. `_retry_until_visible` below is the main defense: all five graph
    mutations in this module (`add_node`, `pipeline_node_update`, `pipeline_node_remove`,
    `pipeline_connect_nodes`, `pipeline_disconnect_nodes`) re-read independently after writing and
    retry with backoff until that re-read confirms the change, rather than trusting `update()`'s own
    return value (which just echoes back what this process sent, not what a fresh read of the
    platform now shows).

    It **narrows the race, it does not eliminate it.** The write is still a whole-graph PATCH built
    from a possibly-stale read, so a concurrent writer's PATCH can still clobber a node this call
    verified, and this call's PATCH can still clobber theirs; convergence only holds while both
    writers are inside their retry windows. Closing it properly needs a version/ETag precondition on
    `Pipelines.update`, which the SDK does not offer. See `docs/eval.md`.
    """
    with _locks_guard:
        lock = _pipeline_locks.setdefault(pipeline_id, threading.Lock())
    with lock:
        yield


def _retry_until_visible(
    project_id: str,
    pipeline_id: str,
    mutate: Callable[[dl.entities.Pipeline, int], None],
    verify: Callable[[dl.entities.Pipeline], bool],
    *,
    max_attempts: int = 4,
    base_delay: float = 1.5,
) -> dl.entities.Pipeline:
    """Apply a pipeline graph mutation, retrying until an independent re-read confirms it.

    See `pipeline_lock`'s docstring for why this exists: `pipeline.update()` PATCHes the
    platform with this process's entire local node/connection list, so a `get_pipeline` read
    that races a very recent write can silently discard it on the next `update()`. The only
    real defense is a **fresh, independent** read after writing — never the object `update()`
    itself returns.

    The first check happens with no delay, so the common case (nothing racing) costs one extra
    read and no wall-clock time; only a failed check pays the backoff.

    Two contracts callers must honour, both of which have been the source of real defects:

    - **`mutate(pipeline, attempt)` must be a no-op when the target state is already present**,
      since `attempt > 1` means a previous attempt's write may have landed even though verifying it
      hadn't. A caller that needs to reject a genuine pre-existing conflict (a duplicate node name,
      a connection that was never there) should raise for it only on `attempt == 1`, when the read
      is known to be fresh. Re-applying a non-idempotent write here is how a node's position ends
      up double-scaled (see `_stored_position`).
    - **`verify(pipeline)` must be total: it may not raise for a state it cannot observe.** A
      verification read is allowed to be a stale snapshot, so "the node isn't there" is an expected
      answer, not an error. Letting `_require_node`/`_require_port` raise out of `verify` aborts the
      very retry this loop exists to perform, and reports a missing node the call just used
      successfully — use `_connection_state`, which returns `None` for exactly that case.
    """
    delay = 0.0
    for attempt in range(1, max_attempts + 1):
        # Back off *before* re-reading and re-writing, never between the write and its check: the
        # condition being waited out is an unpropagated write, and re-PATCHing the whole node list
        # from a still-stale read is the most destructive thing this loop can do. The first pass has
        # no delay, so the common case costs one extra read and no wall-clock time.
        if delay:
            time.sleep(delay)
        with pipeline_lock(pipeline_id):
            pipeline = get_pipeline(project_id, pipeline_id)
            mutate(pipeline, attempt)
        verified = get_pipeline(project_id, pipeline_id)
        if verify(verified):
            return verified
        delay = base_delay if delay == 0 else delay * 2
    raise RuntimeError(
        f"Change to pipeline {pipeline_id!r} still isn't visible on an independent re-read "
        f"after {max_attempts} attempts. The platform may be unusually slow to propagate "
        "writes right now; call pipeline_get(include_graph=true) to check before assuming "
        "nothing happened."
    )


def _resolve_recipe(dataset: dl.entities.Dataset, spec: TaskNodeSpec) -> tuple[str, str]:
    """Work out which recipe a task node should annotate with."""
    recipe_id = spec.recipe_id
    if recipe_id is None:
        recipe_ids = dataset.get_recipe_ids()
        if not recipe_ids:
            raise ValueError(
                f"Dataset {dataset.id} has no recipe, so a task node needs an explicit recipe_id."
            )
        recipe_id = recipe_ids[0]
    title = spec.recipe_title
    if title is None:
        title = dataset.recipes.get(recipe_id=recipe_id).title or recipe_id
    return recipe_id, title


def build_node(pipeline: dl.entities.Pipeline, spec: NodeSpec) -> dl.entities.PipelineNode:
    """Turn a validated node spec into a dtlpy node entity.

    Pure for dataset and code nodes, so blueprints can derive an expected graph offline;
    `add_node` verifies the resources those nodes name before writing. Function and task nodes
    must fetch their service or dataset here, and verify it as they do.
    """
    project = pipeline.project
    if isinstance(spec, DatasetNodeSpec):
        data_filters = (
            dl.Filters(custom_filter=spec.data_filters, resource=dl.FiltersResource.ITEM)
            if spec.data_filters is not None
            else None
        )
        return dl.DatasetNode(
            name=spec.name,
            project_id=project.id,
            dataset_id=spec.dataset_id,
            dataset_folder=spec.dataset_folder,
            load_existing_data=spec.load_existing_data,
            data_filters=data_filters,
            position=spec.position,
        )
    if isinstance(spec, CodeNodeSpec):
        return code_node.build_code_node(
            name=spec.name,
            project_id=project.id,
            project_name=project.name,
            source=spec.code,
            position=spec.position,
        )
    if isinstance(spec, FunctionNodeSpec):
        service = owned(project, "services", "service", service_id=spec.service_id)
        return dl.FunctionNode(
            name=spec.name,
            service=service,
            function_name=spec.function_name,
            position=spec.position,
        )
    if isinstance(spec, TaskNodeSpec):
        dataset = owned(project, "datasets", "dataset", dataset_id=spec.dataset_id)
        recipe_id, recipe_title = _resolve_recipe(dataset, spec)
        return dl.TaskNode(
            name=spec.name,
            project_id=project.id,
            dataset_id=spec.dataset_id,
            recipe_id=recipe_id,
            recipe_title=recipe_title,
            task_owner=spec.task_owner,
            task_type=spec.task_type,
            workload=[
                dl.entities.WorkloadUnit(assignee_id=unit.assignee_id, load=unit.load)
                for unit in spec.workload
            ],
            position=spec.position,
        )
    raise ValueError(f"Unsupported node spec: {type(spec).__name__}")


def _require_node(pipeline: dl.entities.Pipeline, node_name: str) -> dl.entities.PipelineNode:
    node = pipeline.nodes.get(node_name=node_name)
    if node is None:
        available = ", ".join(n.name for n in pipeline.nodes) or "none"
        raise ValueError(f"Pipeline has no node named {node_name!r}. Existing nodes: {available}.")
    return node


def _require_port(
    node: dl.entities.PipelineNode, port_id: str | None, direction: str
) -> dl.entities.PipelineNodeIO:
    """Resolve a port id to the port object ``connect``/``disconnect`` expect."""
    ports = node.outputs if direction == "output" else node.inputs
    if not ports:
        raise ValueError(f"Node {node.name!r} has no {direction} ports.")
    if port_id is None:
        return ports[0]
    for port in ports:
        if port.port_id == port_id:
            return port
    known = ", ".join(p.port_id for p in ports)
    raise ValueError(f"Node {node.name!r} has no {direction} port {port_id!r}. Known ports: {known}.")


_ANY_ACTION = object()
"""Sentinel for "match this edge whatever its action" — `None` is itself a real action value."""


def _connection_matches(
    connection: Any,
    source_port_id: str,
    target_port_id: str,
    action: Any = _ANY_ACTION,
) -> bool:
    """Whether `connection` is the edge described by these ports and (optionally) this action.

    DOE models conditional routing as several connections off one output port differing only by
    `action`, so ports alone do not identify an edge. `pipeline_connect_nodes` matches on the action
    it was asked for — otherwise adding a second, differently-actioned edge silently no-ops and
    reports success. `pipeline_disconnect_nodes` takes no action argument and so matches any, which
    is what its "connections are identified by their endpoints" contract promises.
    """
    if connection.source.port_id != source_port_id or connection.target.port_id != target_port_id:
        return False
    return action is _ANY_ACTION or connection.action == action


def _stored_position(position: tuple[int, int]) -> tuple[float, float]:
    """What dtlpy actually stores when you assign `node.position = position`.

    `PipelineNode.position`'s setter is a **scaling transform, not a store**: it writes
    `x * 1.7 * NODE_SIZE[0] + NODE_SIZE[0] / 2` (and `y * 1.5 * NODE_SIZE[1] + NODE_SIZE[1]`), so
    reading the attribute back never returns the grid coordinates that were assigned, and assigning
    twice compounds the scale. Both matter for retry-until-visible: verification has to compare
    against the stored form, and a retry must not re-assign a position that already landed or the
    node drifts across the canvas. Mirrored here rather than round-tripped through a scratch node so
    the arithmetic is explicit, and pinned by
    `test_dtlpy_position_setter_scales_grid_coordinates` so an upstream change fails loudly.
    """
    width, height = dl.entities.node.NODE_SIZE
    return (position[0] * 1.7 * width + width / 2, position[1] * 1.5 * height + height)


def _require_endpoints(
    pipeline: dl.entities.Pipeline,
    source_node_name: str,
    target_node_name: str,
    source_port_id: str | None,
    target_port_id: str | None,
) -> tuple[Any, Any]:
    """Resolve both ends of an edge, raising the precise reason if either is missing."""
    source = _require_node(pipeline, source_node_name)
    target = _require_node(pipeline, target_node_name)
    return (
        _require_port(source, source_port_id, "output"),
        _require_port(target, target_port_id, "input"),
    )


def _connection_state(
    pipeline: dl.entities.Pipeline,
    source_node_name: str,
    target_node_name: str,
    source_port_id: str | None,
    target_port_id: str | None,
    action: Any = _ANY_ACTION,
) -> bool | None:
    """Whether this edge exists in `pipeline` — or `None` if this snapshot cannot answer.

    Returns `None` when either endpoint node or port is absent, because that is what a **stale
    read** looks like, not a caller error. `_require_node`/`_require_port` raise for the same
    condition, which is right for a fresh first read and wrong for a verification read: letting
    that exception escape `_retry_until_visible` aborts the retry the loop exists to perform, and
    reports "Pipeline has no node named X" about a node the call just used successfully.

    So: a verification predicate must treat `None` as "not visible yet" and let the loop retry,
    while a first-attempt validation should re-resolve strictly to raise the precise reason.
    """
    try:
        resolved_source, resolved_target = _require_endpoints(
            pipeline, source_node_name, target_node_name, source_port_id, target_port_id
        )
    except ValueError:
        return None
    return any(
        _connection_matches(c, resolved_source.port_id, resolved_target.port_id, action)
        for c in pipeline.connections
    )


def add_node(
    project_id: str,
    pipeline_id: str,
    spec: NodeSpec,
    references: tuple[tuple[str, str], ...] = (),
) -> outputs.PipelineNodeResult:
    """Add one built node to a pipeline, refusing a name already in the graph.

    Shared by ``pipeline_node_add`` and every ``rag_add_*_node`` tool in ``tools/rag.py``, so
    the retry-until-visible fix (see ``_retry_until_visible``) applies everywhere a node gets
    added, not just here.

    ``references`` names resources a generated code node will touch once installed, as
    ``(kind, id)`` pairs with kind ``"dataset"`` or ``"model"``. Each, and a dataset node's own
    dataset, must belong to the pipeline's project before anything is written.
    """
    if isinstance(spec, DatasetNodeSpec):
        references = (("dataset", spec.dataset_id), *references)

    # Built at most once and reused on every retry. `build_node` mints a fresh `node_id` per call,
    # so rebuilding would re-send the same node under a different id -- dangling any connection the
    # platform already held against the old one. It also avoids repeating build_node's own platform
    # calls (`services.get`/`datasets.get`) for function and task nodes.
    built: dl.entities.PipelineNode | None = None

    def mutate(pipeline: dl.entities.Pipeline, attempt: int) -> None:
        nonlocal built
        if pipeline.nodes.get(node_name=spec.name) is not None:
            if attempt == 1:
                raise ValueError(f"Pipeline already has a node named {spec.name!r}; pick another name.")
            return  # a previous attempt's write already landed; nothing left to do
        if built is None:
            for kind, resource_id in references:
                owned(pipeline.project, f"{kind}s", kind, **{f"{kind}_id": resource_id})
            built = build_node(pipeline, spec)
        pipeline.nodes.add(node=built)
        dataloop_call("pipeline graph update", pipeline.update)

    def verify(pipeline: dl.entities.Pipeline) -> bool:
        return pipeline.nodes.get(node_name=spec.name) is not None

    verified = _retry_until_visible(project_id, pipeline_id, mutate, verify)
    return {
        "pipeline": serialize.pipeline(verified),
        "node": serialize.node(_require_node(verified, spec.name)),
    }


def register(mcp: MCPServer) -> None:
    @mcp.tool(title="Add pipeline node", annotations=ADDITIVE)
    def pipeline_node_add(
        project_id: Annotated[str, Field(description="Project that owns the pipeline.")],
        pipeline_id: Annotated[str, Field(description="Pipeline to add the node to.")],
        node: Annotated[NodeSpec, Field(description="The node to add; shape depends on node_type.")],
        confirm_code: Annotated[
            bool, Field(description="Must be true when adding a code node that will run on DOE.")
        ] = False,
    ) -> outputs.PipelineNodeResult:
        """Add a node to a pipeline.

        The first node added becomes the pipeline's start node. Nodes are not connected
        automatically: use pipeline_connect_nodes afterwards.
        """
        if isinstance(node, CodeNodeSpec) and not confirm_code:
            raise ValueError("Refusing to add code node: set confirm_code=true after reviewing the code.")
        return add_node(project_id, pipeline_id, node)

    @mcp.tool(title="Update pipeline node", annotations=OVERWRITE)
    def pipeline_node_update(
        project_id: Annotated[str, Field(description="Project that owns the pipeline.")],
        pipeline_id: Annotated[str, Field(description="Pipeline containing the node.")],
        node_name: Annotated[str, Field(description="Current name of the node to change.")],
        new_name: Annotated[str | None, Field(description="Rename the node.")] = None,
        position: Annotated[
            tuple[int, int] | None, Field(description="Move the node to this (x, y) grid position.")
        ] = None,
        code: Annotated[
            str | None,
            Field(description="Replace a code node's function. Only valid for code nodes."),
        ] = None,
        confirm_code: Annotated[
            bool, Field(description="Must be true when replacing code that will run on DOE.")
        ] = False,
    ) -> outputs.PipelineNodeResult:
        """Rename, reposition, or replace the code of an existing node."""
        if new_name is None and position is None and code is None:
            raise ValueError("Provide new_name, position, or code: nothing to update otherwise.")
        # Validate syntax before touching the network: a bad function body shouldn't cost a
        # pipeline fetch and lock acquisition to discover, matching CodeNodeSpec's own
        # validate-before-any-platform-call behavior on pipeline_node_add.
        function_name = code_node.function_name_of(code) if code is not None else None
        final_name = new_name or node_name

        def target_node(pipeline: dl.entities.Pipeline) -> Any:
            """The node this call is editing, under whichever name it currently goes by.

            A rename that landed on an earlier attempt leaves it under `final_name`, so look there
            first — otherwise a retry would report the original name as missing.
            """
            return pipeline.nodes.get(node_name=final_name) or pipeline.nodes.get(node_name=node_name)

        def applied(node: Any) -> bool:
            return (
                node.name == final_name
                and (position is None or tuple(node.position) == _stored_position(position))
                and (function_name is None or node.namespace.function_name == function_name)
            )

        def mutate(pipeline: dl.entities.Pipeline, attempt: int) -> None:
            node = target_node(pipeline)
            if node is None:
                if attempt == 1:
                    _require_node(pipeline, node_name)  # raises with the "existing nodes" list
                return  # a later attempt reading a stale snapshot; the loop will re-read
            if applied(node):
                return  # a previous attempt's write already landed
            if code is not None:
                if node.node_type != dl.entities.PipelineNodeType.CODE:
                    raise ValueError(
                        f"Node {node_name!r} is a {node.node_type} node; code can only be set on a code node."
                    )
                if not confirm_code:
                    raise ValueError(
                        "Refusing to update code node: set confirm_code=true after reviewing the code."
                    )
                node.config = {
                    "package": {
                        "code": code_node.build_runner_code(code),
                        "name": function_name,
                        "type": "code",
                    }
                }
                node.namespace.function_name = function_name
            if position is not None:
                node.position = position
            if new_name is not None:
                node.name = new_name
            dataloop_call("pipeline graph update", pipeline.update)

        def verify(pipeline: dl.entities.Pipeline) -> bool:
            node = pipeline.nodes.get(node_name=final_name)
            return node is not None and applied(node)

        verified = _retry_until_visible(project_id, pipeline_id, mutate, verify)
        return {
            "pipeline": serialize.pipeline(verified),
            "node": serialize.node(_require_node(verified, final_name)),
        }

    @mcp.tool(title="Remove pipeline node", annotations=DELETE)
    def pipeline_node_remove(
        project_id: Annotated[str, Field(description="Project that owns the pipeline.")],
        pipeline_id: Annotated[str, Field(description="Pipeline containing the node.")],
        node_name: Annotated[
            str, Field(description="Name of the node to remove. dtlpy addresses nodes by name.")
        ],
        confirm: Annotated[
            bool, Field(description="Must be true to proceed. Removing a node also removes its edges.")
        ] = False,
        ctx: Context | None = None,
    ) -> outputs.PipelineNodeRemovalResult:
        """Remove a node, along with every connection attached to it."""
        confirm_or_elicit(ctx, confirm, "remove this node", "it also removes every edge touching it")

        def mutate(pipeline: dl.entities.Pipeline, attempt: int) -> None:
            if pipeline.nodes.get(node_name=node_name) is None:
                if attempt == 1:
                    _require_node(pipeline, node_name)  # raises with the "existing nodes" list
                return  # a previous attempt's removal already landed
            if not pipeline.nodes.remove(node_name):
                raise RuntimeError(f"Failed to remove node {node_name!r}.")
            dataloop_call("pipeline graph update", pipeline.update)

        def verify(pipeline: dl.entities.Pipeline) -> bool:
            return pipeline.nodes.get(node_name=node_name) is None

        verified = _retry_until_visible(project_id, pipeline_id, mutate, verify)
        return {"removed": True, "node_name": node_name, "pipeline": serialize.pipeline(verified)}

    @mcp.tool(title="Connect pipeline nodes", annotations=ADDITIVE)
    def pipeline_connect_nodes(
        project_id: Annotated[str, Field(description="Project that owns the pipeline.")],
        pipeline_id: Annotated[str, Field(description="Pipeline to wire up.")],
        source_node_name: Annotated[str, Field(description="Node the output comes from.")],
        target_node_name: Annotated[str, Field(description="Node the input goes to.")],
        source_port_id: Annotated[
            str | None, Field(description="Output port id. Defaults to the node's first output.")
        ] = None,
        target_port_id: Annotated[
            str | None, Field(description="Input port id. Defaults to the node's first input.")
        ] = None,
        action: Annotated[
            str | None,
            Field(description="Action that moves items along this edge, e.g. 'complete' or 'discard'."),
        ] = None,
    ) -> outputs.PipelineSummary:
        """Connect one node's output to another node's input.

        Call pipeline_get with include_graph=true to see node names and port ids.
        """

        def state(pipeline: dl.entities.Pipeline) -> bool | None:
            return _connection_state(
                pipeline, source_node_name, target_node_name, source_port_id, target_port_id, action
            )

        def mutate(pipeline: dl.entities.Pipeline, attempt: int) -> None:
            present = state(pipeline)
            if present is None:
                if attempt == 1:
                    # Fresh first read: a node or port really is missing, so re-resolve strictly
                    # to raise the precise reason. On a later attempt the same shape means a stale
                    # read, and the loop should just try again.
                    _require_endpoints(
                        pipeline, source_node_name, target_node_name, source_port_id, target_port_id
                    )
                return
            if present:
                return  # already connected -- our own previous attempt, or a genuine repeat call
            source = _require_node(pipeline, source_node_name)
            target = _require_node(pipeline, target_node_name)
            source.connect(
                node=target,
                source_port=_require_port(source, source_port_id, "output"),
                target_port=_require_port(target, target_port_id, "input"),
                action=action,
            )
            dataloop_call("pipeline graph update", pipeline.update)

        verified = _retry_until_visible(project_id, pipeline_id, mutate, lambda p: state(p) is True)
        return serialize.pipeline(verified, include_graph=True)

    @mcp.tool(title="Disconnect pipeline nodes", annotations=DELETE)
    def pipeline_disconnect_nodes(
        project_id: Annotated[str, Field(description="Project that owns the pipeline.")],
        pipeline_id: Annotated[str, Field(description="Pipeline to edit.")],
        source_node_name: Annotated[str, Field(description="Node the connection starts at.")],
        target_node_name: Annotated[str, Field(description="Node the connection ends at.")],
        source_port_id: Annotated[
            str | None, Field(description="Output port id. Defaults to the node's first output.")
        ] = None,
        target_port_id: Annotated[
            str | None, Field(description="Input port id. Defaults to the node's first input.")
        ] = None,
        confirm: Annotated[
            bool, Field(description="Must be true to proceed. Disconnecting nodes changes pipeline behavior.")
        ] = False,
        ctx: Context | None = None,
    ) -> outputs.PipelineSummary:
        """Remove the connection between two nodes.

        Connections have no id in DOE, so they are identified by their endpoints.
        """
        confirm_or_elicit(
            ctx, confirm, "disconnect these nodes", "it changes how items flow through the pipeline"
        )

        def state(pipeline: dl.entities.Pipeline) -> bool | None:
            return _connection_state(
                pipeline, source_node_name, target_node_name, source_port_id, target_port_id
            )

        def mutate(pipeline: dl.entities.Pipeline, attempt: int) -> None:
            present = state(pipeline)
            if present is None:
                if attempt == 1:
                    # See pipeline_connect_nodes: strict re-resolution only on the fresh first read.
                    _require_endpoints(
                        pipeline, source_node_name, target_node_name, source_port_id, target_port_id
                    )
                return
            if not present:
                if attempt == 1:
                    raise ValueError(
                        f"No connection found from {source_node_name!r} to {target_node_name!r} "
                        "on those ports."
                    )
                return  # a previous attempt's removal already landed
            source = _require_node(pipeline, source_node_name)
            target = _require_node(pipeline, target_node_name)
            source.disconnect(
                node=target,
                source_port=_require_port(source, source_port_id, "output"),
                target_port=_require_port(target, target_port_id, "input"),
            )
            dataloop_call("pipeline graph update", pipeline.update)

        verified = _retry_until_visible(project_id, pipeline_id, mutate, lambda p: state(p) is False)
        return serialize.pipeline(verified, include_graph=True)

    @mcp.resource(
        "dataloop://projects/{project_id}/pipelines/{pipeline_id}/graph",
        title="Pipeline graph",
        description="Full pipeline detail including nodes and connections — the same body "
        "pipeline_get(include_graph=true) returns, addressable outside a tool call so a large "
        "graph does not have to round-trip through the conversation.",
        mime_type="application/json",
    )
    def pipeline_graph_resource(project_id: str, pipeline_id: str) -> outputs.PipelineSummary:
        return serialize.pipeline(get_pipeline(project_id, pipeline_id), include_graph=True)
