"""Deterministic pipeline construction from a resolved blueprint.

**Prototype.** Not registered as a tool yet — see the note at the bottom of this docstring.

When a user declines marketplace templates and an agent builds the pipeline itself, the
construction must be deterministic: the same blueprint produces the same graph, every run. The
only things allowed to vary are the user's own gated choices — which dataset, which model, what
code, what node settings — and those are inputs *to* the blueprint, pinned before construction
starts.

**What was actually non-deterministic.** Measured before writing this, by constructing the same
three-node graph twice through the per-node path and diffing `to_json()`: 18 fields differed, and
every one was a uuid4 `nodeId` or `portId`. Node types, config, code, positions and edge structure
were already stable. So identifier minting was the whole problem, and deriving ids from the
blueprint's content solves it.

Deriving them buys three things beyond reproducibility:

- **Idempotent re-runs.** The same blueprint re-applied targets the same node ids, so it converges
  instead of accumulating duplicates.
- **Diffability.** Two runs, or a run against the platform's current state, compare directly.
- **Referential integrity across a rebuild.** A connection recorded against a node id still means
  the same node after the graph is rebuilt from the blueprint.

**One write, not one per node.** `build_graph` assembles the whole graph in memory and PATCHes
once. `Pipelines.update` sends the entire node list every time (techspec A.4), so each extra call
is another lost-update window — eleven of them for a five-node RAG chain with four edges. A lost
update means the same blueprint produced a different graph, so collapsing those windows is part of
the determinism property, not merely a speed optimisation. It does not *close* the race: a
concurrent writer can still clobber the single PATCH, which needs a precondition dtlpy does not
offer.

**What this cannot make deterministic**, and the spec should say so rather than implying otherwise:
platform state (which datasets and models exist), data-dependent results, DOE-side model
inference, and the discovery/disambiguation that decides what goes *into* the blueprint. The
boundary is the point where the user's choices are fixed.

**Not yet a tool.** Registering one costs advertised schema and the surface has ~4 tok of headroom
against its ceiling. The intended route is a `--profile skills` that advertises composite builders
*instead of* the fine-grained node tools, which is smaller than today's surface rather than larger
— but that is a Phase 3/4 decision this prototype exists to inform, not to pre-empt.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

import dtlpy as dl
from pydantic import BaseModel, Field, model_validator

from .schemas import NodeSpec

#: Namespace for derived identifiers. A fixed, arbitrary UUID: uuid5 needs a namespace, and using
#: a constant one means ids depend on the blueprint alone. Never change it — doing so re-points
#: every future build away from the nodes existing pipelines were built with.
NAMESPACE = uuid.UUID("6f0c7c6e-3a9b-4f52-9a4a-6a1a7c1d2e30")


@dataclass(frozen=True)
class Edge:
    """One directed connection, addressed by node *name* rather than by id.

    Names are the blueprint's stable addressing scheme: ids are derived from the blueprint, so a
    blueprint cannot refer to them before it exists.
    """

    source: str
    target: str
    action: str | None = None


class PipelineBlueprint(BaseModel):
    """A fully-resolved description of a graph: every user choice already made."""

    name: str
    nodes: list[NodeSpec] = Field(min_length=1)
    edges: list[Edge] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}

    @model_validator(mode="after")
    def _names_are_unique(self) -> PipelineBlueprint:
        seen = set()
        for node in self.nodes:
            if node.name in seen:
                raise ValueError(
                    f"duplicate node name {node.name!r}: names address nodes within a blueprint, "
                    "so they have to be unique."
                )
            seen.add(node.name)
        return self


def fingerprint(plan: PipelineBlueprint) -> str:
    """A stable digest of every choice in the blueprint.

    Two blueprints that would build the same graph share a fingerprint; any difference in a
    dataset id, a code body, a position or an edge changes it. Sorted keys and a compact separator
    so dict ordering cannot leak in.
    """
    payload = {
        "name": plan.name,
        "nodes": [node.model_dump(mode="json") for node in plan.nodes],
        "edges": [[edge.source, edge.target, edge.action] for edge in plan.edges],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _derived_id(*parts: str) -> str:
    """A UUID derived from the blueprint, in place of dtlpy's uuid4.

    A full 36-character UUID string, the same shape dtlpy emits (`str(uuid.uuid4())`) — pinned by
    `test_derived_ids_have_the_same_shape_dtlpy_emits`, because a subtly different width would be
    accepted locally and rejected, or silently mangled, by the platform.
    """
    return str(uuid.uuid5(NAMESPACE, ":".join(parts)))


def _apply_derived_ids(node: Any, digest: str) -> None:
    """Replace dtlpy's randomly minted node and port ids with derived ones.

    Ports are keyed by direction and position rather than by their own name: a node can carry two
    ports with the same name in different directions, and `name` is not required to be unique
    within a direction either.
    """
    node.node_id = _derived_id(digest, node.name, "node")
    for direction in ("inputs", "outputs"):
        for index, port in enumerate(getattr(node, direction, None) or []):
            port.port_id = _derived_id(digest, node.name, direction, str(index))


def build_graph(
    pipeline: dl.entities.Pipeline, plan: PipelineBlueprint, *, write: bool = False
) -> dl.entities.Pipeline:
    """Construct `plan` into `pipeline`, deterministically. Writes once, only if asked.

    Separating construction from the write lets a caller diff the result against the platform's
    current graph before committing to it — which is the point of a deterministic build.
    """
    from .tools.pipeline_nodes import build_node

    digest = fingerprint(plan)
    built: dict[str, Any] = {}

    for spec in plan.nodes:
        node = build_node(pipeline, spec)
        _apply_derived_ids(node, digest)
        built[spec.name] = node

    # Added in blueprint order, so the first node listed becomes the start node (Nodes.add sets it
    # when start_nodes is empty) rather than whichever happened to be built first.
    for spec in plan.nodes:
        pipeline.nodes.add(node=built[spec.name])

    for index, edge in enumerate(plan.edges, start=1):
        for role, name in (("sources", edge.source), ("targets", edge.target)):
            if name not in built:
                raise ValueError(
                    f"edge {index} {role[:-1]}s unknown node {name!r}. Blueprint nodes: {', '.join(built)}."
                )
        built[edge.source].connect(node=built[edge.target], action=edge.action)

    if write:
        pipeline.update()
    return pipeline


def canonical_graph(pipeline: dl.entities.Pipeline) -> dict[str, Any]:
    """A comparable form of a graph: structure and settings, without platform-assigned noise.

    Ids are omitted even though they are derived and therefore stable — a graph read back from the
    platform carries fields this process never sent, and the question a caller is asking is "is
    this the same configuration", not "is this the same JSON".
    """
    by_id = {node.node_id: node.name for node in pipeline.nodes}
    return {
        "nodes": [
            {
                "name": node.name,
                "type": str(node.node_type),
                "position": tuple(node.position),
                "config": node.config,
            }
            for node in pipeline.nodes
        ],
        "edges": [
            (by_id.get(c.source.node_id), by_id.get(c.target.node_id), c.action) for c in pipeline.connections
        ],
        # Mapped to names like everything else. Leaving raw ids here defeated the whole point:
        # two graphs with identical structure compared unequal purely because their start-node
        # ids differed, which is exactly the platform-assigned noise this form exists to drop.
        "start_nodes": [by_id.get(n.get("nodeId")) for n in (pipeline.start_nodes or [])],
    }
