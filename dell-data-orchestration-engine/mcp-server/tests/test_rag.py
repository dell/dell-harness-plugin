# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for RAG (Retrieval-Augmented Generation) pipeline tools.

These tests verify that RAG pipeline creation, node addition, and querying work correctly.
They use mocked DOE entities to avoid requiring a live platform instance.

The load-bearing assertion in here is the storage contract: chunks are items, and embeddings live
in a feature set. No generated node code may write a vector into item metadata — a vector there
would be invisible to `dataset.features` and to the platform's own Clustering app, and would be a
second source of truth for `rag_query` to disagree with.
"""

import ast
import io
from types import SimpleNamespace
from unittest.mock import MagicMock

import dtlpy as dl
import pytest

from doe_mcp.tools import rag
from tests.conftest import OwnedRepo


@pytest.fixture
def mock_project():
    """Create a mock DOE project."""
    project = SimpleNamespace(
        id="proj-1",
        name="test_project",
        pipelines=MagicMock(),
        datasets=MagicMock(),
        models=MagicMock(),
    )
    return project


@pytest.fixture
def mock_pipeline():
    """Create a mock DOE pipeline with nodes and update method."""
    pipeline = dl.entities.Pipeline.from_json(
        _json={
            "id": "pipe-1",
            "name": "test_rag_pipeline",
            "projectId": "proj-1",
            "nodes": [],
            "connections": [],
            "startNodes": [],
            "variables": [],
        },
        client_api=dl.client_api,
        project=SimpleNamespace(
            id="proj-1", name="test_project", datasets=OwnedRepo("proj-1"), models=OwnedRepo("proj-1")
        ),
        is_fetched=False,
    )
    # Keep the graph local: update() would otherwise PATCH the platform.
    pipeline.update = lambda: pipeline
    return pipeline


@pytest.fixture
def mock_dataset():
    """Create a mock DOE dataset."""
    dataset = SimpleNamespace(
        id="ds-1",
        name="test_dataset",
        items=MagicMock(),
        features=MagicMock(),
        feature_sets=MagicMock(),
    )
    return dataset


def node_code(pipeline, node_name):
    """The source a code node will run on DOE."""
    return pipeline.nodes.get(node_name=node_name).config["package"]["code"]


def chunk_item(item_id, name, chunk_index, text):
    """A chunk item as rag_query sees it: provenance in metadata, text in the binary."""
    return SimpleNamespace(
        id=item_id,
        name=name,
        metadata={
            "user": {
                "rag": {
                    "role": "chunk",
                    "text_item_id": "text-1",
                    "source_item_id": "pdf-1",
                    "source_item_name": "policy.pdf",
                    "chunk_index": chunk_index,
                }
            }
        },
        download=lambda save_locally=False, _text=text: io.BytesIO(_text.encode("utf-8")),
    )


def test_rag_pipeline_create(call_tool, monkeypatch, mock_project, mock_pipeline):
    """Test creating a RAG pipeline and setting description."""
    mock_project.pipelines.create.return_value = mock_pipeline
    mock_project.pipelines.update.return_value = mock_pipeline
    monkeypatch.setattr("doe_mcp.tools.rag.get_project", lambda pid: mock_project)

    result = call_tool(
        "rag_pipeline_create",
        {
            "project_id": "proj-1",
            "name": "my-rag",
            "source_dataset_id": "ds-in",
            "output_dataset_id": "ds-out",
            "description": "A test RAG pipeline",
        },
    )

    assert result.is_error is False
    assert result.structured_content["id"] == "pipe-1"
    mock_project.pipelines.create.assert_called_once_with(name="my-rag", project_id="proj-1")
    assert mock_pipeline.description == "A test RAG pipeline"


def test_rag_add_pdf_filter_node(call_tool, monkeypatch, mock_pipeline):
    """Test adding a PDF filter node with DQL filter."""
    monkeypatch.setattr("doe_mcp.tools.pipeline_nodes.get_pipeline", lambda pid, ppid: mock_pipeline)

    result = call_tool(
        "rag_add_pdf_filter_node",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "source_dataset_id": "ds-source",
            "node_name": "pdf_filter",
            "load_existing": True,
        },
    )

    assert result.is_error is False
    assert result.structured_content["node"]["name"] == "pdf_filter"
    assert len(mock_pipeline.nodes) == 1
    node = mock_pipeline.nodes[0]
    assert node.name == "pdf_filter"
    assert node.load_existing_data is True
    assert "application/pdf" in node.metadata["triggerToPipeline"]["filter"]


def test_rag_add_duplicate_node_name_fails(call_tool, monkeypatch, mock_pipeline):
    """Adding a node with a duplicate name must return an error result."""
    monkeypatch.setattr("doe_mcp.tools.pipeline_nodes.get_pipeline", lambda pid, ppid: mock_pipeline)

    call_tool(
        "rag_add_pdf_filter_node",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "source_dataset_id": "ds-source",
            "node_name": "pdf_filter",
        },
    )

    result = call_tool(
        "rag_add_pdf_filter_node",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "source_dataset_id": "ds-source",
            "node_name": "pdf_filter",
        },
    )

    assert result.is_error is True
    assert "already has a node named 'pdf_filter'" in result.content[0].text


def test_rag_add_pdf_to_text_node(call_tool, monkeypatch, mock_pipeline):
    """PDF text is extracted on DOE and uploaded as a text item, not stuffed into metadata."""
    monkeypatch.setattr("doe_mcp.tools.pipeline_nodes.get_pipeline", lambda pid, ppid: mock_pipeline)

    result = call_tool(
        "rag_add_pdf_to_text_node",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "output_dataset_id": "ds-out",
            "node_name": "pdf_to_text",
        },
    )

    assert result.is_error is False
    assert result.structured_content["node"]["name"] == "pdf_to_text"
    code = node_code(mock_pipeline, "pdf_to_text")
    assert "pypdf" in code
    assert "item.download(save_locally=False)" in code
    assert 'dataset_id="ds-out"' in code
    assert '"/rag/text"' in code
    assert "items.upload" in code
    # The document text is the item's binary now; nothing dumps it into metadata.
    assert "extracted_text" not in code
    assert "text_preview" not in code


def test_rag_add_text_to_chunks_node(call_tool, monkeypatch, mock_pipeline):
    """Chunks become items in the output dataset, carrying only provenance in metadata."""
    monkeypatch.setattr("doe_mcp.tools.pipeline_nodes.get_pipeline", lambda pid, ppid: mock_pipeline)

    result = call_tool(
        "rag_add_text_to_chunks_node",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "output_dataset_id": "ds-out",
            "chunk_size": 1024,
            "chunk_overlap": 128,
            "node_name": "chunker",
        },
    )

    assert result.is_error is False
    assert result.structured_content["node"]["name"] == "chunker"
    code = node_code(mock_pipeline, "chunker")
    assert "chunk_size = 1024" in code
    assert "chunk_overlap = 128" in code
    assert 'dataset_id="ds-out"' in code
    assert '"/rag/chunks"' in code
    assert '"role": "chunk"' in code
    assert '"text_item_id": item.id' in code


def test_rag_add_embedding_node_delegates_to_model(call_tool, monkeypatch, mock_pipeline):
    """The embedding node calls a deployed model; the model writes the feature set."""
    monkeypatch.setattr("doe_mcp.tools.pipeline_nodes.get_pipeline", lambda pid, ppid: mock_pipeline)

    result = call_tool(
        "rag_add_embedding_node",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "output_dataset_id": "ds-out",
            "model_id": "model-1",
            "node_name": "embedder",
        },
    )

    assert result.is_error is False
    assert result.structured_content["node"]["name"] == "embedder"
    code = node_code(mock_pipeline, "embedder")
    assert 'models.get(model_id="model-1")' in code
    assert "model.embed(item_ids=batch)" in code
    assert 'field="metadata.user.rag.text_item_id"' in code


def test_generated_node_code_never_writes_embeddings_to_metadata(call_tool, monkeypatch, mock_pipeline):
    """No node may put a vector in item metadata: the feature set is the only store."""
    monkeypatch.setattr("doe_mcp.tools.pipeline_nodes.get_pipeline", lambda pid, ppid: mock_pipeline)

    call_tool(
        "rag_add_pdf_to_text_node",
        {"project_id": "proj-1", "pipeline_id": "pipe-1", "output_dataset_id": "ds-out"},
    )
    call_tool(
        "rag_add_text_to_chunks_node",
        {"project_id": "proj-1", "pipeline_id": "pipe-1", "output_dataset_id": "ds-out"},
    )
    call_tool(
        "rag_add_embedding_node",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "output_dataset_id": "ds-out",
            "model_id": "model-1",
        },
    )

    for node_name in ("pdf_to_text", "text_to_chunks", "generate_embeddings"):
        code = node_code(mock_pipeline, node_name)
        # Parses as Python, so a placeholder substitution cannot ship a syntax error to a runner.
        ast.parse(code)
        assert '"embeddings"' not in code
        assert '"embedding"' not in code

    embed_code = node_code(mock_pipeline, "generate_embeddings")
    assert "item.metadata" not in embed_code
    assert "item.update()" not in embed_code


def test_rag_add_output_node(call_tool, monkeypatch, mock_pipeline):
    """Test adding output storage dataset node."""
    monkeypatch.setattr("doe_mcp.tools.pipeline_nodes.get_pipeline", lambda pid, ppid: mock_pipeline)

    result = call_tool(
        "rag_add_output_node",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "output_dataset_id": "ds-target",
            "node_name": "output_storage",
        },
    )

    assert result.is_error is False
    assert result.structured_content["node"]["name"] == "output_storage"
    node = mock_pipeline.nodes.get(node_name="output_storage")
    assert node is not None
    assert node.dataset_id == "ds-target"


def test_rag_pipeline_complete_sequence(call_tool, monkeypatch, mock_pipeline):
    """Test building the complete 5-node RAG pipeline sequence."""
    monkeypatch.setattr("doe_mcp.tools.pipeline_nodes.get_pipeline", lambda pid, ppid: mock_pipeline)

    call_tool(
        "rag_add_pdf_filter_node",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "source_dataset_id": "ds-src",
            "node_name": "1_pdf_filter",
        },
    )
    call_tool(
        "rag_add_pdf_to_text_node",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "output_dataset_id": "ds-dst",
            "node_name": "2_pdf_to_text",
        },
    )
    call_tool(
        "rag_add_text_to_chunks_node",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "output_dataset_id": "ds-dst",
            "node_name": "3_text_to_chunks",
        },
    )
    call_tool(
        "rag_add_embedding_node",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "output_dataset_id": "ds-dst",
            "model_id": "model-1",
            "node_name": "4_embed",
        },
    )
    call_tool(
        "rag_add_output_node",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "output_dataset_id": "ds-dst",
            "node_name": "5_output",
        },
    )

    assert len(mock_pipeline.nodes) == 5
    node_names = [n.name for n in mock_pipeline.nodes]
    assert node_names == ["1_pdf_filter", "2_pdf_to_text", "3_text_to_chunks", "4_embed", "5_output"]


@pytest.fixture
def rag_query_platform(monkeypatch, mock_project, mock_dataset):
    """A dataset with one feature set, two chunk vectors, and a model that embeds a query."""
    query_item = SimpleNamespace(id="q-1", delete=MagicMock())
    mock_dataset.items.upload.return_value = query_item

    feature_set = SimpleNamespace(id="fs-1", name="clip", model_id="model-1")
    mock_dataset.feature_sets.list.return_value = SimpleNamespace(items=[feature_set])
    query_feature = SimpleNamespace(id="f-q", entity_id="q-1", value=[1.0, 0.0], delete=MagicMock())
    mock_dataset.features.list.side_effect = [
        # Polling finds the query vector.
        SimpleNamespace(items=[query_feature]),
        # Cleanup discovers and deletes that vector, then verifies it is absent.
        SimpleNamespace(items=[query_feature]),
        SimpleNamespace(items=[]),
        # Final call: the chunk vectors to rank against the cleaned-up query.
        SimpleNamespace(
            items=[
                SimpleNamespace(id="f-1", entity_id="chunk-1", value=[0.0, 1.0]),
                SimpleNamespace(id="f-2", entity_id="chunk-2", value=[1.0, 0.0]),
            ],
            has_next_page=False,
        ),
    ]
    mock_dataset.items.list.return_value = SimpleNamespace(items=[])

    chunks = {
        "chunk-1": chunk_item("chunk-1", "policy_chunk_00000.txt", 0, "General intro to company."),
        "chunk-2": chunk_item("chunk-2", "policy_chunk_00001.txt", 1, "Returns accepted within 30 days."),
    }
    mock_dataset.items.get.side_effect = lambda item_id: chunks[item_id]

    model = SimpleNamespace(
        id="model-1", project_id="proj-1", embed=MagicMock(return_value=SimpleNamespace(wait=lambda: None))
    )
    mock_project.models.get.return_value = model

    monkeypatch.setattr("doe_mcp.tools.rag.get_dataset", lambda pid, did: mock_dataset)
    monkeypatch.setattr("doe_mcp.tools.rag.get_project", lambda pid: mock_project)
    monkeypatch.setattr("doe_mcp.tools.rag.elicit_or_refuse", lambda *args, **kwargs: None)
    return SimpleNamespace(
        dataset=mock_dataset, model=model, query_item=query_item, query_feature=query_feature
    )


def test_rag_query_ranks_chunks_by_vector_similarity(call_tool, rag_query_platform):
    """The query is embedded by the same model, then chunks are ranked by cosine similarity."""
    result = call_tool(
        "rag_query",
        {
            "project_id": "proj-1",
            "dataset_id": "ds-out",
            "query": "returns",
            "model_id": "model-1",
            "max_results": 5,
        },
    )

    assert result.is_error is False
    sc = result.structured_content
    assert sc["feature_set_id"] == "fs-1"
    assert sc["query_cleanup"]["vector"]["status"] == "verified_absent"
    assert sc["query_cleanup"]["item"]["status"] == "verified_absent"
    assert sc["vectors_scanned"] == 2
    assert sc["results_count"] == 2
    # chunk-2's vector is identical to the query's, so it must rank first.
    assert [r["item_id"] for r in sc["results"]] == ["chunk-2", "chunk-1"]
    assert sc["results"][0]["similarity"] == 1.0
    assert sc["results"][0]["text"] == "Returns accepted within 30 days."
    assert sc["results"][0]["source_item_name"] == "policy.pdf"
    rag_query_platform.model.embed.assert_called_once_with(item_ids=["q-1"])


def test_rag_query_deletes_its_throwaway_query_item(call_tool, rag_query_platform):
    """The uploaded query item exists only to be embedded; it must not be left behind."""
    call_tool(
        "rag_query",
        {"project_id": "proj-1", "dataset_id": "ds-out", "query": "returns", "model_id": "model-1"},
    )

    rag_query_platform.query_item.delete.assert_called_once()
    rag_query_platform.query_feature.delete.assert_called_once()
    rag_query_platform.dataset.items.list.assert_called_once()


def test_rag_query_applies_min_similarity(call_tool, rag_query_platform):
    """Chunks below the threshold are dropped rather than padded into the answer."""
    result = call_tool(
        "rag_query",
        {
            "project_id": "proj-1",
            "dataset_id": "ds-out",
            "query": "returns",
            "model_id": "model-1",
            "min_similarity": 0.5,
        },
    )

    sc = result.structured_content
    assert sc["results_count"] == 1
    assert sc["results"][0]["item_id"] == "chunk-2"


def test_rag_query_skips_vectors_whose_item_is_gone(call_tool, rag_query_platform):
    """A vector can outlive its item — a deleted query item ranks perfectly and means nothing."""
    dataset = rag_query_platform.dataset
    dataset.features.list.side_effect = [
        SimpleNamespace(items=[rag_query_platform.query_feature]),
        SimpleNamespace(items=[rag_query_platform.query_feature]),
        SimpleNamespace(items=[]),
        SimpleNamespace(
            items=[
                # Left behind by an earlier query, and an exact match for this one.
                SimpleNamespace(id="f-stale", entity_id="q-0", value=[1.0, 0.0]),
                SimpleNamespace(id="f-1", entity_id="chunk-1", value=[0.0, 1.0]),
            ],
            has_next_page=False,
        ),
    ]
    live = {"chunk-1": chunk_item("chunk-1", "policy_chunk_00000.txt", 0, "General intro.")}

    def get(item_id):
        if item_id not in live:
            raise ValueError("Item not found")
        return live[item_id]

    dataset.items.get.side_effect = get

    result = call_tool(
        "rag_query",
        {"project_id": "proj-1", "dataset_id": "ds-out", "query": "returns", "model_id": "model-1"},
    )

    assert result.is_error is False
    assert [r["item_id"] for r in result.structured_content["results"]] == ["chunk-1"]


def test_rag_query_skips_vectors_for_non_chunk_items(call_tool, rag_query_platform):
    """Only chunk items are answers; a vector on the text item is not a retrievable passage."""
    dataset = rag_query_platform.dataset
    dataset.features.list.side_effect = [
        SimpleNamespace(items=[rag_query_platform.query_feature]),
        SimpleNamespace(items=[rag_query_platform.query_feature]),
        SimpleNamespace(items=[]),
        SimpleNamespace(
            items=[
                SimpleNamespace(id="f-t", entity_id="text-1", value=[1.0, 0.0]),
                SimpleNamespace(id="f-1", entity_id="chunk-1", value=[0.0, 1.0]),
            ],
            has_next_page=False,
        ),
    ]
    text_item = SimpleNamespace(id="text-1", name="policy.txt", metadata={"user": {"rag": {"role": "text"}}})
    items = {"text-1": text_item, "chunk-1": chunk_item("chunk-1", "c.txt", 0, "General intro.")}
    dataset.items.get.side_effect = lambda item_id: items[item_id]

    result = call_tool(
        "rag_query",
        {"project_id": "proj-1", "dataset_id": "ds-out", "query": "returns", "model_id": "model-1"},
    )

    assert [r["item_id"] for r in result.structured_content["results"]] == ["chunk-1"]


def test_rag_query_without_feature_set_is_an_error(call_tool, monkeypatch, mock_project, mock_dataset):
    """Nothing embedded yet means say so, not return an empty result that reads as 'no match'."""
    mock_dataset.feature_sets.list.return_value = SimpleNamespace(items=[])
    mock_project.models.get.return_value = SimpleNamespace(
        id="model-1", project_id="proj-1", embed=MagicMock()
    )
    monkeypatch.setattr("doe_mcp.tools.rag.get_dataset", lambda pid, did: mock_dataset)
    monkeypatch.setattr("doe_mcp.tools.rag.get_project", lambda pid: mock_project)

    result = call_tool(
        "rag_query",
        {"project_id": "proj-1", "dataset_id": "ds-1", "query": "returns", "model_id": "model-1"},
    )

    assert result.is_error is True
    assert "no feature set" in result.content[0].text
    mock_dataset.items.upload.assert_not_called()


# --- upstream behaviour the generated code-node bodies depend on ------------------------


def test_item_from_json_without_a_project_id_has_no_project():
    """Pin why the generated node bodies resolve resources via dl.* instead of item.project.

    The item JSON a pipeline code node receives can omit `projectId`, and dtlpy then leaves
    `item.project` as None rather than raising or resolving it -- so `item.project.datasets.get(...)`
    inside a code node is a latent AttributeError. The bodies use `dl.datasets.get(dataset_id=...)`
    instead, which returns a fully project-bound Dataset, so writes are scoped by that dataset.

    Note this is NOT a breach of the AGENTS.md "always use project.<repo>" rule: that governs tool
    bodies in this server process, where `Pipelines.__init__` fabricates an empty Project. These
    bodies run on DOE, where there is no project to resolve in the first place.
    """
    item = dl.entities.Item.from_json(
        _json={"id": "it-1", "name": "a.pdf", "type": "file", "filename": "/a.pdf", "metadata": {}},
        client_api=dl.client_api,
        dataset=None,
    )

    assert item.project is None, "if this ever holds, the dl.* workaround can be revisited"


@pytest.mark.parametrize(
    "template",
    ["PDF_TO_TEXT_CODE", "TEXT_TO_CHUNKS_CODE", "EMBEDDING_CODE"],
)
def test_generated_code_is_pure_ascii(template):
    """A non-ASCII character in a code node's body is silently truncated by the platform.

    Silently: no error, just a short package that fails at runtime in a confusing way. The note
    lived in one template's comment and was asserted nowhere, so an em-dash typed into any of the
    three would have shipped.
    """
    source = getattr(rag, template)

    assert source.isascii(), "non-ASCII in a code-node body is silently truncated by the platform"


@pytest.mark.parametrize(
    "template",
    ["PDF_TO_TEXT_CODE", "TEXT_TO_CHUNKS_CODE", "EMBEDDING_CODE"],
)
def test_generated_code_never_reaches_for_item_project(template):
    """Regression guard for the workaround above, at the point it would be undone.

    Checked against the parsed AST, not the raw text: every template *mentions* `item.project` in
    the comment explaining why it is avoided, so a substring search would pass vacuously today and
    for the wrong reason tomorrow.
    """
    tree = ast.parse(getattr(rag, template))

    reaches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "project"
        and isinstance(node.value, ast.Name)
        and node.value.id == "item"
    ]

    assert reaches == [], "item.project is None inside a code-node runner; resolve via dl.* instead"


def test_generated_node_identifiers_are_inert_python_literals(call_tool, monkeypatch, mock_pipeline):
    """Quotes, newlines, comments and Unicode in all four ids stay AST string constants."""
    monkeypatch.setattr("doe_mcp.tools.pipeline_nodes.get_pipeline", lambda pid, ppid: mock_pipeline)
    dataset_id = 'ds");\n__import__("os").system("touch /tmp/rag-pwned")\n# snowman: ☃'
    model_id = 'model\'");\nraise RuntimeError("injected")\n#'

    call_tool(
        "rag_add_pdf_to_text_node",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "output_dataset_id": dataset_id,
            "node_name": "safe_pdf",
        },
    )
    call_tool(
        "rag_add_text_to_chunks_node",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "output_dataset_id": dataset_id,
            "node_name": "safe_chunks",
        },
    )
    call_tool(
        "rag_add_embedding_node",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "output_dataset_id": dataset_id,
            "model_id": model_id,
            "node_name": "safe_embed",
        },
    )

    cases = [
        (node_code(mock_pipeline, "safe_pdf"), [dataset_id]),
        (node_code(mock_pipeline, "safe_chunks"), [dataset_id]),
        (node_code(mock_pipeline, "safe_embed"), [dataset_id, model_id]),
    ]
    for source, expected_values in cases:
        tree = ast.parse(source)
        constants = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)]
        assert all(value in constants for value in expected_values)
        assert source.isascii()
        assert "_LITERAL__" not in source
        assert not any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "system"
            for node in ast.walk(tree)
        )


def test_rag_query_refuses_side_effects_without_human_elicitation(
    call_tool, monkeypatch, mock_project, mock_dataset
):
    feature_set = SimpleNamespace(id="fs-1", name="clip", model_id="model-1")
    mock_dataset.feature_sets.list.return_value = SimpleNamespace(items=[feature_set])
    mock_project.models.get.return_value = SimpleNamespace(
        id="model-1", project_id="proj-1", embed=MagicMock()
    )
    monkeypatch.setattr("doe_mcp.tools.rag.get_dataset", lambda pid, did: mock_dataset)
    monkeypatch.setattr("doe_mcp.tools.rag.get_project", lambda pid: mock_project)

    result = call_tool(
        "rag_query",
        {"project_id": "proj-1", "dataset_id": "ds-1", "query": "returns", "model_id": "model-1"},
    )

    assert result.is_error is True
    assert "Refusing to upload and embed a temporary RAG query item" in result.content[0].text
    mock_dataset.items.upload.assert_not_called()


def test_embed_query_preserves_primary_error_and_reports_cleanup_failures():
    item = SimpleNamespace(id="q-1", delete=MagicMock(side_effect=RuntimeError("delete boom")))
    dataset = SimpleNamespace(items=MagicMock(), features=MagicMock())
    dataset.items.upload.return_value = item
    dataset.items.list.return_value = SimpleNamespace(items=[item])
    dataset.features.list.side_effect = [
        SimpleNamespace(items=[]),
        SimpleNamespace(items=[]),
    ]
    model = SimpleNamespace(
        id="model-1", project_id="proj-1", embed=MagicMock(side_effect=RuntimeError("embed boom"))
    )

    with pytest.raises(RuntimeError) as excinfo:
        rag.embed_query(dataset, model, "query", "fs-1")

    message = str(excinfo.value)
    assert "Query embedding failed" in message
    assert "embed boom" in message
    assert "cleanup also failed" in message
    assert "delete boom" in message
    assert "query item still exists" in message


def test_embed_query_reports_residual_vector_after_delete():
    item = SimpleNamespace(id="q-1", delete=MagicMock())
    feature = SimpleNamespace(id="f-q", entity_id="q-1", value=[1.0, 0.0], delete=MagicMock())
    dataset = SimpleNamespace(items=MagicMock(), features=MagicMock())
    dataset.items.upload.return_value = item
    dataset.items.list.return_value = SimpleNamespace(items=[])
    dataset.features.list.side_effect = [
        SimpleNamespace(items=[feature]),
        SimpleNamespace(items=[feature]),
        SimpleNamespace(items=[feature]),
    ]
    model = SimpleNamespace(id="model-1", project_id="proj-1", embed=MagicMock(return_value=None))

    with pytest.raises(RuntimeError, match="embedding succeeded but cleanup failed") as excinfo:
        rag.embed_query(dataset, model, "query", "fs-1")

    assert "query vector still exists: f-q" in str(excinfo.value)
    feature.delete.assert_called_once()
    item.delete.assert_called_once()


class GeneratedChunkItems:
    """Small item repository that executes the generated chunker's overwrite/cleanup contract."""

    def __init__(self):
        self.by_name = {}
        self.current_source_id = None
        self.prepared_filters = []

    def upload(self, *, remote_name, item_metadata, **_):
        entity = SimpleNamespace(name=remote_name, metadata=item_metadata)

        def delete(name=remote_name):
            self.by_name.pop(name, None)

        entity.delete = delete
        self.by_name[remote_name] = entity
        return entity

    def list(self, filters):
        self.prepared_filters.append(str(filters.prepare()))
        visible = [
            entity
            for entity in self.by_name.values()
            if entity.metadata["user"]["rag"]["source_item_id"] == self.current_source_id
        ]
        return SimpleNamespace(all=lambda: list(visible))


