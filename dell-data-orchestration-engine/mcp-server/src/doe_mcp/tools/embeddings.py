"""Embeddings and clustering over a dataset's feature vectors.

DOE ships a native Clustering app — the TensorBoard Embedding Projector, reachable from the
Dataset Browser. It is a *visualisation*: it renders a UMAP projection for a person to look at,
and exposes no API that returns "here are your clusters". An agent cannot consume a picture.

What it consumes, though, is reachable. The app runs on **feature sets**, and the SDK exposes the
whole input side: ``Model.embed_datasets`` extracts vectors (the same operation the Clustering
tab's "Add CLIP Embeddings" button performs), ``Dataset.feature_sets`` reports what a dataset
already has, and ``Features.list`` reads the vectors back.

So this module does two distinct things:

* **Prepare and inspect** — say whether a dataset can be clustered at all, and extract embeddings
  when it cannot. That feeds the platform's own app; a human then opens the Clustering tab.
* **Cluster here** — group the vectors in this process so an *agent* can describe the themes in
  words. This exists because `dtlpy` has no vector-distance query: there is no server-side
  nearest-neighbour or grouping call to delegate to (verified against 1.125.6). Grouping a bounded
  sample locally is the only way to turn vectors into a description.

The clustering is deliberately plain: k-means over a bounded sample, fixed seed, numpy only.
It is a means of describing a dataset, not a modelling result, and nothing downstream depends on
its exact partition.
"""

import math
from typing import Annotated, Any

import dtlpy as dl
import numpy as np
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from .. import paging
from ..context import dataloop_call, get_dataset, get_project
from ..guards import confirm_or_elicit, elicit_or_refuse, gate_local_compute
from ..progress import report_progress

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
MUTATING = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)

# Extraction spins up a high-memory machine and runs a model over every item, so the ceiling is
# a guard against embedding a very large dataset by accident, not a technical limit.
DEFAULT_EMBED_CEILING = 5000
# Vectors are pulled into this process to cluster them, so the sample is bounded for memory as
# much as for time.
VECTOR_PAGE_SIZE = 100
MAX_VECTORS = 2000
KMEANS_ITERATIONS = 25
KMEANS_SEED = 0


