# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Running a pipeline, finding a stuck execution, and terminating it.

Closes the widest gap in `scripts/measure_dtlpy_coverage.py` — `Executions` 0/12 and
`PipelineExecutions` 0/6 — which was not a breadth complaint: Phase 5's agentic-rag run left an
execution stuck that blocked `pipeline_delete`, and the writeup records that no exposed tool could
terminate it.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import dtlpy as dl
import pytest

from doe_mcp.server import build_server
from doe_mcp.tools import executions


def call(name, args, profiles=("pipelines",)):
    from mcp import Client

    async def run():
        async with Client(build_server(profiles=list(profiles))) as client:
            return await client.call_tool(name, args)

    return asyncio.run(run())


@pytest.fixture
def platform(monkeypatch):
    """A project and pipeline whose execution repositories are recording mocks."""
    execution = SimpleNamespace(
        id="exec-1",
        project_id="proj-1",
        status="in-progress",
        in_progress=True,
        service_id="svc-1",
        function_name="run",
        pipeline_id="pipe-1",
        node_id="node-1",
        created_at="2026-08-27T00:00:00Z",
        updated_at="2026-08-27T00:00:01Z",
        terminate=MagicMock(),
    )
    repo = MagicMock()
    repo.get.return_value = execution
    repo.list.return_value = SimpleNamespace(items=[execution], items_count=1, has_next_page=False)

    project = SimpleNamespace(id="proj-1", name="proj", pipelines=MagicMock())
    project.pipelines.validate.return_value = {"valid": True}

    pipeline = SimpleNamespace(
        id="pipe-1",
        to_json=lambda: {"id": "pipe-1", "nodes": []},
        pipeline_executions=MagicMock(),
    )
    pipeline.pipeline_executions.create.return_value = SimpleNamespace(
        id="run-1", pipeline_id="pipe-1", status="in-progress", created_at=None, updated_at=None, nodes=[]
    )

    monkeypatch.setattr(executions, "get_project", lambda project_id: project)
    monkeypatch.setattr(executions, "get_pipeline", lambda project_id, pipeline_id: pipeline)
    monkeypatch.setattr(dl.repositories, "Executions", lambda **_: repo)
    return SimpleNamespace(execution=execution, repo=repo, project=project, pipeline=pipeline)


# --- the incident this closes --------------------------------------------------------------


def test_a_stuck_execution_can_be_found_and_terminated(platform):
    """The exact sequence Phase 5 could not perform: find the in-progress run, then stop it."""
    listed = call(
        "execution_list",
        {"project_id": "proj-1", "pipeline_id": "pipe-1", "in_progress_only": True},
    )
    assert listed.is_error is False
    assert listed.structured_content["items"][0]["id"] == "exec-1"

    stopped = call(
        "execution_terminate",
        {"project_id": "proj-1", "execution_id": "exec-1", "confirm": True},
    )

    assert stopped.is_error is False
    assert platform.execution.terminate.called
    assert stopped.structured_content["terminated"] is True


def test_terminate_refuses_without_confirmation(platform):
    """Abandoning in-flight work with unrolled-back partial writes is not a default."""
    result = call("execution_terminate", {"project_id": "proj-1", "execution_id": "exec-1"})

    assert result.is_error is True
    assert not platform.execution.terminate.called


def test_terminate_reports_the_platform_view_not_its_own(platform):
    """Re-reads after terminating, the same discipline as the graph mutations.

    `terminate()` returning without raising is not evidence the execution stopped; only a fresh
    read is. Two gets: one to fetch, one to report.
    """
    call("execution_terminate", {"project_id": "proj-1", "execution_id": "exec-1", "confirm": True})

    assert platform.repo.get.call_count == 2


def test_running_a_pipeline_requires_confirmation(platform):
    """A run consumes compute and its nodes write data."""
    result = call("pipeline_execute", {"project_id": "proj-1", "pipeline_id": "pipe-1"})

    assert result.is_error is True
    assert not platform.pipeline.pipeline_executions.create.called


def test_running_a_pipeline_passes_the_input_through(platform):
    result = call(
        "pipeline_execute",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "execution_input": {"item": "it-1"},
            "confirm": True,
        },
    )

    assert result.is_error is False
    kwargs = platform.pipeline.pipeline_executions.create.call_args.kwargs
    assert kwargs["execution_input"] == {"item": "it-1"}


def test_execution_list_can_be_narrowed_to_in_progress(platform):
    """The stuck-execution case: everything still running, which is what can be terminated."""
    call("execution_list", {"project_id": "proj-1", "in_progress_only": True})

    filters = platform.repo.list.call_args.kwargs["filters"]
    rendered = str(filters.prepare())

    assert "in-progress" in rendered


def test_validate_checks_a_graph_without_installing_it(platform):
    """Cheaper than learning from a failed install, and the check to run after building a graph."""
    result = call("pipeline_validate", {"project_id": "proj-1", "pipeline_id": "pipe-1"})

    assert result.is_error is False
    assert platform.project.pipelines.validate.called
    assert result.structured_content["result"] == {"valid": True}


# --- upstream behaviour these depend on ------------------------------------------------------


def test_dtlpy_execution_logs_defaults_would_block():
    """Pin the trap: `Executions.logs` follows the stream and waits for completion by default.

    Both defaults are True upstream, so a naive `repo.logs(execution_id=...)` inside a tool body
    blocks until the execution finishes -- which, for the stuck execution that motivated this
    module, is never. Nothing here calls `logs` today (`execution_log` was cut for budget), so this
    test exists to make sure the trap is understood before it is added back.
    """
    import inspect

    signature = inspect.signature(dl.repositories.Executions.logs)

    assert signature.parameters["follow"].default is True
    assert signature.parameters["until_completed"].default is True


def test_dtlpy_validate_takes_json_not_an_id():
    """Why pipeline_validate can check a graph that has not been created yet.

    `Pipelines.validate(pipeline_json=...)` works on a dict, so `doe_mcp.blueprint` can validate a
    built graph before anything is written. If this ever changes to take an id, that possibility
    goes away and the deterministic-construction story loses a check.
    """
    import inspect

    parameters = inspect.signature(dl.repositories.Pipelines.validate).parameters

    assert "pipeline_json" in parameters


def test_execution_tools_are_in_the_default_surface():
    """They were profile-scoped for one release purely because there was no token headroom.

    Stripping advisory schema titles freed 4,067 tokens (17.8% of the surface), which paid for
    promoting them. Running and unsticking a pipeline is not a specialist activity and should not
    need a flag; this asserts it stays that way.
    """
    default = {tool.name for tool in asyncio.run(build_server().list_tools())}

    for name in ("pipeline_execute", "execution_list", "execution_terminate", "pipeline_validate"):
        assert name in default
