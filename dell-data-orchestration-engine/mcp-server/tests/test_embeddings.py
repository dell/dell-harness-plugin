# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Embedding status, extraction, and in-server clustering.

Clustering runs here because dtlpy exposes no vector-distance query; these tests pin the
guards around that (bounded reads, honest caveats) and the k-means itself on separable data.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from doe_mcp.tools import embeddings

ARGS = {"project_id": "proj-1", "dataset_id": "ds-1"}


def fake_feature_set(set_id="fs-1", name="clip", size=512, model_id="model-1"):
    return SimpleNamespace(id=set_id, name=name, size=size, entity_type="item", model_id=model_id)


def fake_feature(entity_id, value):
    return SimpleNamespace(id=f"f-{entity_id}", entity_id=entity_id, value=value)


class FakeFeatureSets:
    def __init__(self, sets):
        self._sets = sets

    def list(self):
        return SimpleNamespace(items=list(self._sets))


class FakeFeatures:
    def __init__(self, pages):
        self._pages = pages
        self._total = sum(len(page) for page in pages)
        self.filters_seen = []

    def list(self, filters=None):
        self.filters_seen.append(filters)
        index = getattr(filters, "page", 0)
        page = self._pages[index] if index < len(self._pages) else []
        return SimpleNamespace(
            items=page, has_next_page=index < len(self._pages) - 1, items_count=self._total
        )


class FakeItems:
    """Items the dataset still holds. `missing` stands for a vector that outlived its item."""

    def __init__(self, missing=()):
        self.missing = set(missing)
        self.requested: list[str] = []

    def get(self, item_id=None, **_):
        self.requested.append(item_id)
        if item_id in self.missing:
            raise ValueError(f"Item {item_id} not found")
        return SimpleNamespace(id=item_id)


def build_dataset(monkeypatch, sets=(), feature_pages=(), items_count=10, missing_items=()):
    ds = SimpleNamespace(
        id="ds-1",
        name="photos",
        items_count=items_count,
        feature_sets=FakeFeatureSets(sets),
        features=FakeFeatures(list(feature_pages)),
        items=FakeItems(missing_items),
    )
    monkeypatch.setattr(embeddings, "get_dataset", lambda project_id, dataset_id: ds)
    return ds


# --- status ------------------------------------------------------------------------------


def test_status_reports_no_embeddings_and_points_at_extraction(monkeypatch, call_tool):
    build_dataset(monkeypatch, sets=())

    body = call_tool("dataset_embeddings_status", ARGS).structured_content

    assert body["has_embeddings"] is False
    assert body["feature_sets"] == []
    assert "dataset_embed" in body["next_step"]


def test_status_reports_an_existing_feature_set(monkeypatch, call_tool):
    build_dataset(
        monkeypatch,
        sets=[fake_feature_set()],
        feature_pages=[[fake_feature("item-1", [1.0, 0.0])]],
    )

    body = call_tool("dataset_embeddings_status", ARGS).structured_content

    assert body["has_embeddings"] is True
    assert body["feature_sets"][0]["name"] == "clip"
    assert body["feature_sets"][0]["vector_count"] == 1
    # The platform's own Clustering tab is the other consumer worth mentioning.
    assert "Clustering tab" in body["next_step"]


# --- extraction ---------------------------------------------------------------------------


class FakeModel:
    project_id = "proj-1"

    def __init__(self):
        self.embedded = None

    def embed_datasets(self, dataset_ids=None, attach_trigger=False):
        self.embedded = {"dataset_ids": dataset_ids, "attach_trigger": attach_trigger}
        return SimpleNamespace(id="cmd-1")


@pytest.fixture
def project(monkeypatch):
    model = FakeModel()
    proj = SimpleNamespace(id="proj-1", models=SimpleNamespace(get=lambda model_id=None, **_: model))
    monkeypatch.setattr(embeddings, "get_project", lambda project_id: proj)
    return model


EMBED = {**ARGS, "model_id": "model-1"}


def test_embed_refuses_without_confirmation(monkeypatch, project, call_tool):
    build_dataset(monkeypatch, items_count=10)

    result = call_tool("dataset_embed", EMBED)

    assert result.is_error is True
    assert "billable" in result.content[0].text
    assert project.embedded is None


