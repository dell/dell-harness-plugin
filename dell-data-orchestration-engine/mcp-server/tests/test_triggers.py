# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Trigger tools: the confirmation gate, duplicate refusal, and project-wide listing."""

from types import SimpleNamespace

import pytest

from doe_mcp.tools import triggers


def fake_trigger(trigger_id="trg-1", pipeline_id="pipe-1", actions=("Created",)):
    return SimpleNamespace(
        id=trigger_id,
        name="on-new-image",
        active=True,
        type="Event",
        resource="Item",
        actions=list(actions),
        execution_mode="Once",
        service_id=None,
        pipeline_id=pipeline_id,
        function_name="run",
        project_id="proj-1",
        createdAt="2026-01-01",
    )


class FakeTriggers:
    def __init__(self, existing=()):
        self.created: dict | None = None
        self.deleted: str | None = None
        self._existing = list(existing)

    def list(self, **_):
        return SimpleNamespace(items=list(self._existing), items_count=len(self._existing))

    def get(self, trigger_id=None):
        return fake_trigger(trigger_id)

    def create(self, **kwargs):
        self.created = kwargs
        return fake_trigger()

    def delete(self, trigger_id=None):
        self.deleted = trigger_id
        return True


@pytest.fixture(autouse=True)
def mock_get_dataset(monkeypatch):
    dataset = SimpleNamespace(id="ds-1", name="my-dataset")
    monkeypatch.setattr(triggers, "get_dataset", lambda project_id, dataset_id: dataset)
    return dataset


@pytest.fixture
def project(monkeypatch):
    fake = FakeTriggers()
    proj = SimpleNamespace(id="proj-1", name="proj", triggers=fake)
    monkeypatch.setattr(triggers, "get_project", lambda project_id: proj)
    return proj


CREATE = {"project_id": "proj-1", "name": "on-new-image", "pipeline_id": "pipe-1", "dataset_id": "ds-1"}


def test_create_refuses_without_confirmation(project, call_tool):
    result = call_tool("trigger_create", CREATE)

    assert result.is_error is True
    assert "confirm=true" in result.content[0].text
    # Nothing standing was left behind.
    assert project.triggers.created is None


def test_create_needs_exactly_one_target(project, call_tool):
    result = call_tool(
        "trigger_create",
        {**CREATE, "service_id": "svc-1", "confirm": True},
    )

    assert result.is_error is True
    assert "exactly one" in result.content[0].text
    assert project.triggers.created is None


def test_create_wires_an_item_created_event(project, call_tool):
    result = call_tool("trigger_create", {**CREATE, "confirm": True})

    assert result.is_error is False
    sent = project.triggers.created
    assert sent["pipeline_id"] == "pipe-1"
    assert [action.value for action in sent["actions"]] == ["Created"]
    # Scoped to one dataset rather than the whole project.
    assert sent["filters"] is not None


def test_create_reports_deferred_creation_when_pipeline_is_not_installed(monkeypatch, call_tool):
    """dtlpy returns a bare ``True``, not a Trigger entity, when the pipeline isn't installed yet.

    Regression: serializing that ``True`` as if it were an entity raised ``AttributeError:
    'bool' object has no attribute 'id'`` — a confusing crash instead of "not created yet".
    Verified against dtlpy 1.125.6's ``Triggers.create``.
    """
    fake = FakeTriggers()
    fake.create = lambda **kwargs: True
    proj = SimpleNamespace(id="proj-1", name="proj", triggers=fake)
    monkeypatch.setattr(triggers, "get_project", lambda project_id: proj)

    result = call_tool("trigger_create", {**CREATE, "confirm": True})

    assert result.is_error is False
    assert result.structured_content["created"] is True
    assert result.structured_content["pending_install"] is True
    assert "pipeline_install" in result.structured_content["note"]


def test_create_refuses_a_duplicate_of_an_existing_trigger(monkeypatch, call_tool):
    fake = FakeTriggers(existing=[fake_trigger()])
    proj = SimpleNamespace(id="proj-1", name="proj", triggers=fake)
    monkeypatch.setattr(triggers, "get_project", lambda project_id: proj)

    result = call_tool("trigger_create", {**CREATE, "confirm": True})

    assert result.is_error is True
    assert "already exists" in result.content[0].text
    assert fake.created is None


def test_list_returns_all_project_triggers(monkeypatch, call_tool):
    fake = FakeTriggers(existing=[fake_trigger(), fake_trigger("trg-2", "pipe-2")])
    proj = SimpleNamespace(id="proj-1", name="proj", triggers=fake)
    monkeypatch.setattr(triggers, "get_project", lambda project_id: proj)

    result = call_tool("trigger_list", {"project_id": "proj-1"})

    assert result.is_error is False
    # Scoped to the project, so an orphan left by an earlier session is still discoverable.
    assert [item["id"] for item in result.structured_content["items"]] == ["trg-1", "trg-2"]


def test_list_pages_and_preserves_project_scope(monkeypatch, call_tool):
    seen = {}

    class RecordingTriggers(FakeTriggers):
        def list(self, filters=None):
            seen["page"] = filters.page
            seen["page_size"] = filters.page_size
            seen["prepared"] = filters.prepare()
            return super().list()

    proj = SimpleNamespace(
        id="proj-1",
        name="proj",
        triggers=RecordingTriggers(existing=[fake_trigger()]),
    )
    monkeypatch.setattr(triggers, "get_project", lambda project_id: proj)

    result = call_tool(
        "trigger_list",
        {"project_id": "proj-1", "page": 2, "page_size": 25},
    )

    assert result.is_error is False
    assert seen["page"] == 2
    assert seen["page_size"] == 25
    assert seen["prepared"]["filter"] == {"$and": [{"projectId": "proj-1"}]}


def test_get_reports_whether_the_trigger_is_still_active(project, call_tool):
    result = call_tool("trigger_get", {"project_id": "proj-1", "trigger_id": "trg-9"})

    assert result.structured_content["id"] == "trg-9"
    assert result.structured_content["active"] is True


def test_delete_refuses_without_confirmation(project, call_tool):
    result = call_tool("trigger_delete", {"project_id": "proj-1", "trigger_id": "trg-1"})

    assert result.is_error is True
    assert project.triggers.deleted is None


def test_delete_removes_the_trigger_when_confirmed(project, call_tool):
    result = call_tool("trigger_delete", {"project_id": "proj-1", "trigger_id": "trg-1", "confirm": True})

    assert result.is_error is False
    assert project.triggers.deleted == "trg-1"


def test_create_refuses_dataset_outside_the_project(project, monkeypatch, call_tool):
    def missing(project_id, dataset_id):
        raise ValueError(f"Dataset {dataset_id!r} not found in project {project_id!r}.")

    monkeypatch.setattr(triggers, "get_dataset", missing)
    result = call_tool("trigger_create", {**CREATE, "confirm": True})

    assert result.is_error is True
    assert "not found" in result.content[0].text
    assert project.triggers.created is None


def test_create_names_the_dataset_in_confirmation(project, call_tool):
    result = call_tool("trigger_create", CREATE)
    assert result.is_error is True
    assert "my-dataset" in result.content[0].text


def test_create_refuses_a_project_wide_trigger(project, call_tool):
    """Dataset scoping is the only cost limit this server can impose (ADR-004, Ambiguity 9)."""
    unscoped = {key: value for key, value in CREATE.items() if key != "dataset_id"}

    result = call_tool("trigger_create", {**unscoped, "confirm": True})

    assert result.is_error is True
    assert project.triggers.created is None