def generated_chunk_runner(call_tool, monkeypatch, mock_pipeline):
    monkeypatch.setattr("doe_mcp.tools.pipeline_nodes.get_pipeline", lambda pid, ppid: mock_pipeline)
    result = call_tool(
        "rag_add_text_to_chunks_node",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "output_dataset_id": "ds-out",
            "chunk_size": 100,
            "chunk_overlap": 0,
            "node_name": "generated_chunker",
        },
    )
    assert result.is_error is False
    namespace = {}
    source = (
        rag.TEXT_TO_CHUNKS_CODE.replace("__OUTPUT_DATASET_ID_LITERAL__", rag.python_string_literal("ds-out"))
        .replace("__CHUNK_FOLDER__", rag.CHUNK_FOLDER)
        .replace("__CHUNK_SIZE__", "100")
        .replace("__CHUNK_OVERLAP__", "0")
    )
    exec(compile(source, "<generated-chunker>", "exec"), namespace)  # noqa: S102
    repository = GeneratedChunkItems()
    dataset = SimpleNamespace(items=repository)
    monkeypatch.setattr(rag.dl.datasets, "get", lambda dataset_id: dataset)

    def run(source_item_id, text):
        repository.current_source_id = source_item_id
        item = SimpleNamespace(
            id=f"text-{source_item_id}",
            name="report.txt",
            metadata={
                "user": {
                    "rag": {
                        "source_item_id": source_item_id,
                        "source_item_name": "report.pdf",
                    }
                }
            },
            download=lambda save_locally=False: io.BytesIO(text.encode("utf-8")),
            update=MagicMock(),
        )
        namespace["run"](item)
        return item

    return repository, run


