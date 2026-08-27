"""RAG (Retrieval-Augmented Generation) pipeline tools.

This module provides tools to create and manage RAG pipelines that:
1. Filter PDF files using DQL queries
2. Extract text from PDFs into text items
3. Split each text item into chunk items in the output dataset
4. Embed those chunk items with a model installed in the project
5. Enable semantic search over the chunks

Two storage decisions shape everything here.

**Chunks are items, not metadata.** A feature vector attaches to a platform *entity* — item,
annotation or dataset (``dl.FeatureEntityType``) — so chunk-level retrieval is only possible if
each chunk is an item of its own. Chunk items also keep the whole document out of an item's
metadata document, which has no room for it.

**Embeddings are feature sets, never metadata.** Vectors are written by the model service that
produced them, into its own feature set; nothing in this module copies a vector into
``metadata.user``. Metadata carries provenance only — which document a chunk came from, and
where in it — which is what the DQL queries below filter on. A vector in metadata would be
invisible to the platform's Clustering app and to ``dataset.features``, and would bloat every
item read.

Everything expensive runs on DOE: extraction and chunking in code nodes, embedding in the
model's own service. This process only ranks vectors at query time, because `dtlpy` exposes no
vector-distance query to delegate to (see tools/embeddings.py).
"""

import io
import time
import uuid
from typing import Annotated, Any

import dtlpy as dl
import numpy as np
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from .. import outputs, paging, serialize
from ..context import dataloop_call, get_dataset, get_project
from ..guards import gate_local_compute
from ..schemas import CodeNodeSpec, DatasetNodeSpec
from . import pipeline_nodes
from .embeddings import cosine_similarity, read_vectors

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
MUTATING = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)

# Where derived items live in the output dataset. Fixed rather than configurable so the DQL in
# the embedding node and the provenance the query tool reports describe the same layout.
TEXT_FOLDER = "/rag/text"
CHUNK_FOLDER = "/rag/chunks"
QUERY_FOLDER = "/rag/queries"

# The model's embed endpoint takes a list of item ids; chunk items are fed to it in batches so a
# long document does not turn into one enormous request.
EMBED_BATCH_SIZE = 100

# A query is embedded by the same model that embedded the chunks — the only way to get a vector
# comparable with them — which means uploading it, waiting for the service, then reading the
# vector back. The wait is bounded so a stalled service surfaces as an error, not a hang.
QUERY_POLL_ATTEMPTS = 30
QUERY_POLL_SECONDS = 2.0

# Text pulled back for the chunks that actually rank, so an answer can quote them.
MAX_CHUNK_TEXT_CHARS = 4000


