# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Running a pipeline, watching what it does, and stopping it when it wedges.

The widest gap `scripts/measure_dtlpy_coverage.py` found: `Executions` 0/12 and
`PipelineExecutions` 0/6 reachable. Installing a pipeline was the only way to make work happen,
with no way to trigger a single run, see why a run stalled, or terminate one.

Two pieces of evidence that this was a real gap rather than missing breadth:

* `AGENTS.md` instructed agents to orchestrate work with `pipeline_execute` — a tool that did not
  exist. The convention document assumed the capability.
* Phase 5's agentic-rag eval run left an execution stuck after `pipeline_pause`, which blocked
  `pipeline_delete`, and the writeup records that "no exposed tool can force-terminate" it. That is
  `Executions.terminate`. The run had to wait it out.

Scoped to what the evidence demands. An earlier draft also had `pipeline_execution_list` and
`execution_log`; both are useful, neither is backed by a recorded failure, and together they cost
408 tok against a surface with 4 tok of headroom. `execution_list` already answers "which run is
stuck", which is what the incident needed. They are the first candidates if headroom appears.

`pipeline_validate` is here rather than with the pipeline tools because it belongs to the same
"before you run it, and while it runs" story, and because `Pipelines.validate` takes a *pipeline
JSON* rather than an id — so a graph can be checked before anything is created, which is what makes
it useful to `doe_mcp.blueprint`'s deterministic construction.
"""

from typing import Annotated, Any

import dtlpy as dl
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import Field

from .. import outputs, paging, serialize
from ..context import dataloop_call, get_execution, get_pipeline, get_project
from ..guards import confirm_or_elicit
from ..safety import ADDITIVE, DELETE, READ_ONLY


def register(mcp: MCPServer) -> None:
    @mcp.tool(title="Run a pipeline once", annotations=ADDITIVE)
    def pipeline_execute(
        project_id: Annotated[str, Field(description="Project that owns the pipeline.")],
        pipeline_id: Annotated[str, Field(description="Pipeline to run.")],
        execution_input: Annotated[
            dict[str, Any] | None,
            Field(description="Input for the pipeline's start node, e.g. {'item': '<item_id>'}."),
        ] = None,
        confirm: Annotated[
            bool, Field(description="Must be true: a run consumes compute and can write data.")
        ] = False,
        ctx: Context | None = None,
    ) -> outputs.PipelineExecutionResult:
        """Trigger one run of an installed pipeline, instead of waiting for a trigger to fire.

        The pipeline must already be installed. Returns the run's id and per-node status; poll
        with execution_list.
        """
        confirm_or_elicit(
            ctx, confirm, "run this pipeline", "it consumes compute and its nodes may write data"
        )
        pipeline = get_pipeline(project_id, pipeline_id)
        created = dataloop_call(
            "pipeline execute",
            lambda: pipeline.pipeline_executions.create(
                pipeline_id=pipeline_id, execution_input=execution_input
            ),
        )
        return {"execution": serialize.pipeline_execution(created)}

    @mcp.tool(title="List service executions", annotations=READ_ONLY)
    def execution_list(
        project_id: Annotated[str, Field(description="Project to look in.")],
        pipeline_id: Annotated[
            str | None, Field(description="Only executions belonging to this pipeline.")
        ] = None,
        in_progress_only: Annotated[
            bool, Field(description="Only executions still running — the ones that can be stuck.")
        ] = False,
        page: Annotated[int, Field(ge=0, description="Zero-based page.")] = 0,
        page_size: Annotated[
            int, Field(ge=1, le=paging.MAX_LIMIT, description="Executions per page.")
        ] = paging.DEFAULT_LIMIT,
    ) -> paging.ListResult:
        """List individual function runs and their status, with in_progress_only to narrow.

        A pipeline run reports a node's state; the execution beneath it is the finer level, and
        its id is what other execution tools take.
        """
        project = get_project(project_id)
        repo = dl.repositories.Executions(client_api=dl.client_api, project=project)
        filters = dl.Filters(resource=dl.FiltersResource.EXECUTION)
        filters.page = page
        filters.page_size = page_size
        if pipeline_id is not None:
            filters.add(field="pipelineId", values=pipeline_id)
        if in_progress_only:
            filters.add(field="latestStatus.status", values="in-progress")
        listed = dataloop_call("execution list", lambda: repo.list(filters=filters))
        return paging.from_paged(listed, lambda entity: dict(serialize.execution(entity)), limit=page_size)

    @mcp.tool(title="Terminate a running execution", annotations=DELETE)
    def execution_terminate(
        project_id: Annotated[str, Field(description="Project that owns the execution.")],
        execution_id: Annotated[str, Field(description="Execution to stop.")],
        confirm: Annotated[
            bool, Field(description="Must be true: the execution's work is abandoned mid-flight.")
        ] = False,
        ctx: Context | None = None,
    ) -> outputs.ExecutionTerminationResult:
        """Stop a stuck or hung execution, so it stops blocking a delete.

        Whatever it was part-way through is abandoned; anything already written stays written.
        Find the id with execution_list.
        """
        confirm_or_elicit(
            ctx,
            confirm,
            "terminate this execution",
            "its work is abandoned mid-flight and partial writes are not rolled back",
        )
        project = get_project(project_id)
        execution = get_execution(project, execution_id)
        dataloop_call("execution terminate", lambda: execution.terminate())
        # Re-read rather than trusting terminate()'s return, the same discipline as the graph
        # mutations: the caller needs the platform's view of the status, not ours.
        return {"terminated": True, "execution": serialize.execution(get_execution(project, execution_id))}

    @mcp.tool(title="Validate a pipeline graph", annotations=READ_ONLY)
    def pipeline_validate(
        project_id: Annotated[str, Field(description="Project that owns the pipeline.")],
        pipeline_id: Annotated[str, Field(description="Pipeline whose graph to check.")],
    ) -> outputs.PipelineValidationResult:
        """Ask the platform whether a pipeline is installable, without installing it.

        Cheaper than learning from a failed install. Run it after building and before
        pipeline_install.
        """
        pipeline = get_pipeline(project_id, pipeline_id)
        project = get_project(project_id)
        result = dataloop_call(
            "pipeline validate",
            lambda: project.pipelines.validate(pipeline_json=pipeline.to_json()),
        )
        # validate() returns whatever the endpoint said; it is not documented as a bool, so the
        # raw answer is reported rather than coerced into one (Appendix A.4b).
        return {"pipeline_id": pipeline_id, "result": result}
