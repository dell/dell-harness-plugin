"""Dataset tools, including the label-argument workaround and the delete guard."""

from types import SimpleNamespace

import dtlpy as dl
import pytest

from doe_mcp.tools import datasets


def fake_dataset(dataset_id="ds-1", name="images"):
    return SimpleNamespace(
        id=dataset_id,
        name=name,
        items_count=3,
        annotations_count=0,
        readonly=False,
        index_driver="v2",
        creator="me@example.com",
        created_at="2026-01-01",
        updated_at="2026-01-02",
        url="https://example/ds-1",
    )


class FakeDatasets:
    def __init__(self):
        self.created: dict | None = None
        self.updated: dict | None = None
        self.deleted: dict | None = None

    def create(self, **kwargs):
        self.created = kwargs
        return fake_dataset(name=kwargs["dataset_name"])

    def get(self, dataset_id=None, **_):
        dataset = fake_dataset(dataset_id=dataset_id)
        dataset.project = SimpleNamespace(datasets=self)
        return dataset

    def update(self, dataset=None, patch=None, **_):
        self.updated = {"dataset": dataset, "patch": patch}
        return dataset

    def delete(self, **kwargs):
        self.deleted = kwargs
        return True

    def list(self, name=None):
        return [fake_dataset(), fake_dataset("ds-2", "video")]


@pytest.fixture
def repo(monkeypatch):
    fake = FakeDatasets()
    project = SimpleNamespace(id="proj-1", name="proj", datasets=fake)
    monkeypatch.setattr(datasets, "get_project", lambda project_id: project)
    monkeypatch.setattr(datasets, "get_dataset", lambda project_id, dataset_id: fake.get(dataset_id))
    return fake


# --- the labels workaround -------------------------------------------------------------


def test_dtlpy_rejects_the_label_shapes_its_signature_advertises():
    """Pin the upstream behaviour that forces build_labels to exist.

    ``datasets.create`` documents ``labels`` as "dictionary of {tag: color} or list of
    label entities", but it wraps a non-list in a list and then calls ``serialize_labels``,
    which requires a mapping. Both plain shapes therefore blow up.
    """
    with pytest.raises(AttributeError):
        dl.entities.Dataset.serialize_labels(["cat", "dog"])
    with pytest.raises(AttributeError):
        dl.entities.Dataset.serialize_labels([{"cat": (255, 0, 0)}])


def test_build_labels_from_names():
    labels = datasets.build_labels(["cat", "dog"])

    assert all(isinstance(label, dl.entities.Label) for label in labels)
    assert [label.tag for label in labels] == ["cat", "dog"]


def test_every_label_carries_a_colour():
    """A colourless Label makes dtlpy raise KeyError: 'color' on the real platform.

    ``Label.to_root`` only writes ``color`` when the label has one, but
    ``Ontologies.labels_to_roots`` then indexes ``root['value']['color']`` unconditionally.
    This test drives dtlpy's own conversion, which is what caught it live.
    """
    roots = dl.repositories.Ontologies.labels_to_roots(datasets.build_labels(["cat", "dog"]))

    assert [root["value"]["color"] for root in roots] == [
        "#{:02x}{:02x}{:02x}".format(*datasets.color_for(tag)) for tag in ("cat", "dog")
    ]


def test_label_colours_are_stable_across_calls():
    assert datasets.color_for("cat") == datasets.color_for("cat")
    assert datasets.color_for("cat") != datasets.color_for("dog")


def test_build_labels_from_colour_mapping():
    labels = datasets.build_labels({"cat": "#ff0000"})

    assert labels[0].tag == "cat"
    assert labels[0].hex == "#ff0000"


def test_build_labels_passthrough_none():
    assert datasets.build_labels(None) is None


def test_create_sends_label_entities(repo, call_tool):
    result = call_tool("dataset_create", {"project_id": "proj-1", "name": "new-ds", "labels": ["cat"]})

    assert result.is_error is False
    sent = repo.created["labels"]
    assert all(isinstance(label, dl.entities.Label) for label in sent)


# --- destructive guards ----------------------------------------------------------------


def test_delete_refuses_without_confirmation(repo, call_tool):
    result = call_tool("dataset_delete", {"project_id": "proj-1", "dataset_id": "ds-1"})

    assert result.is_error is True
    assert "confirm=true" in result.content[0].text
    assert repo.deleted is None, "nothing may be deleted without confirmation"


def test_delete_passes_both_dtlpy_confirmation_flags(repo, call_tool):
    result = call_tool("dataset_delete", {"project_id": "proj-1", "dataset_id": "ds-1", "confirm": True})

    assert result.is_error is False
    # dtlpy refuses the delete unless both flags are set.
    assert repo.deleted == {"dataset_id": "ds-1", "sure": True, "really": True}


# --- ordinary paths --------------------------------------------------------------------


def test_list_is_bounded(repo, call_tool):
    result = call_tool("dataset_list", {"project_id": "proj-1", "page": 1, "page_size": 1, "limit": 1})

    assert result.structured_content["returned"] == 1
    assert result.structured_content["total"] == 2
    assert result.structured_content["has_more"] is False
    assert result.structured_content["page"] == 1
    assert result.structured_content["page_size"] == 1


def test_list_projects_down_to_requested_fields(repo, call_tool):
    result = call_tool("dataset_list", {"project_id": "proj-1", "fields": ["name"]})

    for entity in result.structured_content["items"]:
        assert set(entity) == {"id", "name"}


def test_update_merges_name_into_raw_patch(repo, call_tool):
    result = call_tool(
        "dataset_update",
        {
            "project_id": "proj-1",
            "dataset_id": "ds-1",
            "name": "renamed",
            "patch": {"description": "updated"},
        },
    )

    assert result.is_error is False
    assert repo.updated["patch"] == {"description": "updated", "name": "renamed"}
    assert repo.updated["dataset"].name == "renamed"


def test_update_requires_something_to_change(repo, call_tool):
    result = call_tool("dataset_update", {"project_id": "proj-1", "dataset_id": "ds-1"})

    assert result.is_error is True
    assert "nothing to update" in result.content[0].text


def test_update_rejects_unsupported_patch_fields(repo, call_tool):
    result = call_tool(
        "dataset_update",
        {"project_id": "proj-1", "dataset_id": "ds-1", "patch": {"readonly": True}},
    )

    assert result.is_error is True
    assert "Unsupported dataset patch fields" in result.content[0].text
    assert repo.updated is None


def test_limit_above_ceiling_is_rejected_before_the_tool_runs(repo, call_tool):
    result = call_tool("dataset_list", {"project_id": "proj-1", "limit": 10_000})

    assert result.is_error is True