def test_embed_refuses_a_dataset_above_the_ceiling(monkeypatch, project, call_tool):
    build_dataset(monkeypatch, items_count=50_000)

    result = call_tool("dataset_embed", {**EMBED, "confirm": True, "max_items": 5000})

    assert result.is_error is True
    message = result.content[0].text
    # The ceiling exists so a huge dataset cannot be embedded by reflex; it must name both numbers.
    assert "50000" in message and "5000" in message
    assert project.embedded is None


def test_embed_runs_when_confirmed_and_within_the_ceiling(monkeypatch, project, call_tool):
    build_dataset(monkeypatch, items_count=10)

    body = call_tool("dataset_embed", {**EMBED, "confirm": True}).structured_content

    assert project.embedded == {"dataset_ids": ["ds-1"], "attach_trigger": False}
    assert body["trigger_attached"] is False
    # Asynchronous: a caller must not cluster immediately on the strength of this return.
    assert "asynchronously" in body["note"]
    assert body["vectors_written"] is None
    assert "vector_count" in body["note"]


def test_embed_names_the_standing_cost_when_attaching_a_trigger(monkeypatch, project, call_tool):
    build_dataset(monkeypatch, items_count=10)

    result = call_tool("dataset_embed", {**EMBED, "attach_trigger": True})

    assert result.is_error is True
    # The refusal must distinguish one-off spend from automation that outlives the session.
    assert "outliving this conversation" in result.content[0].text


def test_embed_attaches_the_trigger_when_confirmed(monkeypatch, project, call_tool):
    build_dataset(monkeypatch, items_count=10)

    call_tool("dataset_embed", {**EMBED, "attach_trigger": True, "confirm": True})

    assert project.embedded["attach_trigger"] is True


def test_embed_reports_actual_vectors_written_not_just_the_dataset_item_count(
    monkeypatch, project, call_tool
):
    """Live-verified 2026-08-31 against a real mixed dataset: an image-only CLIP model embedded
    60 of 81 items (the JPEGs) and silently skipped the other 21 (PDFs) -- embed_datasets' own
    return value said nothing about it. dataset_embed must read the feature set back rather than
    echoing items_count as if every item got a vector.
    """
    build_dataset(
        monkeypatch,
        sets=[fake_feature_set(set_id="fs-1", model_id="model-1")],
        feature_pages=[[fake_feature(f"item-{i}", [0.1]) for i in range(60)]],
        items_count=81,
    )

    body = call_tool("dataset_embed", {**EMBED, "confirm": True}).structured_content

    assert body["items"] == 81
    assert body["vectors_written"] == 60


def test_embed_does_not_pass_a_verdict_on_an_asynchronous_snapshot(monkeypatch, project, call_tool):
    """The count is read milliseconds after submission, while extraction is still running, so a
    number below `items` is the *normal* case. An earlier version emitted a `shortfall` verdict
    here claiming a permanent input_type mismatch, which would have fired on almost every healthy
    call. Report the snapshot and say what it is; let the polling tool settle it.
    """
    build_dataset(
        monkeypatch,
        sets=[fake_feature_set(set_id="fs-1", model_id="model-1")],
        feature_pages=[[fake_feature(f"item-{i}", [0.1]) for i in range(3)]],
        items_count=81,
    )

    body = call_tool("dataset_embed", {**EMBED, "confirm": True}).structured_content

    assert body["vectors_written"] == 3
    assert "shortfall" not in body
    assert "still running" in body["vectors_written_as_of"]
    assert "dataset_embeddings_status" in body["note"]


def test_embed_reports_an_unknown_count_as_null_never_as_zero(monkeypatch, project, call_tool):
    """`PagedEntities.items_count` defaults to 0 and is only overwritten when the response carries
    `totalItemsCount`, so a bare read cannot tell "no total reported" from "zero vectors".
    Reporting the first as a confident 0 is how a healthy run gets told it embedded nothing.
    """

    class NoTotalFeatures(FakeFeatures):
        def list(self, filters=None):
            page = super().list(filters)
            page.items_count = 0  # dtlpy's default showing through, with a non-empty page
            return page

    dataset = build_dataset(
        monkeypatch,
        sets=[fake_feature_set(set_id="fs-1", model_id="model-1")],
        feature_pages=[[fake_feature("item-1", [0.1])]],
        items_count=10,
    )
    dataset.features = NoTotalFeatures([[fake_feature("item-1", [0.1])]])

    body = call_tool("dataset_embed", {**EMBED, "confirm": True}).structured_content

    assert body["vectors_written"] is None