PDF_TO_TEXT_CODE = r'''def run(item):
    """Extract text from a PDF and store it as a text item in the RAG output dataset."""
    import io
    import re
    import zlib

    import dtlpy as dl

    text = ""
    try:
        import pypdf
        buffer = item.download(save_locally=False)
        stream = io.BytesIO(buffer) if isinstance(buffer, bytes) else buffer
        reader = pypdf.PdfReader(stream)
        pages_text = [page.extract_text() or "" for page in reader.pages]
        text = "\n\n".join(pages_text).strip()
    except Exception:
        try:
            import PyPDF2
            buffer = item.download(save_locally=False)
            stream = io.BytesIO(buffer) if isinstance(buffer, bytes) else buffer
            reader = PyPDF2.PdfReader(stream)
            pages_text = [page.extract_text() or "" for page in reader.pages]
            text = "\n\n".join(pages_text).strip()
        except Exception:
            # Pure python stream text extractor fallback
            buffer = item.download(save_locally=False)
            if hasattr(buffer, "getvalue"):
                raw = buffer.getvalue()
            elif isinstance(buffer, bytes):
                raw = buffer
            else:
                raw = bytes(buffer)
            text_chunks = []
            streams = re.findall(rb"stream[\r\n]+(.*?)[\r\n]+endstream", raw, re.DOTALL)
            for s in streams:
                decompressed = None
                try:
                    decompressed = zlib.decompress(s)
                except Exception:
                    try:
                        decompressed = zlib.decompress(s, -15)
                    except Exception:
                        decompressed = s

                if decompressed:
                    tj_matches = re.findall(rb"\((.*?)\)\s*Tj", decompressed)
                    for m in tj_matches:
                        try:
                            text_chunks.append(m.decode("latin1", errors="ignore"))
                        except Exception:
                            pass
                    array_matches = re.findall(rb"\[(.*?)\]\s*TJ", decompressed)
                    for arr in array_matches:
                        strs = re.findall(rb"\((.*?)\)", arr)
                        for s_part in strs:
                            try:
                                text_chunks.append(s_part.decode("latin1", errors="ignore"))
                            except Exception:
                                pass
            full_text = " ".join(text_chunks)
            text = re.sub(r"\s+", " ", full_text).strip()

    if not text:
        # Failing loudly beats writing an empty text item that later nodes would chunk into
        # nothing and embed as nothing.
        raise ValueError("No text could be extracted from PDF item {}".format(item.id))

    # item.project is unreliable here: the item JSON a pipeline code node receives can omit
    # projectId, leaving item.project None (verified live). dl.datasets.get needs no project
    # context to fetch by id. (Note: keep this comment ASCII-only -- a non-ASCII character
    # here previously caused the platform to silently truncate the packaged code by a couple
    # of bytes, corrupting the trailing statement.)
    output_dataset = dl.datasets.get(dataset_id="__OUTPUT_DATASET_ID__")
    buffer = io.BytesIO(text.encode("utf-8"))
    base_name = item.name.rsplit(".", 1)[0]
    text_item = output_dataset.items.upload(
        local_path=buffer,
        remote_path="__TEXT_FOLDER__",
        remote_name="{}.txt".format(base_name),
        overwrite=True,
        item_metadata={
            "user": {
                "rag": {
                    "role": "text",
                    "source_item_id": item.id,
                    "source_item_name": item.name,
                    "char_count": len(text),
                }
            }
        },
    )
    return text_item
'''


TEXT_TO_CHUNKS_CODE = r'''def run(item):
    """Split a text item into overlapping chunk items in the RAG output dataset."""
    import io

    import dtlpy as dl

    chunk_size = __CHUNK_SIZE__
    chunk_overlap = __CHUNK_OVERLAP__

    buffer = item.download(save_locally=False)
    raw = buffer.getvalue() if hasattr(buffer, "getvalue") else buffer
    text = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw)
    if not text.strip():
        raise ValueError("Text item {} is empty; nothing to chunk.".format(item.id))

    rag_metadata = (item.metadata or {}).get("user", {}).get("rag", {})
    # item.project is unreliable here: the item JSON a pipeline code node receives can omit
    # projectId, leaving item.project None (verified live). dl.datasets.get needs no project
    # context to fetch by id.
    output_dataset = dl.datasets.get(dataset_id="__OUTPUT_DATASET_ID__")
    base_name = item.name.rsplit(".", 1)[0]
    stride = max(1, chunk_size - chunk_overlap)

    chunk_count = 0
    for start in range(0, len(text), stride):
        chunk = text[start : start + chunk_size]
        if not chunk.strip():
            continue
        # One upload per chunk: item_metadata applies to the whole call, and each chunk needs its
        # own offsets so a retrieved chunk can be located in the source document.
        output_dataset.items.upload(
            local_path=io.BytesIO(chunk.encode("utf-8")),
            remote_path="__CHUNK_FOLDER__",
            remote_name="{}_chunk_{:05d}.txt".format(base_name, chunk_count),
            overwrite=True,
            item_metadata={
                "user": {
                    "rag": {
                        "role": "chunk",
                        "text_item_id": item.id,
                        "source_item_id": rag_metadata.get("source_item_id"),
                        "source_item_name": rag_metadata.get("source_item_name"),
                        "chunk_index": chunk_count,
                        "start_pos": start,
                        "end_pos": min(start + chunk_size, len(text)),
                    }
                }
            },
        )
        chunk_count += 1

    if chunk_count == 0:
        raise ValueError("Text item {} produced no chunks.".format(item.id))

    # Provenance only -- the count, not the content, and never a vector.
    if item.metadata is None:
        item.metadata = {}
    item.metadata.setdefault("user", {}).setdefault("rag", {})["chunk_count"] = chunk_count
    item.update()
    return item
'''


