# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Skills: whole pipelines built in one deterministic pass from resolved user choices.

**Prototype, for measurement.** Registered only under `--profile skills`, deliberately not in
`ALL_MODULES` — see the note at the end.

A "skill" here is the counterpart to a marketplace template for the case where the user declines
templates and the agent has to construct the pipeline itself. The plan's requirement is that this
construction be *deterministic*: the same choices produce the same graph, every run. The only
things allowed to vary are the user's own gated choices — which dataset, which model, what code,
what node settings — and a skill takes those as arguments, so they are pinned before construction
starts.

Two differences from the per-node tools this replaces, both load-bearing:

- **Derived identifiers.** `doe_mcp.blueprint` mints node and port ids from a digest of the
  blueprint rather than letting dtlpy generate uuid4s. Measured on the three-node case, that was
  the *only* source of construction non-determinism: 18 fields differed between two runs and every
  one was a `nodeId` or `portId`.
- **One write.** The graph is assembled in memory and PATCHed once, instead of once per node and
  once per edge. `Pipelines.update` sends the entire node list (techspec A.4), so each extra call
  is another lost-update window — eleven of them for the five-node RAG chain. A lost update means
  the same choices produced a different graph, so this is part of the determinism property, not
  just a speed one. It does not *close* the race, which needs a precondition dtlpy does not offer.

**Binding condition: reuse before building.** Before constructing anything, `apply_blueprint`
looks up a pipeline already named for this blueprint and, if one exists, compares its live
`canonical_graph` against `blueprint.expected_canonical_graph` -- what these exact choices would
build. A match is adopted as-is; a same name with a different graph, or no existing pipeline,
falls through to building from scratch. A skill must never construct a duplicate of a pipeline
that already has the correct structure.

