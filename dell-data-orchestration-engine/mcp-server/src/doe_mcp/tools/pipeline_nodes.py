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
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

import dtlpy as dl
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from .. import code_node, outputs, serialize
from ..context import get_pipeline
from ..guards import confirm_or_elicit
from ..schemas import (
    CodeNodeSpec,
    DatasetNodeSpec,
    FunctionNodeSpec,
    NodeSpec,
    TaskNodeSpec,
)

MUTATING = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False
)
_locks_guard = threading.Lock()
_pipeline_locks: dict[str, threading.Lock] = {}


@contextmanager
def pipeline_lock(pipeline_id: str) -> Iterator[None]:
    with _locks_guard:
        lock = _pipeline_locks.setdefault(pipeline_id, threading.Lock())
    with lock:
        yield


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
    """Turn a validated node spec into a dtlpy node entity."""
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
        service = project.services.get(service_id=spec.service_id)
        return dl.FunctionNode(
            name=spec.name,
            service=service,
            function_name=spec.function_name,
            position=spec.position,
        )
    if isinstance(spec, TaskNodeSpec):
        dataset = project.datasets.get(dataset_id=spec.dataset_id)
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


def register(mcp: MCPServer) -> None:
    @mcp.tool(title="Add pipeline node", annotations=MUTATING)
    def pipeline_node_add(
        project_id: Annotated[str, Field(description="Project that owns the pipeline.")],
        pipeline_id: Annotated[str, Field(description="Pipeline to add the node to.")],
        node: Annotated[NodeSpec, Field(description="The node to add; shape depends on node_type.")],
        confirm_code: Annotated[
            bool, Field(description="Must be true when adding a code node that will run on Dataloop.")
        ] = False,
    ) -> dict[str, Any]:
        """Add a node to a pipeline.

        The first node added becomes the pipeline's start node. Nodes are not connected
        automatically: use pipeline_connect_nodes afterwards.
        """
        if isinstance(node, CodeNodeSpec) and not confirm_code:
            raise ValueError("Refusing to add code node: set confirm_code=true after reviewing the code.")
        with pipeline_lock(pipeline_id):
            pipeline = get_pipeline(project_id, pipeline_id)
            if pipeline.nodes.get(node_name=node.name) is not None:
                raise ValueError(f"Pipeline already has a node named {node.name!r}; pick another name.")
            built = build_node(pipeline, node)
            pipeline.nodes.add(node=built)
            updated = pipeline.update()
            return {
                "pipeline": serialize.pipeline(updated),
                "node": serialize.node(_require_node(updated, node.name)),
            }

    @mcp.tool(title="Update pipeline node", annotations=MUTATING)
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
            bool, Field(description="Must be true when replacing code that will run on Dataloop.")
        ] = False,
    ) -> dict[str, Any]:
        """Rename, reposition, or replace the code of an existing node."""
        if new_name is None and position is None and code is None:
            raise ValueError("Provide new_name, position, or code: nothing to update otherwise.")
        with pipeline_lock(pipeline_id):
            pipeline = get_pipeline(project_id, pipeline_id)
            node = _require_node(pipeline, node_name)

            if code is not None:
                if node.node_type != dl.entities.PipelineNodeType.CODE:
                    raise ValueError(
                        f"Node {node_name!r} is a {node.node_type} node; code can only be set on a code node."
                    )
                if not confirm_code:
                    raise ValueError(
                        "Refusing to update code node: set confirm_code=true after reviewing the code."
                    )
                function_name = code_node.function_name_of(code)
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

            updated = pipeline.update()
            return {
                "pipeline": serialize.pipeline(updated),
                "node": serialize.node(_require_node(updated, new_name or node_name)),
            }

    @mcp.tool(title="Remove pipeline node", annotations=DESTRUCTIVE)
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
    ) -> dict[str, Any]:
        """Remove a node, along with every connection attached to it."""
        confirm_or_elicit(ctx, confirm, "remove this node", "it also removes every edge touching it")
        with pipeline_lock(pipeline_id):
            pipeline = get_pipeline(project_id, pipeline_id)
            _require_node(pipeline, node_name)
            if not pipeline.nodes.remove(node_name):
                raise RuntimeError(f"Failed to remove node {node_name!r}.")
            updated = pipeline.update()
            return {"removed": True, "node_name": node_name, "pipeline": serialize.pipeline(updated)}

    @mcp.tool(title="Connect pipeline nodes", annotations=MUTATING)
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
        with pipeline_lock(pipeline_id):
            pipeline = get_pipeline(project_id, pipeline_id)
            source = _require_node(pipeline, source_node_name)
            target = _require_node(pipeline, target_node_name)
            source.connect(
                node=target,
                source_port=_require_port(source, source_port_id, "output"),
                target_port=_require_port(target, target_port_id, "input"),
                action=action,
            )
            updated = pipeline.update()
            return serialize.pipeline(updated, include_graph=True)

    @mcp.tool(title="Disconnect pipeline nodes", annotations=DESTRUCTIVE)
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
    ) -> dict[str, Any]:
        """Remove the connection between two nodes.

        Connections have no id in Dataloop, so they are identified by their endpoints.
        """
        confirm_or_elicit(
            ctx, confirm, "disconnect these nodes", "it changes how items flow through the pipeline"
        )
        with pipeline_lock(pipeline_id):
            pipeline = get_pipeline(project_id, pipeline_id)
            source = _require_node(pipeline, source_node_name)
            target = _require_node(pipeline, target_node_name)
            removed = source.disconnect(
                node=target,
                source_port=_require_port(source, source_port_id, "output"),
                target_port=_require_port(target, target_port_id, "input"),
            )
            if not removed:
                raise ValueError(
                    f"No connection found from {source_node_name!r} to {target_node_name!r} on those ports."
                )
            updated = pipeline.update()
            return serialize.pipeline(updated, include_graph=True)

    @mcp.resource(
        "dataloop://projects/{project_id}/pipelines/{pipeline_id}/graph",
        title="Pipeline graph",
        description="Full pipeline detail including nodes and connections — the same body "
        "pipeline_get(include_graph=true) returns, addressable outside a tool call so a large "
        "graph does not have to round-trip through the conversation.",
        mime_type="application/json",
    )
    def pipeline_graph_resource(project_id: str, pipeline_id: str) -> dict[str, Any]:
        return serialize.pipeline(get_pipeline(project_id, pipeline_id), include_graph=True)
