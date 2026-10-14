# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Annotation reads. Writing is deliberately absent — see doe_mcp.tools.annotations."""

from types import SimpleNamespace

import pytest

from doe_mcp.tools import annotations


def fake_annotation(annotation_id="ann-1", label="person"):
    return SimpleNamespace(
        id=annotation_id,
        item_id="item-1",
        label=label,
        type="box",
        creator="model",
        created_at="2026-01-01",
        # Shape dtlpy actually writes: AnnotationCollection.add(model_info=...) stores
        # {'name', 'model_id', 'confidence'} under metadata.user.model.
        metadata={"user": {"model": {"name": "detector", "model_id": "model-1", "confidence": 0.9}}},
        coordinates=[{"x": 1, "y": 2}],
    )


class FakeBuilder:
    def __init__(self):
        self.added: list[dict] = []

    def add(self, annotation_definition=None, model_info=None, **_):
        self.added.append({"definition": annotation_definition, "model_info": model_info})


class FakeAnnotations:
    def __init__(self, matched=2):
        self.list_filters = "unset"
        self.deleted = None
        self.builder_used: FakeBuilder | None = None
        self.uploaded = None
        self._matched = matched

    def list(self, filters=None, page_offset=None, page_size=None):
        self.list_filters = filters
        self.list_page = page_offset
        self.list_page_size = page_size
        return SimpleNamespace(
            annotations=[fake_annotation(), fake_annotation("ann-2", "car")],
            items=[fake_annotation(), fake_annotation("ann-2", "car")],
            items_count=self._matched,
            page_offset=page_offset,
            page_size=page_size,
        )

    def get(self, annotation_id=None):
        return fake_annotation(annotation_id)

    def delete(self, annotation_id=None, filters=None, **_):
        self.deleted = {"annotation_id": annotation_id, "filters": filters}
        return True

    def builder(self):
        self.builder_used = FakeBuilder()
        return self.builder_used

    def upload(self, builder):
        self.uploaded = builder
        return SimpleNamespace(annotations=[fake_annotation(label="person")])


@pytest.fixture
def dataset(monkeypatch):
    item_annotations = FakeAnnotations()
    item = SimpleNamespace(id="item-1", annotations=item_annotations)
    ds = SimpleNamespace(
        id="ds-1",
        items=SimpleNamespace(get=lambda item_id=None, **_: item),
        annotations=FakeAnnotations(),
        labels_flat_dict={"person": object(), "car": object()},
    )
    monkeypatch.setattr(annotations, "get_dataset", lambda project_id, dataset_id: ds)
    return ds


ARGS = {"project_id": "proj-1", "dataset_id": "ds-1", "item_id": "item-1"}


def test_annotation_list_returns_labels_and_model_provenance(dataset, call_tool):
    result = call_tool("annotation_list", ARGS)

    assert result.is_error is False
    listed = result.structured_content["items"]
    assert [entry["label"] for entry in listed] == ["person", "car"]
    # Which model produced a tag matters when reviewing auto-tags.
    assert listed[0]["model_id"] == "model-1"


def test_annotation_list_filters_by_label(dataset, call_tool):
    call_tool("annotation_list", {**ARGS, "label": "person"})

    item = dataset.items.get()
    assert item.annotations.list_filters is not None


def test_annotation_list_threads_page_inputs_to_platform(dataset, call_tool):
    call_tool("annotation_list", {**ARGS, "page": 2, "page_size": 10})

    item = dataset.items.get()
    assert item.annotations.list_page == 2
    assert item.annotations.list_page_size == 10


def test_annotation_list_projects_down_to_requested_fields(dataset, call_tool):
    result = call_tool("annotation_list", {**ARGS, "fields": ["label"]})

    for entity in result.structured_content["items"]:
        assert set(entity) == {"id", "label"}


def test_annotation_get_returns_one(dataset, call_tool):
    result = call_tool(
        "annotation_get",
        {"project_id": "proj-1", "dataset_id": "ds-1", "annotation_id": "ann-7"},
    )

    assert result.structured_content["id"] == "ann-7"


