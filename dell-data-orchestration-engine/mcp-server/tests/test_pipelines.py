# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pipeline lifecycle: the confirmation gates on install/delete, and the settings-patch workaround.

Regression coverage for a gap this repo's own code review flagged: these tools had no dedicated
unit tests and were exercised only indirectly through the eval harness and live smoke.
"""

from types import SimpleNamespace

import pytest

from doe_mcp.tools import pipelines


def fake_pipeline(pipeline_id="pipe-1", name="flow", project=None):
    return SimpleNamespace(
        id=pipeline_id,
        name=name,
        status="not-installed",
        project_id="proj-1",
        description=None,
        created_at="2026-01-01",
        updated_at="2026-01-01",
        nodes=[],
        connections=[],
        start_nodes=[],
        url=None,
        project=project,
    )


class FakePipelines:
    def __init__(self, existing=None):
        self.created: dict | None = None
        self.deleted: dict | None = None
        self.installed: dict | None = None
        self.paused: dict | None = None
        self.reset_call: dict | None = None
        self.updated: dict | None = None
        self._existing = existing or fake_pipeline()

    def create(self, **kwargs):
        self.created = kwargs
        return fake_pipeline(name=kwargs.get("name", "flow"))

    def get(self, pipeline_id=None, **_):
        return self._existing

    def delete(self, **kwargs):
        self.deleted = kwargs
        return True

    def install(self, pipeline=None, resume_option=None):
        self.installed = {"pipeline": pipeline, "resume_option": resume_option}
        return fake_pipeline(pipeline_id=pipeline.id, name=pipeline.name)

    def pause(self, pipeline=None, keep_triggers_active=None):
        self.paused = {"pipeline": pipeline, "keep_triggers_active": keep_triggers_active}
        return True

    def reset(self, pipeline=None, stop_if_running=None):
        self.reset_call = {"pipeline": pipeline, "stop_if_running": stop_if_running}
        return True

    def stats(self, pipeline=None):
        return SimpleNamespace(
            pipeline_counters=[SimpleNamespace(status="success", count=3)],
            pipeline_averages=SimpleNamespace(avg_time_per_execution=1.5, avg_execution_per_day=2),
            node_counters=[],
        )

    def update(self, pipeline=None):
        self.updated = {"pipeline": pipeline}
        return pipeline


@pytest.fixture
def project(monkeypatch):
    fake = FakePipelines()
    fake._existing.project = SimpleNamespace(id="proj-1", name="proj", pipelines=fake)
    proj = SimpleNamespace(id="proj-1", name="proj", pipelines=fake)
    monkeypatch.setattr(pipelines, "get_project", lambda project_id: proj)
    monkeypatch.setattr(pipelines, "get_pipeline", lambda project_id, pipeline_id: fake._existing)
    return proj


# --- pipeline_create --------------------------------------------------------------------


def test_create_makes_an_empty_pipeline(project, call_tool):
    result = call_tool("pipeline_create", {"project_id": "proj-1", "name": "flow"})

    assert result.is_error is False
    assert project.pipelines.created["name"] == "flow"


# --- pipeline_delete: confirmation gate --------------------------------------------------


def test_delete_refuses_without_confirmation(project, call_tool):
    result = call_tool("pipeline_delete", {"project_id": "proj-1", "pipeline_id": "pipe-1"})

    assert result.is_error is True
    assert "confirm=true" in result.content[0].text
    assert project.pipelines.deleted is None, "nothing may be deleted without confirmation"


def test_delete_removes_the_pipeline_when_confirmed(project, call_tool):
    result = call_tool("pipeline_delete", {"project_id": "proj-1", "pipeline_id": "pipe-1", "confirm": True})

    assert result.is_error is False
    assert project.pipelines.deleted == {"pipeline_id": "pipe-1"}
    assert result.structured_content["name"] == "flow"


# --- pipeline_install: confirmation gate --------------------------------------------------


def test_install_refuses_without_confirmation(project, call_tool):
    """Regression: pipeline_install had no confirmation gate despite comparable blast radius to
    pipeline_execute and trigger_create -- an installed pipeline keeps consuming compute until it
    is paused or deleted.
    """
    result = call_tool("pipeline_install", {"project_id": "proj-1", "pipeline_id": "pipe-1"})

    assert result.is_error is True
    assert "confirm=true" in result.content[0].text
    assert project.pipelines.installed is None


def test_install_starts_the_pipeline_when_confirmed(project, call_tool):
    result = call_tool("pipeline_install", {"project_id": "proj-1", "pipeline_id": "pipe-1", "confirm": True})

    assert result.is_error is False
    assert project.pipelines.installed["pipeline"].id == "pipe-1"


def test_install_passes_the_resume_option_through(project, call_tool):
    call_tool(
        "pipeline_install",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "confirm": True,
            "resume_option": "resumeExistingCycles",
        },
    )

    assert project.pipelines.installed["resume_option"] == "resumeExistingCycles"


# --- pipeline_pause / pipeline_reset / pipeline_stats --------------------------------------


def test_pause_uninstalls_the_pipeline(project, call_tool):
    result = call_tool("pipeline_pause", {"project_id": "proj-1", "pipeline_id": "pipe-1"})

    assert result.is_error is False
    assert project.pipelines.paused["pipeline"].id == "pipe-1"


def test_reset_clears_execution_counters(project, call_tool):
    result = call_tool("pipeline_reset", {"project_id": "proj-1", "pipeline_id": "pipe-1"})

    assert result.is_error is False
    assert result.structured_content["reset"] is True


def test_stats_reports_counters_and_averages(project, call_tool):
    result = call_tool("pipeline_stats", {"project_id": "proj-1", "pipeline_id": "pipe-1"})

    assert result.is_error is False
    assert result.structured_content["pipeline_counters"] == [{"status": "success", "count": 3}]


# --- pipeline_update_settings: the PipelineSettings.to_json workaround --------------------


def test_update_settings_requires_at_least_one_change(project, call_tool):
    result = call_tool("pipeline_update_settings", {"project_id": "proj-1", "pipeline_id": "pipe-1"})

    assert result.is_error is True
    assert "at least one setting" in result.content[0].text


def test_update_settings_sends_an_explicit_payload_not_the_buggy_to_json(project, monkeypatch, call_tool):
    """Regression: `PipelineSettings.to_json` gates every field on `default_resume_option` and
    then writes that value into `keepTriggersActive`, `activeTriggerAskAgain` and `lastUpdate`
    too. `_patch_settings` builds the PATCH body explicitly instead of routing through it.
    """
    sent: dict = {}

    class FakeClientApi:
        def gen_request(self, req_type=None, path=None, json_req=None):
            sent["req_type"] = req_type
            sent["path"] = path
            sent["json_req"] = json_req
            return True, SimpleNamespace(
                json=lambda: {
                    "id": "pipe-1",
                    "name": "flow",
                    "projectId": "proj-1",
                    "nodes": [],
                    "connections": [],
                    "startNodes": [],
                    "variables": [],
                }
            )

        def check_response(self, success, response, path=None):
            return success

    monkeypatch.setattr(pipelines.dl, "client_api", FakeClientApi())

    result = call_tool(
        "pipeline_update_settings",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "keep_triggers_active": True,
        },
    )

    assert result.is_error is False
    assert sent["path"] == "/pipelines/pipe-1"
    # Only the field actually requested is in the payload -- not the to_json bug's blanket write.
    assert sent["json_req"] == {"settings": {"keepTriggersActive": True}}


def test_update_settings_raises_a_clear_error_when_the_patch_fails(project, monkeypatch, call_tool):
    class FailingClientApi:
        def gen_request(self, req_type=None, path=None, json_req=None):
            return False, SimpleNamespace(json=lambda: {})

        def check_response(self, success, response, path=None):
            return False

    monkeypatch.setattr(pipelines.dl, "client_api", FailingClientApi())

    result = call_tool(
        "pipeline_update_settings",
        {"project_id": "proj-1", "pipeline_id": "pipe-1", "keep_triggers_active": True},
    )

    assert result.is_error is True
    assert "Failed to update settings" in result.content[0].text