EMBEDDING_CODE = r'''def run(item):
    """Embed this document's chunk items with a deployed model, into its feature set."""
    import dtlpy as dl

    batch_size = __BATCH_SIZE__

    # item.project is unreliable here: the item JSON a pipeline code node receives can omit
    # projectId, leaving item.project None (verified live). dl.models.get/dl.datasets.get need
    # no project context to fetch by id.
    model = dl.models.get(model_id="__MODEL_ID__")
    output_dataset = dl.datasets.get(dataset_id="__OUTPUT_DATASET_ID__")

    # Chunk items are found by DQL rather than by an id list carried in metadata: a long document
    # produces thousands of chunks, and the query pages instead of growing the item document.
    filters = dl.Filters(resource=dl.FiltersResource.ITEM)
    filters.add(field="metadata.user.rag.text_item_id", values=item.id)
    filters.page_size = batch_size

    embedded = 0
    batch = []
    for chunk_item in output_dataset.items.list(filters=filters).all():
        batch.append(chunk_item.id)
        if len(batch) >= batch_size:
            model.embed(item_ids=batch)
            embedded += len(batch)
            batch = []
    if batch:
        model.embed(item_ids=batch)
        embedded += len(batch)

    if embedded == 0:
        raise ValueError("No chunk items found for text item {}.".format(item.id))

    # Nothing is written back to the item. The model service writes each vector into its own
    # feature set; a copy in metadata would be a second, stale source of truth.
    return item
'''


def resolve_feature_set(dataset: Any, model_id: str, feature_set_id: str | None) -> str:
    """Which feature set holds this dataset's chunk vectors."""
    if feature_set_id is not None:
        return feature_set_id
    sets = paging.page_entities(dataloop_call("feature set list", lambda: dataset.feature_sets.list()))
    if not sets:
        raise ValueError(
            f"Dataset {dataset.id} has no feature set, so nothing has been embedded yet. Install "
            "and run the RAG pipeline's embedding node, then retry."
        )
    candidates = [entry for entry in sets if getattr(entry, "model_id", None) == model_id] or sets
    if len(candidates) > 1:
        names = ", ".join(f"{getattr(s, 'name', '?')} ({getattr(s, 'id', '?')})" for s in candidates)
        raise ValueError(f"Provide feature_set_id: this dataset has several — {names}.")
    return str(candidates[0].id)


def embed_query(dataset: Any, model: Any, query: str, feature_set_id: str) -> np.ndarray:
    """Vector for a query string, produced by the same model that embedded the chunks.

    A query vector is only comparable with the chunk vectors if the same model made it, and that
    model runs on the platform over *items*. So the query is uploaded as a throwaway text item,
    embedded, read back, and deleted — the upload exists only to give the model something to
    embed.

    Deleting the item may or may not take its vector with it, so the ranking below cannot assume
    the feature set holds chunk vectors only: it re-reads each hit's item and drops anything that
    is not a live chunk.
    """
    item = dataloop_call(
        "query item upload",
        lambda: dataset.items.upload(
            local_path=io.BytesIO(query.encode("utf-8")),
            remote_path=QUERY_FOLDER,
            remote_name=f"query_{uuid.uuid4().hex}.txt",
            item_metadata={"user": {"rag": {"role": "query"}}},
        ),
    )
    try:
        execution = dataloop_call("query embed", lambda: model.embed(item_ids=[item.id]))
        if execution is not None and hasattr(execution, "wait"):
            dataloop_call("query embed wait", execution.wait)
        filters = dl.Filters(resource=dl.FiltersResource.FEATURE)
        filters.add(field="featureSetId", values=feature_set_id)
        filters.add(field="entityId", values=item.id)
        for attempt in range(QUERY_POLL_ATTEMPTS):
            listed = dataloop_call("query feature list", lambda: dataset.features.list(filters=filters))
            for feature in paging.page_entities(listed):
                value = getattr(feature, "value", None)
                if value:
                    return np.asarray(value, dtype=float)
            if attempt < QUERY_POLL_ATTEMPTS - 1:
                time.sleep(QUERY_POLL_SECONDS)
        raise RuntimeError(
            f"The query was uploaded and sent to model {getattr(model, 'id', '?')}, but no vector "
            f"appeared in feature set {feature_set_id} within "
            f"{int(QUERY_POLL_ATTEMPTS * QUERY_POLL_SECONDS)}s. Check the model's service is up."
        )
    finally:
        dataloop_call("query item delete", item.delete)