# --- writing: classification only, ontology-checked (ADR-002, Ambiguity 8) --------------


def test_create_writes_a_classification_for_a_known_label(dataset, call_tool):
    result = call_tool("annotation_create", {**ARGS, "label": "person"})

    assert result.is_error is False
    item = dataset.items.get()
    added = item.annotations.builder_used.added[0]
    assert added["definition"].label == "person"
    # Classification carries no coordinates — that is the whole point of the v1 restriction.
    assert not hasattr(added["definition"], "pts")


def test_create_refuses_a_label_outside_the_ontology_and_lists_the_valid_ones(dataset, call_tool):
    result = call_tool("annotation_create", {**ARGS, "label": "unicorn"})

    assert result.is_error is True
    message = result.content[0].text
    assert "unicorn" in message
    assert "person" in message and "car" in message
    # Refused before any upload.
    assert dataset.items.get().annotations.uploaded is None


def test_create_refuses_when_the_dataset_has_no_ontology(dataset, call_tool):
    dataset.labels_flat_dict = {}

    result = call_tool("annotation_create", {**ARGS, "label": "person"})

    assert result.is_error is True
    assert "no ontology labels" in result.content[0].text


def test_create_records_model_attribution(dataset, call_tool):
    result = call_tool(
        "annotation_create",
        {**ARGS, "label": "person", "model_id": "model-1", "confidence": 0.9},
    )

    assert result.is_error is False
    info = dataset.items.get().annotations.builder_used.added[0]["model_info"]
    # Attribution is what makes delete-by-model possible later.
    assert info == {"name": "model-1", "model_id": "model-1", "confidence": 0.9}


def test_create_requires_confidence_alongside_a_model(dataset, call_tool):
    result = call_tool("annotation_create", {**ARGS, "label": "person", "model_id": "model-1"})

    assert result.is_error is True
    assert "confidence" in result.content[0].text


def test_labels_tool_reports_the_ontology(dataset, call_tool):
    result = call_tool("annotation_labels", {"project_id": "proj-1", "dataset_id": "ds-1"})

    assert result.structured_content["labels"] == ["car", "person"]


# --- deleting ---------------------------------------------------------------------------


def test_delete_refuses_without_confirmation(dataset, call_tool):
    result = call_tool(
        "annotation_delete", {"project_id": "proj-1", "dataset_id": "ds-1", "annotation_id": "ann-1"}
    )

    assert result.is_error is True
    assert dataset.annotations.deleted is None


def test_delete_by_model_reports_the_blast_radius_before_deleting_anything(dataset, call_tool):
    result = call_tool(
        "annotation_delete_by_model",
        {"project_id": "proj-1", "dataset_id": "ds-1", "model_id": "model-1"},
    )

    assert result.is_error is True
    # The refusal names the count so the caller can judge before agreeing.
    assert "2 annotation(s)" in result.content[0].text
    assert dataset.annotations.deleted is None


def test_delete_by_model_filters_on_model_attribution_when_confirmed(dataset, call_tool):
    result = call_tool(
        "annotation_delete_by_model",
        {"project_id": "proj-1", "dataset_id": "ds-1", "model_id": "model-1", "confirm": True},
    )

    assert result.is_error is False
    assert result.structured_content["matched"] == 2
    # Scoped by model attribution, so human-authored annotations survive.
    sent = dataset.annotations.deleted["filters"].prepare()
    assert "metadata.user.model.model_id" in str(sent)


def test_no_geometry_annotation_tool_is_exposed():
    """v1 is classification-only (ADR-002); a box/point tool appearing means that changed."""
    from mcp import Client

    from doe_mcp.server import build_server
    from tests.conftest import run

    async def _names():
        async with Client(build_server()) as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    names = run(_names())
    assert "annotation_create" in names
    assert not {name for name in names if "box" in name or "polygon" in name or "point" in name}