def test_embed_reports_vectors_written_as_none_when_the_feature_set_is_not_found_yet(
    monkeypatch, project, call_tool
):
    """A feature set can lag behind the extraction call finishing; report unknown rather than a
    wrong number.
    """
    build_dataset(monkeypatch, sets=[], items_count=10)

    body = call_tool("dataset_embed", {**EMBED, "confirm": True}).structured_content

    assert body["vectors_written"] is None


def test_embed_does_not_fail_the_call_when_the_read_back_fails(monkeypatch, project, call_tool):
    """The billable work is already submitted by then. Raising would report failure for something
    that succeeded, and invite a retry that pays twice.
    """

    class ExplodingFeatureSets:
        def list(self):
            raise RuntimeError("features endpoint said no")

    dataset = build_dataset(monkeypatch, items_count=10)
    dataset.feature_sets = ExplodingFeatureSets()

    result = call_tool("dataset_embed", {**EMBED, "confirm": True})

    assert result.is_error is False, "a failed diagnostic must not mask a successful extraction"
    assert project.embedded is not None
    assert result.structured_content["vectors_written"] is None
    assert "submitted successfully" in result.structured_content["vectors_written_error"]


def test_embeddings_status_reports_the_vector_count_not_just_the_dimension(monkeypatch, call_tool):
    """`size` is the vector dimension and never changes, yet the guides told agents to poll it
    "until the feature set reaches the expected size". vector_count is the number that moves.
    """
    build_dataset(
        monkeypatch,
        sets=[fake_feature_set(set_id="fs-1", size=512)],
        feature_pages=[[fake_feature(f"item-{i}", [0.1]) for i in range(7)]],
    )

    body = call_tool("dataset_embeddings_status", ARGS).structured_content

    assert body["feature_sets"][0]["size"] == 512
    assert body["feature_sets"][0]["vector_count"] == 7
    assert "vector_count" in body["next_step"]


# --- clustering ---------------------------------------------------------------------------


def separable_pages(per_group=6):
    """Two well-separated blobs, so a correct k-means must recover them."""
    rng = np.random.default_rng(1)
    left = [
        fake_feature(f"l{n}", (rng.normal(0, 0.01, 4) + np.array([0, 0, 0, 0])).tolist())
        for n in range(per_group)
    ]
    right = [
        fake_feature(f"r{n}", (rng.normal(0, 0.01, 4) + np.array([9, 9, 9, 9])).tolist())
        for n in range(per_group)
    ]
    return [left + right]


def test_cluster_recovers_two_separated_groups(monkeypatch, call_tool):
    build_dataset(monkeypatch, sets=[fake_feature_set()], feature_pages=separable_pages())

    body = call_tool("dataset_cluster", {**ARGS, "clusters": 2}).structured_content

    assert body["vectors_used"] == 12
    assert len(body["clusters"]) == 2
    assert [c["size"] for c in body["clusters"]] == [6, 6]
    # Each cluster must be internally consistent: all lefts together, all rights together.
    for cluster in body["clusters"]:
        prefixes = {str(i)[0] for i in cluster["example_item_ids"]}
        assert len(prefixes) == 1


def test_cluster_refuses_when_the_dataset_has_no_feature_set(monkeypatch, call_tool):
    build_dataset(monkeypatch, sets=())

    result = call_tool("dataset_cluster", ARGS)

    assert result.is_error is True
    assert "dataset_embed" in result.content[0].text


def test_cluster_asks_which_feature_set_when_several_exist(monkeypatch, call_tool):
    build_dataset(monkeypatch, sets=[fake_feature_set("fs-1", "clip"), fake_feature_set("fs-2", "nnlm")])

    result = call_tool("dataset_cluster", ARGS)

    assert result.is_error is True
    message = result.content[0].text
    # Guessing which embedding space to use would silently change the answer.
    assert "clip" in message and "nnlm" in message


def test_cluster_refuses_when_there_is_almost_nothing_to_cluster(monkeypatch, call_tool):
    build_dataset(monkeypatch, sets=[fake_feature_set()], feature_pages=[[fake_feature("a", [1, 2])]])

    result = call_tool("dataset_cluster", ARGS)

    assert result.is_error is True
    # Extraction being mid-flight is the likely cause, so the message says so.
    assert "still be running" in result.content[0].text