def test_generated_chunks_with_same_basename_are_scoped_by_source_id(call_tool, monkeypatch, mock_pipeline):
    repository, run = generated_chunk_runner(call_tool, monkeypatch, mock_pipeline)

    run("pdf-a", "A" * 150)
    names_a = {
        name
        for name, entity in repository.by_name.items()
        if entity.metadata["user"]["rag"]["source_item_id"] == "pdf-a"
    }
    run("pdf-b", "B" * 150)
    names_b = {
        name
        for name, entity in repository.by_name.items()
        if entity.metadata["user"]["rag"]["source_item_id"] == "pdf-b"
    }

    assert len(names_a) == 2
    assert len(names_b) == 2
    assert names_a.isdisjoint(names_b)


def test_generated_chunk_rerun_removes_only_stale_chunks_for_that_source(
    call_tool, monkeypatch, mock_pipeline
):
    repository, run = generated_chunk_runner(call_tool, monkeypatch, mock_pipeline)
    run("pdf-b", "B" * 250)
    names_b = {
        name
        for name, entity in repository.by_name.items()
        if entity.metadata["user"]["rag"]["source_item_id"] == "pdf-b"
    }
    run("pdf-a", "A" * 250)
    run("pdf-a", "A" * 80)

    remaining_a = [
        entity
        for entity in repository.by_name.values()
        if entity.metadata["user"]["rag"]["source_item_id"] == "pdf-a"
    ]
    remaining_b = {
        name
        for name, entity in repository.by_name.items()
        if entity.metadata["user"]["rag"]["source_item_id"] == "pdf-b"
    }
    assert len(remaining_a) == 1
    assert remaining_b == names_b
    assert any("metadata.user.rag.source_item_id" in value for value in repository.prepared_filters)
    assert any("metadata.user.rag.role" in value for value in repository.prepared_filters)
