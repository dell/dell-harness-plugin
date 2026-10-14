# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Project discovery: the entry point almost every other tool needs a project_id from.

Regression coverage for a gap this repo's own code review flagged: `project_list`/`project_get`
had no direct unit-level coverage at all.
"""

from types import SimpleNamespace

import pytest

from doe_mcp.tools import projects


def fake_project(project_id="proj-1", name="proj"):
    return SimpleNamespace(
        id=project_id,
        name=name,
        creator="me@example.com",
        created_at="2026-01-01",
        role="owner",
        archived=False,
    )


class FakeProjects:
    def __init__(self, existing=None):
        self._existing = existing or [fake_project(), fake_project("proj-2", "other")]

    def list(self):
        return list(self._existing)


@pytest.fixture
def projects_repo(monkeypatch):
    fake = FakeProjects()
    monkeypatch.setattr(projects, "ensure_authenticated", lambda: None)
    monkeypatch.setattr(projects.dl, "projects", fake)
    monkeypatch.setattr(projects, "get_project", lambda project_id: fake_project(project_id))
    return fake


def test_list_requires_authentication_before_listing(monkeypatch, call_tool):
    """Ordering, not just occurrence: a version that listed first and authenticated afterwards
    would satisfy "authentication happened" while already having hit the platform unauthenticated.
    """
    events = []
    monkeypatch.setattr(projects, "ensure_authenticated", lambda: events.append("auth"))

    class RecordingProjects(FakeProjects):
        def list(self):
            events.append("list")
            return super().list()

    monkeypatch.setattr(projects.dl, "projects", RecordingProjects())

    call_tool("project_list", {})

    assert events == ["auth", "list"]


def test_list_returns_every_accessible_project(projects_repo, call_tool):
    result = call_tool("project_list", {})

    assert result.is_error is False
    assert [item["id"] for item in result.structured_content["items"]] == ["proj-1", "proj-2"]


def test_list_is_bounded_by_page_and_limit(projects_repo, call_tool):
    result = call_tool("project_list", {"page": 1, "page_size": 1, "limit": 1})

    assert result.is_error is False
    assert [item["id"] for item in result.structured_content["items"]] == ["proj-2"]
    assert result.structured_content["page"] == 1
    assert result.structured_content["page_size"] == 1


def test_get_fetches_one_project_by_id(monkeypatch, call_tool):
    """Asserts the id reached `get_project` and the serialized shape came back -- not merely that
    a monkeypatched lambda echoed its argument.
    """
    asked = []
    monkeypatch.setattr(
        projects, "get_project", lambda project_id: asked.append(project_id) or fake_project(project_id)
    )

    result = call_tool("project_get", {"project_id": "proj-9"})

    assert result.is_error is False
    assert asked == ["proj-9"]
    assert result.structured_content["id"] == "proj-9"
    assert result.structured_content["name"] == "proj"


def test_limit_above_ceiling_is_rejected_before_the_tool_runs(monkeypatch, call_tool):
    """Schema validation, so the repository must never be touched -- and the error must be about
    the limit. `is_error is True` alone would also pass on a credentials or import failure.
    """
    listed = []

    class RecordingProjects(FakeProjects):
        def list(self):
            listed.append(True)
            return super().list()

    monkeypatch.setattr(projects, "ensure_authenticated", lambda: None)
    monkeypatch.setattr(projects.dl, "projects", RecordingProjects())

    result = call_tool("project_list", {"limit": 10_000})

    assert result.is_error is True
    assert "limit" in result.content[0].text
    assert listed == [], "rejected by the schema, before the tool body ran"
