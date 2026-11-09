# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Two RAG pipelines that share an output dataset never overwrite or delete each other's items.

The code each ``rag_add_*`` tool sends to the platform is captured verbatim and executed against
one shared, in-memory output dataset that honours ``remote_path``/``remote_name``/``overwrite`` and
evaluates the DQL filters the code builds. Pipeline A processes a source document, then pipeline B
processes the same document with a different chunk size; A's text item and every one of its
chunks must survive unchanged.
"""

import copy
import io
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from doe_mcp.tools import rag
from tests.test_pipeline_nodes import build_pipeline

SOURCE_ID = "pdf-shared"


class SharedOutputDataset:
    """A dataset keyed by full remote path, as the platform stores items."""

    def __init__(self):
        self.by_path: dict[str, SimpleNamespace] = {}
        self.overwritten: list[str] = []
        self.deleted: list[str] = []
        self.items = self
        self._next = 0

    def upload(self, local_path, remote_path, remote_name, overwrite=False, item_metadata=None, **_):
        path = f"{remote_path.rstrip('/')}/{remote_name}"
        if path in self.by_path:
            if not overwrite:
                return None
            self.overwritten.append(path)
        self._next += 1
        data = local_path.getvalue() if hasattr(local_path, "getvalue") else local_path
        entity = SimpleNamespace(
            id=f"item-{self._next}",
            name=remote_name,
            path=path,
            metadata=copy.deepcopy(item_metadata),
            data=data,
            update=MagicMock(),
        )
        entity.download = lambda save_locally=False, _data=data: io.BytesIO(_data)

        def delete(_path=path):
            self.deleted.append(_path)
            self.by_path.pop(_path, None)

        entity.delete = delete
        self.by_path[path] = entity
        return entity

    def list(self, filters):
        clauses = filters.prepare()["filter"]["$and"]

        def lookup(entity, field):
            value = {"metadata": entity.metadata, "type": "file", "hidden": False}
            for part in field.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            return value

        matches = [
            entity
            for entity in list(self.by_path.values())
            if all(
                lookup(entity, field) == expected for clause in clauses for field, expected in clause.items()
            )
        ]
        return SimpleNamespace(all=lambda: list(matches))


@pytest.fixture
def captured_code(monkeypatch):
    """Map (pipeline_id, node name) to the exact source the tool puts in the code node."""
    captured: dict[tuple[str, str], str] = {}
    pipelines: dict[str, object] = {}
    real_add_node = rag.pipeline_nodes.add_node

    def get_pipeline(project_id, pipeline_id):
        return pipelines.setdefault(pipeline_id, build_pipeline())

    def add_node(project_id, pipeline_id, spec, references=()):
        captured[(pipeline_id, spec.name)] = spec.code
        return real_add_node(project_id, pipeline_id, spec, references)

    monkeypatch.setattr(rag.pipeline_nodes, "get_pipeline", get_pipeline)
    monkeypatch.setattr(rag.pipeline_nodes, "add_node", add_node)
    return captured


def build(call_tool, captured, pipeline_id, chunk_size):
    for tool, extra in (
        ("rag_add_pdf_to_text_node", {"node_name": "to_text"}),
        (
            "rag_add_text_to_chunks_node",
            {"node_name": "to_chunks", "chunk_size": chunk_size, "chunk_overlap": 0},
        ),
        ("rag_add_embedding_node", {"node_name": "embed", "model_id": "model-1"}),
    ):
        result = call_tool(
            tool, {"project_id": "proj-1", "pipeline_id": pipeline_id, "output_dataset_id": "ds-out", **extra}
        )
        assert result.is_error is False, result.content[0].text
    functions = {}
    for name in ("to_text", "to_chunks", "embed"):
        namespace: dict = {}
        exec(compile(captured[(pipeline_id, name)], f"<{pipeline_id}-{name}>", "exec"), namespace)  # noqa: S102
        functions[name] = namespace["run"]
    return functions


def run_pipeline(functions, text):
    source = SimpleNamespace(
        id=SOURCE_ID,
        name="handbook.pdf",
        download=lambda save_locally=False: io.BytesIO(b"%PDF"),
    )
    page = SimpleNamespace(extract_text=lambda: text)
    pypdf = SimpleNamespace(PdfReader=lambda stream: SimpleNamespace(pages=[page]))
    import sys

    sys.modules["pypdf"] = pypdf  # type: ignore[assignment]
    try:
        text_item = functions["to_text"](source)
    finally:
        sys.modules.pop("pypdf", None)
    functions["to_chunks"](text_item)
    functions["embed"](text_item)
    return text_item


def snapshot(dataset, pipeline_id):
    return {
        path: (entity.data, copy.deepcopy(entity.metadata))
        for path, entity in dataset.by_path.items()
        if entity.metadata["user"]["rag"].get("pipeline_id") == pipeline_id
    }


def test_second_pipeline_never_overwrites_or_deletes_the_firsts_output(call_tool, captured_code, monkeypatch):
    dataset = SharedOutputDataset()
    embedded: list[list[str]] = []
    monkeypatch.setattr(rag.dl.datasets, "get", lambda dataset_id: dataset)
    model = SimpleNamespace(embed=lambda item_ids: embedded.append(item_ids))
    monkeypatch.setattr(rag.dl.models, "get", lambda model_id: model)

    pipeline_a = build(call_tool, captured_code, "pipe-a", chunk_size=100)
    pipeline_b = build(call_tool, captured_code, "pipe-b", chunk_size=400)

    run_pipeline(pipeline_a, "A" * 350)
    before = snapshot(dataset, "pipe-a")
    assert sum(entity[1]["user"]["rag"]["role"] == "chunk" for entity in before.values()) == 4

    run_pipeline(pipeline_b, "B" * 350)

    assert snapshot(dataset, "pipe-a") == before
    assert not any(path in before for path in dataset.overwritten)
    assert not any(path in before for path in dataset.deleted)
    after_b = snapshot(dataset, "pipe-b")
    assert sum(entity[1]["user"]["rag"]["role"] == "chunk" for entity in after_b.values()) == 1
    # Each embedding run only sends its own pipeline's chunks.
    a_ids = {dataset.by_path[p].id for p in before if before[p][1]["user"]["rag"]["role"] == "chunk"}
    b_ids = {dataset.by_path[p].id for p in after_b if after_b[p][1]["user"]["rag"]["role"] == "chunk"}
    assert set(embedded[0]) == a_ids and set(embedded[1]) == b_ids


def test_rerunning_one_pipeline_only_prunes_its_own_stale_chunks(call_tool, captured_code, monkeypatch):
    dataset = SharedOutputDataset()
    monkeypatch.setattr(rag.dl.datasets, "get", lambda dataset_id: dataset)
    monkeypatch.setattr(rag.dl.models, "get", lambda model_id: SimpleNamespace(embed=lambda item_ids: None))
    pipeline_a = build(call_tool, captured_code, "pipe-a", chunk_size=100)
    pipeline_b = build(call_tool, captured_code, "pipe-b", chunk_size=100)

    run_pipeline(pipeline_b, "B" * 350)
    b_before = snapshot(dataset, "pipe-b")
    run_pipeline(pipeline_a, "A" * 350)
    run_pipeline(pipeline_a, "A" * 120)

    a_chunks = [e for e in snapshot(dataset, "pipe-a").values() if e[1]["user"]["rag"]["role"] == "chunk"]
    assert len(a_chunks) == 2
    assert snapshot(dataset, "pipe-b") == b_before


def test_pipeline_id_is_rendered_as_an_inert_literal(call_tool, captured_code):
    hostile = 'pipe"); import os; os.system("x'
    call_tool(
        "rag_add_text_to_chunks_node",
        {"project_id": "proj-1", "pipeline_id": hostile, "output_dataset_id": "ds-out", "node_name": "c"},
    )
    code = captured_code[(hostile, "c")]
    assert rag.python_string_literal(hostile) in code
    compile(code, "<hostile>", "exec")
    assert "import os" not in code.replace(rag.python_string_literal(hostile), "")