**Not in `ALL_MODULES`, on purpose.** A skill is meant to be an *alternative* to the fine-grained
node tools, not an addition: a session that has `rag_pipeline_build` does not also need six
`rag_add_*_node` tools. Registering it everywhere would grow the default surface past its ceiling
while making that surface strictly more confusing. Whether the `skills` profile should eventually
*replace* `rag` is the question this prototype exists to inform.
"""

from typing import Annotated, Any

import dtlpy as dl
from mcp.server import MCPServer
from pydantic import Field

from .. import serialize
from ..blueprint import (
    Edge,
    PipelineBlueprint,
    build_graph,
    canonical_graph,
    expected_canonical_graph,
    fingerprint,
)
from ..context import DoeError, dataloop_call, get_pipeline, get_project, owned
from ..guards import confirm_or_elicit
from ..routing import CachingRouter, LexicalRouter, catalogue_from
from ..safety import IDEMPOTENT_WRITE, READ_ONLY
from ..schemas import CodeNodeSpec, DatasetNodeSpec
from .rag import (
    CHUNK_FOLDER,
    EMBED_BATCH_SIZE,
    EMBEDDING_CODE,
    PDF_TO_TEXT_CODE,
    TEXT_FOLDER,
    TEXT_TO_CHUNKS_CODE,
    python_string_literal,
)

#: Process-wide, so the same request routes the same way for the life of the server. See
#: `doe_mcp.routing` on why caching is where determinism is actually *added* here.
_ROUTER = CachingRouter(LexicalRouter())


def rag_blueprint(
    *,
    name: str,
    source_dataset_id: str,
    output_dataset_id: str,
    model_id: str,
    chunk_size: int,
    chunk_overlap: int,
    load_existing: bool,
) -> PipelineBlueprint:
    """The five-stage RAG chain, as data: PDFs -> text -> chunks -> embeddings -> output.

    Every argument is a resolved user choice. Same arguments in, same graph out — which is the
    whole point, and is what `tests/test_skills.py` asserts against a pinned fingerprint.

    Node names, positions, DQL filter and code templates match what the `rag_add_*_node` tools
    produce one at a time, so this is a port rather than a second, subtly different RAG shape.
    """
    pdf_filter = {"filter": {"$and": [{"metadata.system.mimetype": {"$eq": "application/pdf"}}]}}
    to_text = PDF_TO_TEXT_CODE.replace(
        "__OUTPUT_DATASET_ID_LITERAL__", python_string_literal(output_dataset_id)
    ).replace("__TEXT_FOLDER__", TEXT_FOLDER)
    to_chunks = (
        TEXT_TO_CHUNKS_CODE.replace("__OUTPUT_DATASET_ID_LITERAL__", python_string_literal(output_dataset_id))
        .replace("__CHUNK_FOLDER__", CHUNK_FOLDER)
        .replace("__CHUNK_SIZE__", str(chunk_size))
        .replace("__CHUNK_OVERLAP__", str(chunk_overlap))
    )
    embed = (
        EMBEDDING_CODE.replace("__OUTPUT_DATASET_ID_LITERAL__", python_string_literal(output_dataset_id))
        .replace("__MODEL_ID_LITERAL__", python_string_literal(model_id))
        .replace("__BATCH_SIZE__", str(EMBED_BATCH_SIZE))
    )
    return PipelineBlueprint(
        name=name,
        nodes=[
            DatasetNodeSpec(
                node_type="dataset",
                name="pdf_filter",
                dataset_id=source_dataset_id,
                load_existing_data=load_existing,
                data_filters=pdf_filter,
                position=(1, 1),
            ),
            CodeNodeSpec(node_type="code", name="pdf_to_text", code=to_text, position=(2, 1)),
            CodeNodeSpec(node_type="code", name="text_to_chunks", code=to_chunks, position=(3, 1)),
            CodeNodeSpec(node_type="code", name="generate_embeddings", code=embed, position=(4, 1)),
            DatasetNodeSpec(
                node_type="dataset",
                name="output_storage",
                dataset_id=output_dataset_id,
                position=(5, 1),
            ),
        ],
        edges=[
            Edge(source="pdf_filter", target="pdf_to_text"),
            Edge(source="pdf_to_text", target="text_to_chunks"),
            Edge(source="text_to_chunks", target="generate_embeddings"),
            Edge(source="generate_embeddings", target="output_storage"),
        ],
    )


def reusable_pipeline(
    project: dl.entities.Project, project_id: str, plan: PipelineBlueprint
) -> dict[str, Any] | None:
    """A same-named pipeline this blueprint's choices already match, if one exists.

    Binding condition: a skill must not build a duplicate of a pipeline that is already correct.
    Existence of the name alone is not enough -- a same-named pipeline left over from a different
    set of choices should not be adopted silently -- so this compares `canonical_graph` of the
    live pipeline against what `plan` would build, not just the name. Returns None on any
    mismatch or absence, which tells the caller to build from scratch as before.
    """
    # Not routed through `dataloop_call`: that normalizes every non-`ValueError` failure into a
    # `RuntimeError`, which would swallow the `NotFound` this depends on distinguishing from a
    # genuine failure (auth, network, a name collision `Pipelines.get` itself refuses to resolve).
    try:
        existing = project.pipelines.get(pipeline_name=plan.name)
    except dl.exceptions.NotFound:
        return None
    except Exception as exc:
        # DoeError, not a bare RuntimeError: this message is hand-built in the same shape
        # dataloop_call produces, so it must also be recognised as already-normalized if it ever
        # ends up inside one -- otherwise it would come back double-prefixed.
        raise DoeError(f"DOE pipeline lookup by name failed: {exc}") from exc

    verified = get_pipeline(project_id, existing.id)
    live_graph = canonical_graph(verified)
    if live_graph != expected_canonical_graph(project, plan):
        return None
    return {
        "pipeline": serialize.pipeline(verified, include_graph=True),
        "blueprint_fingerprint": fingerprint(plan),
        "canonical_graph": live_graph,
        "reused_existing_pipeline": True,
    }


def apply_blueprint(project_id: str, plan: PipelineBlueprint) -> dict[str, Any]:
    """Reuse a matching same-named pipeline; otherwise create one and write the graph in one PATCH.

    A fresh build is always verified by an independent re-read, never `update()`'s own return
    value: that echoes back what this process sent rather than what the platform now holds
    (techspec A.4).
    """
    project = get_project(project_id)
    reused = reusable_pipeline(project, project_id, plan)
    if reused is not None:
        return reused

    created = dataloop_call(
        "pipeline create", lambda: project.pipelines.create(name=plan.name, project_id=project_id)
    )
    pipeline = get_pipeline(project_id, created.id)
    build_graph(pipeline, plan)
    dataloop_call("pipeline graph write", pipeline.update)

    verified = get_pipeline(project_id, created.id)
    built = canonical_graph(verified)
    expected = {node.name for node in plan.nodes}
    missing = sorted(expected - {node["name"] for node in built["nodes"]})
    if missing:
        raise RuntimeError(
            f"Pipeline {created.id} is missing node(s) {missing} on an independent re-read after "
            "writing. The platform may be slow to propagate right now; call "
            "pipeline_get(include_graph=true) to check before assuming nothing happened."
        )
    return {
        "pipeline": serialize.pipeline(verified, include_graph=True),
        "blueprint_fingerprint": fingerprint(plan),
        "canonical_graph": built,
        "reused_existing_pipeline": False,
    }


def _tool_catalogue(mcp: MCPServer) -> list[Any]:
    """The tools this server advertises, read back from the live registry.

    Read from the server rather than a hand-kept list so routing can never drift from what is
    actually advertised -- a router that suggests a tool the session does not carry is worse than
    no router.
    """
    import anyio.from_thread

    return list(anyio.from_thread.run(mcp.list_tools))


def register(mcp: MCPServer) -> None:
    @mcp.tool(title="Build a RAG pipeline in one deterministic pass", annotations=IDEMPOTENT_WRITE)
    def rag_pipeline_build(
        project_id: Annotated[str, Field(description="Project to create the pipeline in.")],
        name: Annotated[str, Field(min_length=1, description="Name for the RAG pipeline.")],
        source_dataset_id: Annotated[str, Field(description="Dataset of PDFs to process.")],
        output_dataset_id: Annotated[
            str, Field(description="Dataset for text and chunk items; not the source dataset.")
        ],
        model_id: Annotated[str, Field(description="A deployed embedding model; model_list finds one.")],
        chunk_size: Annotated[int, Field(ge=100, le=4000, description="Chunk size in characters.")] = 512,
        chunk_overlap: Annotated[int, Field(ge=0, le=500, description="Chunk overlap.")] = 50,
        load_existing: Annotated[
            bool, Field(description="Feed existing PDFs in on install, not just new ones.")
        ] = False,
        confirm: Annotated[bool, Field(description="Must be true: this creates and deploys code.")] = False,
        ctx: Any = None,
    ) -> dict[str, Any]:
        """Build the whole PDFs->text->chunks->embeddings pipeline in one call.

        Deterministic: the same arguments always produce the same graph, node ids included.
        Returns the graph plus a fingerprint of the choices it was built from.
        """
        confirm_or_elicit(
            ctx, confirm, "build this RAG pipeline", "it creates a pipeline and deploys code nodes"
        )
        project = get_project(project_id)
        owned(project, "datasets", "dataset", dataset_id=source_dataset_id)
        owned(project, "datasets", "dataset", dataset_id=output_dataset_id)
        owned(project, "models", "model", model_id=model_id)
        plan = rag_blueprint(
            name=name,
            source_dataset_id=source_dataset_id,
            output_dataset_id=output_dataset_id,
            model_id=model_id,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            load_existing=load_existing,
        )
        return apply_blueprint(project_id, plan)

    @mcp.tool(title="Match a request to the tool or skill that serves it", annotations=READ_ONLY)
    def intent_route(
        request: Annotated[str, Field(min_length=1, description="What the user asked, in their words.")],
        limit: Annotated[int, Field(ge=1, le=10, description="How many candidates to return.")] = 5,
    ) -> dict[str, Any]:
        """Suggest which tool or skill handles a request. Proposes only -- runs nothing.

        Returns ranked candidates and whether the top one is a clear winner. When it is not,
        ask the user rather than guessing.
        """
        catalogue = catalogue_from(_tool_catalogue(mcp))
        route = _ROUTER.route(request, catalogue, limit)
        return {
            "request": request,
            "router": route.router,
            "confident": route.confident,
            "candidates": [{"tool": c.name, "score": round(c.score, 4)} for c in route.candidates],
            "next_step": (
                f"Call {route.best.name} once its arguments are settled."
                if route.confident and route.best
                else "No clear winner: show the candidates and ask which was meant."
            ),
        }