def kmeans(vectors: np.ndarray, k: int, iterations: int = KMEANS_ITERATIONS) -> np.ndarray:
    """Assign each vector to one of k clusters. Plain Lloyd's algorithm, fixed seed.

    Deliberately not scikit-learn: this project declares only `mcp` and `dtlpy`, and sklearn is
    present in some environments only as an incidental transitive dependency. numpy is a hard
    dependency of dtlpy and is declared explicitly in pyproject for this use.
    """
    rng = np.random.default_rng(KMEANS_SEED)
    count = len(vectors)
    k = max(1, min(k, count))
    # k-means++-ish start: first centre at random, the rest far from what is already chosen.
    centres = [vectors[rng.integers(count)]]
    for _ in range(k - 1):
        distances = np.min(np.stack([np.linalg.norm(vectors - centre, axis=1) for centre in centres]), axis=0)
        centres.append(vectors[int(np.argmax(distances))])
    centroids = np.stack(centres)

    labels = np.zeros(count, dtype=int)
    for _ in range(iterations):
        distances = np.linalg.norm(vectors[:, None, :] - centroids[None, :, :], axis=2)
        new_labels = np.argmin(distances, axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for index in range(k):
            members = vectors[labels == index]
            if len(members):
                centroids[index] = members.mean(axis=0)
    return labels


def suggest_k(count: int) -> int:
    """A defensible default when the caller has no opinion: roughly sqrt(n/2), capped."""
    return max(2, min(8, int(math.sqrt(max(count, 1) / 2)) or 2))


def cosine_similarity(vectors: np.ndarray, query_vector: np.ndarray) -> np.ndarray:
    """Compute cosine similarity between matrix of row vectors and a single query vector."""
    if len(vectors) == 0 or len(query_vector) == 0:
        return np.array([])
    norms = np.linalg.norm(vectors, axis=1)
    q_norm = np.linalg.norm(query_vector)
    if q_norm == 0:
        return np.zeros(len(vectors), dtype=float)
    valid = norms > 0
    scores = np.zeros(len(vectors), dtype=float)
    scores[valid] = np.dot(vectors[valid], query_vector) / (norms[valid] * q_norm)
    return scores


def feature_vector_count(dataset: Any, feature_set_id: str) -> tuple[int | None, bool]:
    """Return the number of vectors and whether the platform reported an exact total."""
    filters = dl.Filters(resource=dl.FiltersResource.FEATURE)
    filters.add(field="featureSetId", values=feature_set_id)
    filters.page_size = 1
    listed = dataloop_call("feature count", lambda: dataset.features.list(filters=filters))
    count = getattr(listed, "items_count", None)
    if count is None:
        return None, False
    # dtlpy defaults items_count to zero even when a response omits the total.
    if not count and paging.page_entities(listed):
        return None, False
    return int(count), True


def _vector_count_of(dataset: Any, feature_set: Any) -> int | None:
    """Return an exact count for one listed feature set, or None when unavailable."""
    set_id = getattr(feature_set, "id", None)
    if not set_id:
        return None
    try:
        count, exact = feature_vector_count(dataset, set_id)
    except Exception:
        return None
    return count if exact else None


def item_exists(dataset: Any, item_id: str) -> bool:
    """Whether a feature vector still points at a live item."""
    try:
        return dataset.items.get(item_id=item_id) is not None
    except Exception:
        return False


def feature_set_for_model(dataset: Any, model_id: str) -> Any | None:
    """Find the feature set written by one model, if extraction created it."""
    sets = paging.page_entities(dataloop_call("feature set list", lambda: dataset.feature_sets.list()))
    return next(
        (entry for entry in sets if getattr(entry, "model_id", None) == model_id),
        None,
    )


def read_vectors(dataset: Any, feature_set_id: str, max_vectors: int) -> tuple[list[Any], bool]:
    """Page feature vectors for one feature set, up to a cap."""
    collected: list[Any] = []
    truncated = False
    for page_index in range(max(1, max_vectors // VECTOR_PAGE_SIZE)):
        filters = dl.Filters(resource=dl.FiltersResource.FEATURE)
        filters.add(field="featureSetId", values=feature_set_id)
        filters.page = page_index
        filters.page_size = VECTOR_PAGE_SIZE
        page = dataset.features.list(filters=filters)
        page_items = paging.page_entities(page)
        collected.extend(page_items)
        if not getattr(page, "has_next_page", False):
            break
        if len(collected) >= max_vectors:
            truncated = True
            break
    else:
        truncated = True
    return collected[:max_vectors], truncated


def register(mcp: MCPServer) -> None:
    @mcp.tool(title="Report a dataset's embeddings", annotations=READ_ONLY)
    def dataset_embeddings_status(
        project_id: Annotated[str, Field(description="Project that owns the dataset.")],
        dataset_id: Annotated[str, Field(description="Dataset to inspect.")],
    ) -> dict[str, Any]:
        """Say whether a dataset has embeddings, and therefore whether it can be clustered.

        The platform's Clustering tab needs a feature set to work from. This reports what exists
        without extracting anything, so it is free and safe to call while planning.
        """
        dataset = get_dataset(project_id, dataset_id)
        listed = dataloop_call("feature set list", lambda: dataset.feature_sets.list())
        sets = paging.page_entities(listed)
        summaries = [
            {
                "id": getattr(entry, "id", None),
                "name": getattr(entry, "name", None),
                # Vector dimension, not extraction progress.
                "size": getattr(entry, "size", None),
                "vector_count": _vector_count_of(dataset, entry),
                "entity_type": str(getattr(entry, "entity_type", None)),
                "model_id": getattr(entry, "model_id", None),
            }
            for entry in sets
        ]
        return {
            "dataset_id": dataset_id,
            "has_embeddings": bool(summaries),
            "feature_sets": summaries,
            "next_step": (
                "Extraction is asynchronous: poll this until vector_count stops climbing, then "
                "compare it with the dataset's item count. size is the vector dimension and "
                "does not change. Then cluster with dataset_cluster, or open the dataset's "
                "Clustering tab in the DOE console."
                if summaries
                else "No feature set on this dataset. dataset_embed extracts one, using a model "
                "already installed in the project. That is billable."
            ),
        }

    @mcp.tool(title="Extract embeddings for a dataset", annotations=MUTATING)
    def dataset_embed(
        project_id: Annotated[str, Field(description="Project that owns the dataset.")],
        dataset_id: Annotated[str, Field(description="Dataset to embed.")],
        model_id: Annotated[
            str,
            Field(description="An embedding model already installed in the project, e.g. a CLIP model."),
        ],
        confirm: Annotated[
            bool,
            Field(description="Must be true: extraction runs a model over every item and is billable."),
        ] = False,
        max_items: Annotated[
            int,
            Field(ge=1, description="Refuse if the dataset holds more items than this."),
        ] = DEFAULT_EMBED_CEILING,
        attach_trigger: Annotated[
            bool,
            Field(
                description=(
                    "Also embed new items automatically as they arrive. Standing automation: it "
                    "keeps costing money after this conversation ends."
                )
            ),
        ] = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Extract feature vectors for a dataset, so it can be clustered.

        The same operation as the Clustering tab's "Add CLIP Embeddings" button: the chosen model
        runs over every item on a high-memory machine. Prefer a model already installed.

        Two costs. Extraction is billable and scales with item count, so `max_items` refuses an
        unexpectedly large dataset rather than quietly embedding it. And `attach_trigger` leaves
        automation embedding each new arrival, outliving this session.
        """
        dataset = get_dataset(project_id, dataset_id)
        items_count = getattr(dataset, "items_count", None)
        if items_count is not None and items_count > max_items:
            raise ValueError(
                f"Refusing to embed: dataset holds {items_count} items, above the {max_items} "
                "ceiling. Extraction runs a model over every one of them on a high-memory "
                "machine. Raise max_items deliberately if that cost is intended."
            )

        consequence = (
            "a model runs over every item in the dataset on a high-memory machine, which is billable"
        )
        if attach_trigger:
            consequence += (
                ", and attach_trigger leaves automation running that embeds every new item from "
                "now on, outliving this conversation"
            )
        confirm_or_elicit(ctx, confirm, f"extract embeddings for {items_count or 'all'} item(s)", consequence)

        project = get_project(project_id)
        model = project.models.get(model_id=model_id)
        report_progress(ctx, 0, 100, f"Starting embedding extraction for {items_count or 'all'} item(s)...")
        dataloop_call(
            "embedding extraction",
            lambda: model.embed_datasets(dataset_ids=[dataset_id], attach_trigger=attach_trigger),
        )
        report_progress(ctx, 100, 100, "Embedding extraction complete.")

        # The write is asynchronous. Read back an honest immediate snapshot, but
        # never turn a successful billable submission into a failure if this
        # diagnostic read is unavailable.
        vectors_written: int | None = None
        exact = False
        read_back_error: str | None = None
        try:
            feature_set = feature_set_for_model(dataset, model_id)
            if feature_set is not None:
                vectors_written, exact = feature_vector_count(dataset, feature_set.id)
        except Exception as exc:
            read_back_error = str(exc).strip() or type(exc).__name__

        result: dict[str, Any] = {
            "dataset_id": dataset_id,
            "model_id": model_id,
            "items": items_count,
            "vectors_written": vectors_written if exact else None,
            "vectors_written_as_of": "immediately after submission; extraction is still running",
            "trigger_attached": attach_trigger,
            "note": (
                "Extraction runs asynchronously: vectors_written is an immediate snapshot, not a "
                "final count. Poll dataset_embeddings_status until vector_count stops climbing. "
                "A settled count below items can mean the model input type did not cover every "
                "item; size is only the vector dimension."
            ),
        }
        if read_back_error is not None:
            result["vectors_written_error"] = (
                "Extraction was submitted successfully, but reading the feature set back failed: "
                f"{read_back_error}. Check dataset_embeddings_status; do not retry the billable "
                "submission solely because this diagnostic failed."
            )
        return result

    @mcp.tool(title="Cluster a dataset's embeddings", annotations=READ_ONLY)
    def dataset_cluster(
        project_id: Annotated[str, Field(description="Project that owns the dataset.")],
        dataset_id: Annotated[str, Field(description="Dataset to cluster.")],
        feature_set_id: Annotated[
            str | None,
            Field(description="Which feature set to use. Defaults to the only one, if there is one."),
        ] = None,
        clusters: Annotated[
            int | None, Field(ge=2, le=20, description="How many groups. Defaults to a size-based guess.")
        ] = None,
        max_vectors: Annotated[
            int, Field(ge=2, le=MAX_VECTORS, description="Cap on vectors pulled into this process.")
        ] = 1000,
        examples: Annotated[int, Field(ge=1, le=10, description="Example items per cluster.")] = 3,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Group a dataset's items by embedding similarity, so their themes can be described.

        The Clustering app visualises the same vectors for a human; this returns the grouping as
        data. It runs **in this server** because `dtlpy` has no vector-distance query.

        Sizes are reliable; the cluster *meaning* is not. k-means returns groups, not labels, so
        describing one means looking at its example items — a starting point for inspection,
        never a classification.
        """
        dataset = get_dataset(project_id, dataset_id)
        if feature_set_id is None:
            listed = dataloop_call("feature set list", lambda: dataset.feature_sets.list())
            sets = paging.page_entities(listed)
            if not sets:
                raise ValueError(
                    "This dataset has no feature set, so there is nothing to cluster. Run "
                    "dataset_embed first, or check dataset_embeddings_status."
                )
            if len(sets) > 1:
                names = ", ".join(f"{getattr(s, 'name', '?')} ({getattr(s, 'id', '?')})" for s in sets)
                raise ValueError(f"Provide feature_set_id: this dataset has several — {names}.")
            feature_set_id = sets[0].id

        # k-means runs here, in this process, over vectors pulled off the platform (ADR-017).
        # Past a volume that stops being a bounded diagnostic read.
        gate_local_compute(
            ctx, requested=max_vectors, action="cluster this dataset locally", parameter="max_vectors"
        )
        features, truncated = dataloop_call(
            "feature list", lambda: read_vectors(dataset, feature_set_id, max_vectors)
        )
        usable = [f for f in features if getattr(f, "value", None)]
        if len(usable) < 2:
            raise ValueError(
                f"Only {len(usable)} usable vector(s) in feature set {feature_set_id}; clustering "
                "needs at least two. Extraction may still be running — check "
                "dataset_embeddings_status."
            )

        matrix = np.asarray([f.value for f in usable], dtype=float)
        count = len(usable)
        k = clusters or suggest_k(count)
        labels = kmeans(matrix, k)

        groups: list[dict[str, Any]] = []
        for index in sorted(set(labels.tolist())):
            members = [usable[i] for i, label in enumerate(labels) if label == index]
            groups.append(
                {
                    "cluster": int(index),
                    "size": len(members),
                    "share": round(len(members) / count, 3),
                    "example_item_ids": [getattr(m, "entity_id", None) for m in members[:examples]],
                }
            )
        groups.sort(key=lambda g: g["size"], reverse=True)
        return {
            "dataset_id": dataset_id,
            "feature_set_id": feature_set_id,
            "vectors_used": count,
            "scan_limit": max_vectors,
            "scan_truncated": truncated,
            "clusters": groups,
            "caveat": (
                "k-means groups vectors; it does not name them. To describe a cluster, fetch its "
                "example items and look at them — the grouping is a starting point for "
                "inspection, not a classification."
                + (
                    f" Only the first {count} vectors were used, so the shares describe a sample."
                    if truncated
                    else ""
                )
            ),
        }

    @mcp.tool(title="Semantic visual search over a dataset", annotations=MUTATING)
    def dataset_visual_search(
        project_id: Annotated[str, Field(description="Project that owns the dataset.")],
        dataset_id: Annotated[str, Field(description="Dataset to search in.")],
        query: Annotated[
            str | None,
            Field(description="Natural language visual search query, e.g. 'a photo of a dog'."),
        ] = None,
        query_vector: Annotated[
            list[float] | None,
            Field(description="Precomputed embedding vector. Provide query or query_vector."),
        ] = None,
        feature_set_id: Annotated[
            str | None,
            Field(description="Which feature set to search over. Defaults to the only one, if there is one."),
        ] = None,
        limit: Annotated[
            int, Field(ge=1, le=100, description="Maximum number of matching items to return.")
        ] = 10,
        min_similarity: Annotated[
            float, Field(ge=-1.0, le=1.0, description="Minimum cosine similarity threshold.")
        ] = 0.0,
        max_vectors: Annotated[
            int, Field(ge=2, le=MAX_VECTORS, description="Cap on vectors pulled into this process.")
        ] = 1000,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Search items in a dataset by visual/semantic embedding similarity.

        A supplied query_vector is read-only. A text query is embedded by the deployed
        platform model, which uploads a temporary item and bills for inference, so it
        requires human elicitation.
        """
        if not query and not query_vector:
            raise ValueError("Provide either query or query_vector.")

        dataset = get_dataset(project_id, dataset_id)
        listed = dataloop_call("feature set list", lambda: dataset.feature_sets.list())
        sets = paging.page_entities(listed)
        if not sets:
            raise ValueError(
                "This dataset has no feature set, so there is nothing to search over. Run "
                "dataset_embed first, or check dataset_embeddings_status."
            )
        names = ", ".join(f"{getattr(entry, 'name', '?')} ({getattr(entry, 'id', '?')})" for entry in sets)
        if feature_set_id is None:
            if len(sets) > 1:
                raise ValueError(f"Provide feature_set_id: this dataset has several — {names}.")
            feature_set = sets[0]
            feature_set_id = feature_set.id
        else:
            feature_set = next(
                (entry for entry in sets if getattr(entry, "id", None) == feature_set_id),
                None,
            )
            if feature_set is None:
                raise ValueError(
                    f"Feature set {feature_set_id} is not in dataset {dataset_id}. Available: {names}."
                )

        query_item_id: str | None = None
        if query_vector is not None:
            q_vec = np.asarray(query_vector, dtype=float)
        else:
            model_id = getattr(feature_set, "model_id", None)
            if not model_id:
                raise ValueError(
                    f"Cannot resolve which model embedded feature set {feature_set_id}. "
                    "Provide query_vector instead."
                )
            elicit_or_refuse(
                ctx,
                action="embed this text query on the platform",
                consequence=(
                    "it uploads a temporary item into this dataset, runs the deployed model "
                    "over it (billable), and deletes the item again"
                ),
                remedy="Pass query_vector instead to search without writing anything.",
            )
            project = get_project(project_id)
            model = dataloop_call("model lookup", lambda: project.models.get(model_id=model_id))
            # Deferred to avoid embeddings.py <-> rag.py import recursion.
            from .rag import embed_query

            q_vec, query_item_id = embed_query(dataset, model, query or "", feature_set_id)

        # Cosine similarity over every vector pulled, computed here rather than on the platform.
        gate_local_compute(
            ctx, requested=max_vectors, action="run this visual search locally", parameter="max_vectors"
        )
        features, truncated = dataloop_call(
            "feature list", lambda: read_vectors(dataset, feature_set_id, max_vectors)
        )
        usable = [f for f in features if getattr(f, "value", None) and getattr(f, "entity_id", None)]
        if query_item_id is not None:
            usable = [feature for feature in usable if feature.entity_id != query_item_id]
        if not usable:
            raise ValueError(
                f"No usable vectors found in feature set {feature_set_id}. Extraction may still be "
                "running — check dataset_embeddings_status."
            )

        matrix = np.asarray([f.value for f in usable], dtype=float)
        scores = cosine_similarity(matrix, q_vec)

        ranked_indices = np.argsort(-scores)
        matches: list[dict[str, Any]] = []
        stale = 0
        for idx in ranked_indices:
            score = float(scores[idx])
            if score < min_similarity:
                break
            feature_item = usable[idx]
            if not item_exists(dataset, feature_item.entity_id):
                stale += 1
                continue
            matches.append(
                {
                    "item_id": feature_item.entity_id,
                    "similarity": round(score, 4),
                    "feature_id": getattr(feature_item, "id", None),
                }
            )
            if len(matches) >= limit:
                break

        return {
            "dataset_id": dataset_id,
            "feature_set_id": feature_set_id,
            "query": query,
            "vectors_scanned": len(usable),
            "scan_truncated": truncated,
            "matches_count": len(matches),
            "stale_vectors_skipped": stale,
            "results": matches,
        }
