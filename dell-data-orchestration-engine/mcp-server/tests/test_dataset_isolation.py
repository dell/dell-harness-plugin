# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An item or annotation id from another dataset is rejected before anything reads or writes it.

dtlpy fetches ``/items/<id>`` and ``/annotations/<id>`` without any dataset scope, so an identity
with access to two datasets could pass dataset A and an item id from dataset B. Each case asserts
both that the call fails and that no metadata reached the caller and no write was attempted.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from doe_mcp.tools import annotations, embeddings, items, models, pii, rag

SELECTED = "ds-a"
OTHER = "ds-b"
SECRET_NAME = "other-dataset-private.pdf"


def foreign_item(item_id="item-b"):
    item = MagicMock(name="foreign_item")
    item.id = item_id
    item.dataset_id = OTHER
    item.name = SECRET_NAME
    item.metadata = {"user": {"owner": "other-team"}}
    return item


def foreign_annotation(annotation_id="ann-b"):
    annotation = MagicMock(name="foreign_annotation")
    annotation.id = annotation_id
    annotation.dataset_id = OTHER
    annotation.item_id = "item-b"
    annotation.label = SECRET_NAME
    return annotation


def selected_dataset(item=None, annotation=None):
    dataset = MagicMock(name="selected_dataset")
    dataset.id = SELECTED
    dataset.items.get.return_value = item if item is not None else foreign_item()
    dataset.annotations.get.return_value = annotation if annotation is not None else foreign_annotation()
    dataset.recipes.list.return_value = [MagicMock(ontology_ids=["o"])]
    return dataset


def _assert_rejected(result, kind):
    assert result.is_error is True
    text = result.content[0].text
    assert f"{kind} lookup failed" in text
    assert f"belongs to dataset '{OTHER}'" in text
    assert SECRET_NAME not in text


@pytest.fixture
def dataset(monkeypatch):
    dataset = selected_dataset()
    for module in (items, annotations, pii):
        monkeypatch.setattr(module, "get_dataset", lambda project_id, dataset_id: dataset)
    return dataset


def test_item_get_returns_no_metadata_for_another_datasets_item(dataset, call_tool):
    result = call_tool("item_get", {"project_id": "p", "dataset_id": SELECTED, "item_id": "item-b"})
    _assert_rejected(result, "item")
    assert result.structured_content is None


def test_item_update_never_writes_another_datasets_item(dataset, call_tool):
    item = dataset.items.get.return_value
    result = call_tool(
        "item_update",
        {"project_id": "p", "dataset_id": SELECTED, "item_id": "item-b", "metadata": {"x": 1}},
    )
    _assert_rejected(result, "item")
    dataset.items.update.assert_not_called()
    item.update.assert_not_called()
    assert item.metadata == {"user": {"owner": "other-team"}}


def test_pii_findings_never_written_to_another_datasets_item(dataset, call_tool):
    result = call_tool(
        "pii_set_findings",
        {"project_id": "p", "dataset_id": SELECTED, "item_id": "item-b", "findings": {"entity_types": []}},
    )
    _assert_rejected(result, "item")
    dataset.items.update.assert_not_called()


def test_annotation_list_rejects_another_datasets_item(dataset, call_tool):
    item = dataset.items.get.return_value
    result = call_tool("annotation_list", {"project_id": "p", "dataset_id": SELECTED, "item_id": "item-b"})
    _assert_rejected(result, "item")
    item.annotations.list.assert_not_called()


def test_annotation_create_never_uploads_to_another_datasets_item(dataset, call_tool, monkeypatch):
    monkeypatch.setattr(annotations, "validate_label", lambda dataset, label: None)
    item = dataset.items.get.return_value
    result = call_tool(
        "annotation_create",
        {"project_id": "p", "dataset_id": SELECTED, "item_id": "item-b", "label": "cat"},
    )
    _assert_rejected(result, "item")
    item.annotations.upload.assert_not_called()


def test_annotation_get_returns_nothing_from_another_dataset(dataset, call_tool):
    result = call_tool(
        "annotation_get", {"project_id": "p", "dataset_id": SELECTED, "annotation_id": "ann-b"}
    )
    _assert_rejected(result, "annotation")


def test_annotation_delete_never_deletes_another_datasets_annotation(dataset, call_tool):
    annotation = dataset.annotations.get.return_value
    result = call_tool(
        "annotation_delete",
        {"project_id": "p", "dataset_id": SELECTED, "annotation_id": "ann-b", "confirm": True},
    )
    _assert_rejected(result, "annotation")
    dataset.annotations.delete.assert_not_called()
    annotation.delete.assert_not_called()


def test_item_without_dataset_identity_is_rejected(dataset, call_tool):
    item = foreign_item()
    item.dataset_id = None
    dataset.items.get.return_value = item
    result = call_tool("item_get", {"project_id": "p", "dataset_id": SELECTED, "item_id": "item-b"})
    assert result.is_error is True
    assert "no dataset identity" in result.content[0].text


def test_same_dataset_item_is_still_served(dataset, call_tool):
    dataset.items.get.return_value = SimpleNamespace(
        id="item-a", name="a.pdf", dir="/", type="file", mimetype="application/pdf",
        filename="/a.pdf", dataset_id=SELECTED, annotated=False, created_at=None, metadata={},
    )
    result = call_tool("item_get", {"project_id": "p", "dataset_id": SELECTED, "item_id": "item-a"})
    assert result.is_error is False
    assert result.structured_content["dataset_id"] == SELECTED


def test_vector_and_rag_lookups_treat_foreign_items_as_absent():
    dataset = selected_dataset(item=foreign_item())
    assert embeddings.item_exists(dataset, "item-b") is False
    rag_item = foreign_item()
    rag_item.metadata = {"user": {"rag": {"role": "chunk"}}}
    assert rag.live_chunk(selected_dataset(item=rag_item), "item-b") is None


def test_model_predict_rejects_items_from_a_dataset_outside_the_project(monkeypatch, call_tool):
    model = SimpleNamespace(id="m-1", project_id="proj-1", status="deployed", predict=MagicMock())
    foreign = SimpleNamespace(id="item-b", dataset_id="ds-elsewhere")

    class Datasets:
        def get(self, dataset_id):
            return SimpleNamespace(id=dataset_id, project_id="proj-other")

    project = SimpleNamespace(
        id="proj-1", models=SimpleNamespace(get=lambda **_: model), datasets=Datasets()
    )
    monkeypatch.setattr(models, "get_project", lambda project_id: project)
    monkeypatch.setattr(models.dl, "items", SimpleNamespace(get=lambda item_id: foreign), raising=False)

    result = call_tool(
        "model_predict",
        {"project_id": "proj-1", "model_id": "m-1", "item_ids": ["item-b"], "confirm": True},
    )

    assert result.is_error is True
    assert "belongs to project 'proj-other'" in result.content[0].text
    model.predict.assert_not_called()