def test_cluster_skips_vectors_with_no_value(monkeypatch, call_tool):
    page = [fake_feature("a", [0, 0]), fake_feature("b", None), fake_feature("c", [9, 9])]
    build_dataset(monkeypatch, sets=[fake_feature_set()], feature_pages=[page])

    body = call_tool("dataset_cluster", {**ARGS, "clusters": 2}).structured_content

    assert body["vectors_used"] == 2


def test_cluster_says_the_grouping_is_not_a_classification(monkeypatch, call_tool):
    build_dataset(monkeypatch, sets=[fake_feature_set()], feature_pages=separable_pages())

    body = call_tool("dataset_cluster", {**ARGS, "clusters": 2}).structured_content

    # k-means returns groups, not names. Presenting them as labels would be a fabrication.
    assert "does not name them" in body["caveat"]


def test_cluster_reports_truncation_when_it_reads_only_a_sample(monkeypatch, call_tool):
    pages = [[fake_feature(f"p{p}-{n}", [float(p), float(n)]) for n in range(100)] for p in range(3)]
    build_dataset(monkeypatch, sets=[fake_feature_set()], feature_pages=pages)

    body = call_tool("dataset_cluster", {**ARGS, "max_vectors": 100, "clusters": 2}).structured_content

    assert body["scan_truncated"] is True
    assert body["vectors_used"] == 100
    assert "describe a sample" in body["caveat"]


def test_cluster_reads_a_non_multiple_vector_budget(monkeypatch, call_tool):
    pages = [[fake_feature(f"p{p}-{n}", [float(p), float(n)]) for n in range(100)] for p in range(3)]
    build_dataset(monkeypatch, sets=[fake_feature_set()], feature_pages=pages)

    body = call_tool("dataset_cluster", {**ARGS, "max_vectors": 150, "clusters": 2}).structured_content

    # max_vectors=150 must read two pages and keep 150 vectors, not floor to one page (100).
    assert body["scan_truncated"] is True
    assert body["vectors_used"] == 150


def test_cluster_filters_vectors_to_the_named_feature_set(monkeypatch, call_tool):
    dataset = build_dataset(monkeypatch, sets=[fake_feature_set()], feature_pages=separable_pages())

    call_tool("dataset_cluster", {**ARGS, "feature_set_id": "fs-9", "clusters": 2})

    # Mixing two embedding spaces would produce a meaningless partition.
    assert "fs-9" in str(dataset.features.filters_seen[0].prepare())


# --- the algorithm itself -------------------------------------------------------------------


def test_kmeans_is_deterministic_across_runs():
    rng = np.random.default_rng(7)
    data = np.vstack([rng.normal(0, 0.1, (20, 3)), rng.normal(5, 0.1, (20, 3))])

    first = embeddings.kmeans(data, 2)
    second = embeddings.kmeans(data, 2)

    # A fixed seed means the same dataset described twice reads the same way.
    assert np.array_equal(first, second)


def test_kmeans_handles_k_larger_than_the_sample():
    data = np.array([[0.0, 0.0], [1.0, 1.0]])

    labels = embeddings.kmeans(data, 10)

    assert len(labels) == 2
    assert len(set(labels.tolist())) <= 2


# --- visual search -----------------------------------------------------------------------


def test_cosine_similarity_computation():
    matrix = np.array([[1.0, 0.0], [0.0, 1.0], [0.7071, 0.7071], [0.0, 0.0]])
    query = np.array([1.0, 0.0])

    scores = embeddings.cosine_similarity(matrix, query)

    assert len(scores) == 4
    assert pytest.approx(scores[0]) == 1.0
    assert pytest.approx(scores[1]) == 0.0
    assert pytest.approx(scores[2], abs=1e-3) == 0.7071
    assert pytest.approx(scores[3]) == 0.0


def test_visual_search_requires_query_or_vector(monkeypatch, call_tool):
    build_dataset(monkeypatch, sets=[fake_feature_set()])

    result = call_tool("dataset_visual_search", ARGS)
    assert result.is_error is True
    assert "Provide either query or query_vector" in result.content[0].text


