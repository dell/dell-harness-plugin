"""Pipeline lifecycle confirmation regressions."""

from types import SimpleNamespace

import pytest

from doe_mcp.tools import pipelines


def fake_pipeline(project):
    return SimpleNamespace(
        id="pipe-1",
        name="flow",
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


@pytest.fixture
def project(monkeypatch):
    calls = SimpleNamespace(installed=None)

    class FakePipelines:
        def install(self, pipeline=None, resume_option=None):
            calls.installed = {
                "pipeline": pipeline,
                "resume_option": resume_option,
            }
            return pipeline

    project = SimpleNamespace(id="proj-1", name="proj", pipelines=FakePipelines())
    pipeline = fake_pipeline(project)
    monkeypatch.setattr(pipelines, "get_pipeline", lambda project_id, pipeline_id: pipeline)
    return calls


def test_install_refuses_without_confirmation(project, call_tool):
    result = call_tool("pipeline_install", {"project_id": "proj-1", "pipeline_id": "pipe-1"})

    assert result.is_error is True
    assert "confirm=true" in result.content[0].text
    assert project.installed is None


def test_install_starts_the_pipeline_when_confirmed(project, call_tool):
    result = call_tool(
        "pipeline_install",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "confirm": True,
            "resume_option": "resumeExistingCycles",
        },
    )

    assert result.is_error is False
    assert project.installed["pipeline"].id == "pipe-1"
    assert project.installed["resume_option"] == "resumeExistingCycles"