def live_chunk(dataset: Any, item_id: str) -> Any | None:
    """The chunk item behind a feature vector, or None if it is not one.

    A vector can outlive the item it describes — a deleted query item, or a chunk item removed
    after embedding — and a stale vector ranks just as well as a live one. Returning None for
    those keeps them out of the answer instead of surfacing a dangling id.
    """
    try:
        item = dataset.items.get(item_id=item_id)
    except Exception:
        return None
    role = (getattr(item, "metadata", None) or {}).get("user", {}).get("rag", {}).get("role")
    return item if role == "chunk" else None


def chunk_text(item: Any, limit: int = MAX_CHUNK_TEXT_CHARS) -> str | None:
    """The text of a chunk item, for quoting in an answer. None if it cannot be read."""
    try:
        buffer = item.download(save_locally=False)
    except Exception:
        return None
    raw = buffer.getvalue() if hasattr(buffer, "getvalue") else buffer
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="ignore")[:limit]
    return str(raw)[:limit]


def register(mcp: MCPServer) -> None:
    """Register RAG pipeline tools with the MCP server."""

    @mcp.tool(title="Create RAG pipeline", annotations=MUTATING)
    def rag_pipeline_create(
        project_id: Annotated[str, Field(description="Project to create the RAG pipeline in.")],
        name: Annotated[str, Field(min_length=1, description="Name for the RAG pipeline.")],
        source_dataset_id: Annotated[str, Field(description="Dataset of PDFs to process.")],
        output_dataset_id: Annotated[
            str,
            Field(description="Dataset for the text and chunk items; not the source dataset."),
        ],
        description: Annotated[str | None, Field(description="Pipeline description.")] = None,
    ) -> outputs.PipelineStatusSummary:
        """Create an empty RAG pipeline: PDFs -> text items -> chunk items -> embeddings.

        Add stages with rag_add_*, wire with pipeline_connect_nodes, then pipeline_install.
        """
        project = get_project(project_id)

        # Create the pipeline
        pipeline = project.pipelines.create(name=name, project_id=project_id)

        if description is not None:
            # Another whole-graph PATCH (see pipeline_nodes._retry_until_visible), deliberately
            # left unguarded: the pipeline was created two lines above and its graph is empty, so
            # there is no node list for a stale read to lose. Anything touching a *populated*
            # graph must go through the retry helper instead.
            pipeline.description = description
            pipeline = project.pipelines.update(pipeline=pipeline)

        return serialize.pipeline(pipeline)

    def add_node(
        project_id: str, pipeline_id: str, spec: CodeNodeSpec | DatasetNodeSpec
    ) -> outputs.PipelineNodeResult:
        """Add one built node to a pipeline. Delegates to pipeline_nodes.add_node.

        Shared rather than duplicated so the retry-until-visible fix for the platform's
        read-modify-write race (see pipeline_nodes._retry_until_visible) covers every
        rag_add_*_node tool too, not just pipeline_node_add.
        """
        return pipeline_nodes.add_node(project_id, pipeline_id, spec)

    @mcp.tool(title="Add PDF filter node to RAG pipeline", annotations=MUTATING)
    def rag_add_pdf_filter_node(
        project_id: Annotated[str, Field(description="Project owning the pipeline.")],
        pipeline_id: Annotated[str, Field(description="Pipeline to add the node to.")],
        source_dataset_id: Annotated[str, Field(description="Dataset of PDFs.")],
        node_name: Annotated[str, Field(min_length=1, description="Node name.")] = "pdf_filter",
        load_existing: Annotated[
            bool,
            Field(description="Feed the dataset's existing PDFs in on install, not just new ones."),
        ] = False,
    ) -> outputs.PipelineNodeResult:
        """Add the pipeline's entry node: the source dataset, DQL-filtered to PDFs."""
        # DQL filter to select only PDF files
        pdf_filter = {"filter": {"$and": [{"metadata.system.mimetype": {"$eq": "application/pdf"}}]}}

        return add_node(
            project_id,
            pipeline_id,
            DatasetNodeSpec(
                node_type="dataset",
                name=node_name,
                dataset_id=source_dataset_id,
                load_existing_data=load_existing,
                data_filters=pdf_filter,
                position=(1, 1),
            ),
        )

    @mcp.tool(title="Add PDF-to-text conversion node", annotations=MUTATING)
    def rag_add_pdf_to_text_node(
        project_id: Annotated[str, Field(description="Project owning the pipeline.")],
        pipeline_id: Annotated[str, Field(description="Pipeline to add the node to.")],
        output_dataset_id: Annotated[str, Field(description="Dataset the text item is written to.")],
        node_name: Annotated[str, Field(min_length=1, description="Node name.")] = "pdf_to_text",
        position: Annotated[tuple[int, int] | None, Field(description="Grid position (x, y).")] = None,
    ) -> outputs.PipelineNodeResult:
        """Add a code node that turns each PDF into a text item.

        Extracts with pypdf on DOE, uploads to `/rag/text` tagged with the source id, and
        returns that item. A PDF yielding no text fails the execution.
        """
        code = PDF_TO_TEXT_CODE.replace("__OUTPUT_DATASET_ID__", output_dataset_id).replace(
            "__TEXT_FOLDER__", TEXT_FOLDER
        )
        return add_node(
            project_id,
            pipeline_id,
            CodeNodeSpec(node_type="code", name=node_name, code=code, position=position or (2, 1)),
        )

    @mcp.tool(title="Add text-to-chunks node", annotations=MUTATING)
    def rag_add_text_to_chunks_node(
        project_id: Annotated[str, Field(description="Project owning the pipeline.")],
        pipeline_id: Annotated[str, Field(description="Pipeline to add the node to.")],
        output_dataset_id: Annotated[str, Field(description="Dataset the chunk items go to.")],
        chunk_size: Annotated[int, Field(ge=100, le=4000, description="Chunk size in characters.")] = 512,
        chunk_overlap: Annotated[
            int, Field(ge=0, le=500, description="Overlap between chunks, in characters.")
        ] = 50,
        node_name: Annotated[str, Field(min_length=1, description="Node name.")] = "text_to_chunks",
        position: Annotated[tuple[int, int] | None, Field(description="Grid position (x, y).")] = None,
    ) -> outputs.PipelineNodeResult:
        """Add a code node that splits a text item into overlapping chunk items.

        Each chunk is an item under `/rag/chunks`, tagged with its source and offsets: a
        feature vector attaches to an entity, so chunk-level retrieval needs chunk items.
        """
        code = (
            TEXT_TO_CHUNKS_CODE.replace("__OUTPUT_DATASET_ID__", output_dataset_id)
            .replace("__CHUNK_FOLDER__", CHUNK_FOLDER)
            .replace("__CHUNK_SIZE__", str(chunk_size))
            .replace("__CHUNK_OVERLAP__", str(chunk_overlap))
        )
        return add_node(
            project_id,
            pipeline_id,
            CodeNodeSpec(node_type="code", name=node_name, code=code, position=position or (3, 1)),
        )

    @mcp.tool(title="Add embedding generation node", annotations=MUTATING)
    def rag_add_embedding_node(
        project_id: Annotated[str, Field(description="Project owning the pipeline.")],
        pipeline_id: Annotated[str, Field(description="Pipeline to add the node to.")],
        output_dataset_id: Annotated[str, Field(description="Dataset holding the chunks.")],
        model_id: Annotated[
            str,
            Field(description="A deployed embedding model in this project; model_list finds one."),
        ],
        node_name: Annotated[str, Field(min_length=1, description="Node name.")] = "generate_embeddings",
        position: Annotated[tuple[int, int] | None, Field(description="Grid position (x, y).")] = None,
    ) -> outputs.PipelineNodeResult:
        """Add a code node that embeds a document's chunk items with a deployed model.

        The model service writes each vector into its own feature set; nothing goes into item
        metadata. Billable, and the model must already be deployed.
        """
        code = (
            EMBEDDING_CODE.replace("__OUTPUT_DATASET_ID__", output_dataset_id)
            .replace("__MODEL_ID__", model_id)
            .replace("__BATCH_SIZE__", str(EMBED_BATCH_SIZE))
        )
        return add_node(
            project_id,
            pipeline_id,
            CodeNodeSpec(node_type="code", name=node_name, code=code, position=position or (4, 1)),
        )

    @mcp.tool(title="Add output storage node", annotations=MUTATING)
    def rag_add_output_node(
        project_id: Annotated[str, Field(description="Project owning the pipeline.")],
        pipeline_id: Annotated[str, Field(description="Pipeline to add the node to.")],
        output_dataset_id: Annotated[str, Field(description="Dataset the pipeline terminates on.")],
        node_name: Annotated[str, Field(min_length=1, description="Node name.")] = "output_storage",
        position: Annotated[tuple[int, int] | None, Field(description="Grid position (x, y).")] = None,
    ) -> outputs.PipelineNodeResult:
        """Add a dataset node terminating the pipeline on the output dataset.

        The code nodes upload the items themselves; this is where further automation hangs.
        """
        return add_node(
            project_id,
            pipeline_id,
            DatasetNodeSpec(
                node_type="dataset",
                name=node_name,
                dataset_id=output_dataset_id,
                position=position or (5, 1),
            ),
        )

    @mcp.tool(title="Query RAG dataset", annotations=READ_ONLY)
    def rag_query(
        project_id: Annotated[str, Field(description="Project owning the dataset.")],
        dataset_id: Annotated[str, Field(description="Dataset holding the chunk items.")],
        query: Annotated[str, Field(min_length=1, description="Natural language query.")],
        model_id: Annotated[
            str,
            Field(description="The model that embedded the chunks; another's vectors do not compare."),
        ],
        feature_set_id: Annotated[
            str | None,
            Field(description="Feature set to search. Defaults to the model's, or the only one."),
        ] = None,
        max_results: Annotated[int, Field(ge=1, le=100, description="Maximum chunks to return.")] = 5,
        min_similarity: Annotated[
            float, Field(ge=-1.0, le=1.0, description="Minimum cosine similarity.")
        ] = 0.0,
        max_vectors: Annotated[int, Field(ge=2, le=2000, description="Cap on vectors scanned.")] = 1000,
        ctx: Context | None = None,
    ) -> outputs.RagQueryResult:
        """Rank chunk items against a query by vector similarity over the feature set.

        The query is embedded by the same model as the chunks — uploaded as a throwaway item,
        embedded, read back, deleted. Ranking is local (`dtlpy` has no vector-distance query)
        over a bounded sample: check `scan_truncated`.
        """
        dataset = get_dataset(project_id, dataset_id)
        project = get_project(project_id)
        model = dataloop_call("model lookup", lambda: project.models.get(model_id=model_id))
        resolved_set = resolve_feature_set(dataset, model_id, feature_set_id)

        query_vector = embed_query(dataset, model, query, resolved_set)
        # Ranking happens here, not on the platform (dtlpy has no vector-distance query), so the
        # same tripwire applies as for dataset_cluster: see guards.gate_local_compute.
        gate_local_compute(
            ctx, requested=max_vectors, action="rank these chunks locally", parameter="max_vectors"
        )
        features, truncated = dataloop_call(
            "feature list", lambda: read_vectors(dataset, resolved_set, max_vectors)
        )
        usable = [f for f in features if getattr(f, "value", None) and getattr(f, "entity_id", None)]
        if not usable:
            raise ValueError(
                f"No usable vectors in feature set {resolved_set}. Embedding may still be running "
                "— check dataset_embeddings_status."
            )

        scores = cosine_similarity(np.asarray([f.value for f in usable], dtype=float), query_vector)
        results: list[outputs.RagQueryMatch] = []
        for index in np.argsort(-scores):
            score = float(scores[index])
            if score < min_similarity:
                break
            chunk_item = live_chunk(dataset, usable[index].entity_id)
            if chunk_item is None:
                continue
            provenance = (chunk_item.metadata or {}).get("user", {}).get("rag", {})
            results.append(
                {
                    "item_id": chunk_item.id,
                    "item_name": chunk_item.name,
                    "similarity": round(score, 4),
                    "source_item_id": provenance.get("source_item_id"),
                    "source_item_name": provenance.get("source_item_name"),
                    "chunk_index": provenance.get("chunk_index"),
                    "text": chunk_text(chunk_item),
                }
            )
            if len(results) >= max_results:
                break

        return {
            "query": query,
            "dataset_id": dataset_id,
            "feature_set_id": resolved_set,
            "vectors_scanned": len(usable),
            "scan_truncated": truncated,
            "results_count": len(results),
            "results": results,
        }