def test_visual_search_ranks_matching_items(monkeypatch, call_tool):
    v1 = [1.0, 0.0, 0.0]
    v2 = [0.0, 1.0, 0.0]
    v3 = [0.8, 0.2, 0.0]
    build_dataset(
        monkeypatch,
        sets=[fake_feature_set()],
        feature_pages=[[fake_feature("item-1", v1), fake_feature("item-2", v2), fake_feature("item-3", v3)]],
    )

    args = dict(ARGS, query_vector=[1.0, 0.0, 0.0], limit=2)
    body = call_tool("dataset_visual_search", args).structured_content

    assert body["matches_count"] == 2
    results = body["results"]
    assert results[0]["item_id"] == "item-1"
    assert pytest.approx(results[0]["similarity"]) == 1.0
    assert results[1]["item_id"] == "item-3"
    assert results[1]["similarity"] > 0.5


def test_visual_search_refuses_a_text_query_when_the_feature_set_has_no_model_id(monkeypatch, call_tool):
    """`encode_text_query` (local CLIP) was retired 2026-08-31. A text query is now embedded via
    whichever model embedded the target feature set; if that can't be resolved there is nothing
    to fall back to but an actionable refusal, not a silent local encode.
    """
    build_dataset(monkeypatch, sets=[fake_feature_set(model_id=None)])

    result = call_tool("dataset_visual_search", {**ARGS, "query": "a red car"})

    assert result.is_error is True
    assert "Cannot resolve which model embedded feature set" in result.content[0].text
    assert "query_vector" in result.content[0].text


def text_query_dataset(monkeypatch, feature_pages, missing_items=()):
    """A dataset ready for a text query: one feature set with a model behind it."""
    dataset = build_dataset(
        monkeypatch,
        sets=[fake_feature_set(model_id="model-1")],
        feature_pages=feature_pages,
        missing_items=missing_items,
    )
    fake_model = SimpleNamespace(id="model-1", project_id="proj-1")
    proj = SimpleNamespace(id="proj-1", models=SimpleNamespace(get=lambda model_id=None, **_: fake_model))
    monkeypatch.setattr(embeddings, "get_project", lambda project_id: proj)
    return dataset, fake_model


def stub_embed_query(monkeypatch, vector, item_id="query-item", seen=None):
    from doe_mcp.tools import rag

    def fake_embed_query(dataset, model, query, feature_set_id):
        if seen is not None:
            seen.update(
                {"dataset": dataset, "model": model, "query": query, "feature_set_id": feature_set_id}
            )
        return (
            np.array(vector),
            item_id,
            {
                "vector": {"status": "verified_absent"},
                "item": {"status": "verified_absent"},
            },
        )

    monkeypatch.setattr(rag, "embed_query", fake_embed_query)


def search_with_text_query(monkeypatch, arguments, elicitation_callback):
    """Drive a text query through a real elicitation-capable client.

    The text path is elicitation-gated, so the plain `call_tool` fixture (whose client declares no
    elicitation support) can only ever see a refusal — see `test_visual_search_refuses_a_text_
    query_without_elicitation`.
    """
    import asyncio

    from mcp import Client

    from doe_mcp.server import build_server

    async def run():
        async with Client(build_server(), elicitation_callback=elicitation_callback, mode="legacy") as client:
            return await client.call_tool("dataset_visual_search", arguments)

    return asyncio.run(run())


def test_visual_search_refuses_a_text_query_without_elicitation(monkeypatch, call_tool):
    """Embedding a text query uploads an item and pays for inference, so a client that cannot ask
    a human must refuse rather than do it. No `confirm` parameter exists to bypass this.
    """
    text_query_dataset(monkeypatch, [[fake_feature("item-1", [1.0, 0.0, 0.0])]])
    stub_embed_query(monkeypatch, [1.0, 0.0, 0.0])

    result = call_tool("dataset_visual_search", {**ARGS, "query": "a red car"})

    assert result.is_error is True
    assert "Refusing to embed this text query" in result.content[0].text
    assert "query_vector" in result.content[0].text


def test_visual_search_embeds_a_text_query_via_the_deployed_model(monkeypatch):
    """Regression: this used to run CLIP in-process. It must now route through the same
    deployed-model pattern `rag_query` uses -- never locally.
    """
    import mcp_types as types

    dataset, fake_model = text_query_dataset(
        monkeypatch,
        [[fake_feature("item-1", [1.0, 0.0, 0.0]), fake_feature("item-2", [0.0, 1.0, 0.0])]],
    )
    seen: dict = {}
    stub_embed_query(monkeypatch, [1.0, 0.0, 0.0], seen=seen)

    async def accept(context, params):
        return types.ElicitResult(action="accept", content={"confirmed": True})

    body = search_with_text_query(monkeypatch, {**ARGS, "query": "a red car"}, accept).structured_content

    assert seen == {
        "dataset": dataset,
        "model": fake_model,
        "query": "a red car",
        "feature_set_id": "fs-1",
    }
    assert body["results"][0]["item_id"] == "item-1"
    assert pytest.approx(body["results"][0]["similarity"]) == 1.0


def test_visual_search_never_returns_the_query_item_it_just_embedded(monkeypatch):
    """Regression: embed_query writes the query's vector into the very feature set being scanned,
    so it matched itself at similarity 1.0 and came back as the top hit -- pointing at an item
    that embed_query had already deleted.
    """
    import mcp_types as types

    text_query_dataset(
        monkeypatch,
        [
            [
                # The query's own vector, as the platform left it behind.
                fake_feature("query-item", [1.0, 0.0, 0.0]),
                fake_feature("item-1", [0.9, 0.1, 0.0]),
            ]
        ],
    )
    stub_embed_query(monkeypatch, [1.0, 0.0, 0.0], item_id="query-item")

    async def accept(context, params):
        return types.ElicitResult(action="accept", content={"confirmed": True})

    body = search_with_text_query(monkeypatch, {**ARGS, "query": "a red car"}, accept).structured_content

    assert [hit["item_id"] for hit in body["results"]] == ["item-1"]
    assert body["vectors_scanned"] == 1, "the query's own vector must not even be scanned"


def test_visual_search_drops_vectors_whose_item_no_longer_exists(monkeypatch, call_tool):
    """Residue from *earlier* text searches: the item is gone but its vector still ranks. A
    dangling id looks like a result, so it is dropped and counted instead.
    """
    build_dataset(
        monkeypatch,
        sets=[fake_feature_set()],
        feature_pages=[
            [fake_feature("deleted-query", [1.0, 0.0, 0.0]), fake_feature("item-1", [0.9, 0.1, 0.0])]
        ],
        missing_items=["deleted-query"],
    )

    body = call_tool("dataset_visual_search", {**ARGS, "query_vector": [1.0, 0.0, 0.0]}).structured_content

    assert [hit["item_id"] for hit in body["results"]] == ["item-1"]
    assert body["stale_vectors_skipped"] == 1


def test_visual_search_reports_an_unknown_feature_set_as_such(monkeypatch, call_tool):
    """Regression: an id not in this dataset fell through as `None` and was reported either as a
    missing model_id or as "no usable vectors" -- both wrong diagnoses of a wrong id.
    """
    build_dataset(monkeypatch, sets=[fake_feature_set(set_id="fs-1")])

    result = call_tool(
        "dataset_visual_search", {**ARGS, "query_vector": [1.0, 0.0], "feature_set_id": "fs-nope"}
    )

    assert result.is_error is True
    assert "Feature set fs-nope is not in dataset ds-1" in result.content[0].text


def test_visual_search_respects_min_similarity(monkeypatch, call_tool):
    v1 = [1.0, 0.0, 0.0]
    v2 = [0.0, 1.0, 0.0]
    build_dataset(
        monkeypatch,
        sets=[fake_feature_set()],
        feature_pages=[[fake_feature("item-1", v1), fake_feature("item-2", v2)]],
    )

    args = dict(ARGS, query_vector=[1.0, 0.0, 0.0], min_similarity=0.5)
    body = call_tool("dataset_visual_search", args).structured_content

    assert body["matches_count"] == 1
    assert body["results"][0]["item_id"] == "item-1"


def test_visual_search_refuses_text_query_without_human_elicitation(monkeypatch, call_tool):
    build_dataset(monkeypatch, sets=[fake_feature_set()])

    result = call_tool("dataset_visual_search", {**ARGS, "query": "a red car"})

    assert result.is_error is True
    assert "query_vector" in result.content[0].text


def test_visual_search_rejects_unknown_feature_set(monkeypatch, call_tool):
    build_dataset(monkeypatch, sets=[fake_feature_set()])

    result = call_tool(
        "dataset_visual_search",
        {**ARGS, "feature_set_id": "fs-missing", "query_vector": [1.0, 0.0]},
    )

    assert result.is_error is True
    assert "not in dataset" in result.content[0].text
