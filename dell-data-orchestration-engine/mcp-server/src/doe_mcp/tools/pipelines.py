# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pipeline lifecycle. Graph editing lives in ``pipeline_nodes``."""

from collections.abc import Mapping
from copy import deepcopy
from typing import Annotated, Any, Literal

import dtlpy as dl
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from .. import outputs, paging, serialize
from ..context import get_pipeline, get_project, marketplace_dpks
from ..guards import confirm_or_elicit
from ..progress import report_progress

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
MUTATING = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False
)

ResumeOption = Literal["terminateExistingCycles", "resumeExistingCycles"]


def _stats(stats: dl.entities.PipelineStats) -> dict[str, Any]:
    return {
        "pipeline_counters": [{"status": c.status, "count": c.count} for c in stats.pipeline_counters],
        "pipeline_averages": {
            "avg_time_per_execution": stats.pipeline_averages.avg_time_per_execution,
            "avg_executions_per_day": stats.pipeline_averages.avg_execution_per_day,
        },
        "node_counters": [
            {
                "node_id": n.node_id,
                "counters": [{"status": c.status, "count": c.count} for c in n.counters],
            }
            for n in stats.node_counters
        ],
    }


def _patch_settings(pipeline: dl.entities.Pipeline, settings: dict[str, Any]) -> dl.entities.Pipeline:
    """PATCH pipeline settings with an explicitly built payload.

    dtlpy's ``PipelineSettings.to_json`` has a copy-paste bug: it gates every field on
    ``default_resume_option`` and then writes that same value into ``keepTriggersActive``,
    ``activeTriggerAskAgain`` and ``lastUpdate``. Routing settings through the entity would
    therefore send wrong values, so the payload is assembled here and sent to the same
    endpoint ``Pipelines.update_settings`` uses.
    """
    client_api = dl.client_api
    success, response = client_api.gen_request(
        req_type="patch",
        path=f"/pipelines/{pipeline.id}",
        json_req={"settings": settings},
    )
    if client_api.check_response(success, response, path="/pipelines") is False:
        raise RuntimeError(f"Failed to update settings for pipeline {pipeline.id}")
    return dl.entities.Pipeline.from_json(
        _json=response.json(), client_api=client_api, project=pipeline.project
    )


def select_template(templates: list[dict[str, Any]], template_name: str | None) -> dict[str, Any]:
    """Pick one pipeline template out of a DPK's components.

    dtlpy exposes ``components.pipeline_templates`` as raw pipeline JSON dicts and offers no
    "install this template" call, so instantiating one means selecting its JSON and handing
    it to ``pipelines.create(pipeline_json=...)``.
    """
    if not templates:
        raise ValueError("That package provides no pipeline templates.")
    available = ", ".join(str(template.get("name")) for template in templates)
    if template_name is None:
        if len(templates) > 1:
            raise ValueError(f"That package provides several pipeline templates: {available}. Pick one.")
        return templates[0]
    for template in templates:
        if template.get("name") == template_name:
            return template
    raise ValueError(f"No pipeline template named {template_name!r}. Available templates: {available}.")


def validation_passed(validation: Any) -> bool:
    """Interpret the platform's pipeline validation response conservatively."""
    if validation is None:
        return False
    if isinstance(validation, Mapping):
        for key in ("valid", "success", "passed"):
            if validation.get(key) is False:
                return False
        for key in ("errors", "error", "failures"):
            if validation.get(key):
                return False
    return True


def template_pipeline_json(template: dict[str, Any], project_id: str, pipeline_name: str) -> dict[str, Any]:
    """Prepare template JSON for validate/create without mutating the DPK object.

    dtlpy's PipelineNode.from_json indexes metadata['position'] unconditionally when it
    parses create responses. Some template JSON omits positions, so patch deterministic
    defaults before validation and creation.
    """
    pipeline_json = deepcopy(template)
    pipeline_json["name"] = pipeline_name
    pipeline_json["projectId"] = project_id
    for index, node in enumerate(pipeline_json.get("nodes") or []):
        if isinstance(node, dict):
            node.setdefault("metadata", {}).setdefault("position", {"x": 220 + index * 360, "y": 180})
    return pipeline_json


def register(mcp: MCPServer) -> None:
    @mcp.tool(title="List pipelines", annotations=READ_ONLY)
    def pipeline_list(
        project_id: Annotated[str, Field(description="Project whose pipelines to list.")],
        page: Annotated[int, Field(ge=0, description="Zero-based page number.")] = 0,
        page_size: Annotated[int, Field(ge=1, le=paging.MAX_LIMIT, description="Pipelines per page.")] = (
            paging.DEFAULT_LIMIT
        ),
        limit: Annotated[int, Field(ge=1, le=paging.MAX_LIMIT, description="Max pipelines to return.")] = (
            paging.DEFAULT_LIMIT
        ),
    ) -> paging.ListResult:
        """List pipelines in a project."""
        project = get_project(project_id)
        filters = dl.Filters(resource=dl.FiltersResource.PIPELINE)
        filters.page = page
        filters.page_size = page_size
        return paging.from_paged(project.pipelines.list(filters=filters), serialize.pipeline, limit=limit)

    @mcp.tool(title="Get pipeline", annotations=READ_ONLY)
    def pipeline_get(
        project_id: Annotated[str, Field(description="Project that owns the pipeline.")],
        pipeline_id: Annotated[str, Field(description="Id of the pipeline to fetch.")],
        include_graph: Annotated[
            bool, Field(description="Include nodes, connections and start nodes.")
        ] = False,
    ) -> outputs.PipelineSummary:
        """Fetch a pipeline. Set include_graph to inspect or edit its nodes."""
        pipeline = get_pipeline(project_id, pipeline_id)
        return serialize.pipeline(pipeline, include_graph=include_graph)

    @mcp.tool(title="Create pipeline", annotations=MUTATING)
    def pipeline_create(
        project_id: Annotated[str, Field(description="Project to create the pipeline in.")],
        name: Annotated[str, Field(min_length=1, description="Name for the new pipeline.")],
        description: Annotated[str | None, Field(description="Optional description.")] = None,
    ) -> dict[str, Any]:
        """Create an empty pipeline.

        The pipeline has no nodes yet: add them with pipeline_node_add, connect them with
        pipeline_connect_nodes, then start it with pipeline_install.
        """
        project = get_project(project_id)
        pipeline = project.pipelines.create(name=name, project_id=project_id)
        if description is not None:
            pipeline.description = description
            pipeline = project.pipelines.update(pipeline=pipeline)
        return serialize.pipeline(pipeline)

    @mcp.tool(title="Create pipeline from marketplace template", annotations=MUTATING)
    def pipeline_create_from_template(
        project_id: Annotated[str, Field(description="Project to create the pipeline in.")],
        dpk_id: Annotated[str | None, Field(description="Id of the DPK. Provide this or dpk_name.")] = None,
        dpk_name: Annotated[
            str | None, Field(description="Name of the DPK that provides the template.")
        ] = None,
        dpk_version: Annotated[str | None, Field(description="Version. Defaults to the latest.")] = None,
        template_name: Annotated[
            str | None,
            Field(description="Template to use, from marketplace_get_dpk. Optional if the DPK has only one."),
        ] = None,
        name: Annotated[
            str | None, Field(description="Name for the new pipeline. Defaults to the template's name.")
        ] = None,
        confirm: Annotated[
            bool,
            Field(
                description=(
                    "Must be true to proceed. A template can carry services, triggers and "
                    "compute configuration, so review it with marketplace_get_dpk first."
                )
            ),
        ] = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Create a pipeline from a marketplace package's pipeline template.

        Find candidates with marketplace_search(templates_only=true), then inspect the
        template with marketplace_get_dpk before creating it. The pipeline is created but not
        started: review it with pipeline_get, then start it with pipeline_install.
        """
        if not dpk_id and not dpk_name:
            raise ValueError("Provide either dpk_id or dpk_name.")
        dpk = marketplace_dpks().get(dpk_id=dpk_id, dpk_name=dpk_name, dpk_version=dpk_version)
        components = getattr(dpk, "components", None)
        templates = list(getattr(components, "pipeline_templates", None) or [])
        template = select_template(templates, template_name)

        pipeline_name = name or template.get("name")
        if not pipeline_name:
            raise ValueError("That template has no name, so provide `name` explicitly.")

        project = get_project(project_id)
        pipeline_json = template_pipeline_json(
            template=template, project_id=project_id, pipeline_name=pipeline_name
        )

        # Validate before asking for confirmation, so a broken template fails cheaply.
        validation = project.pipelines.validate(pipeline_json=pipeline_json)
        if not validation_passed(validation):
            raise RuntimeError(
                f"DOE rejected the {pipeline_name!r} pipeline template as invalid: "
                f"{serialize.json_safe(validation)}"
            )

        confirm_or_elicit(
            ctx,
            confirm,
            f"create pipeline {pipeline_name!r} from template",
            "the template is valid, but it may provision services, triggers and compute",
        )

        pipeline = project.pipelines.create(
            name=pipeline_name, project_id=project_id, pipeline_json=pipeline_json
        )
        result = serialize.pipeline(pipeline, include_graph=True)
        result["template"] = serialize.pipeline_template(template)
        result["validation"] = serialize.json_safe(validation)
        return result

    @mcp.tool(title="Delete pipeline", annotations=DESTRUCTIVE)
    def pipeline_delete(
        project_id: Annotated[str, Field(description="Project that owns the pipeline.")],
        pipeline_id: Annotated[str, Field(description="Id of the pipeline to delete.")],
        confirm: Annotated[
            bool,
            Field(description="Must be true to proceed. Deletion cannot be undone; ask the user first."),
        ] = False,
        ctx: Context | None = None,
    ) -> outputs.DeleteResult:
        """Permanently delete a pipeline."""
        confirm_or_elicit(
            ctx, confirm, "delete this pipeline", "it destroys the pipeline and cannot be undone"
        )
        pipeline = get_pipeline(project_id, pipeline_id)
        name = pipeline.name
        pipeline.project.pipelines.delete(pipeline_id=pipeline_id)
        return {"deleted": True, "id": pipeline_id, "name": name}

    @mcp.tool(title="Install pipeline", annotations=MUTATING)
    def pipeline_install(
        project_id: Annotated[str, Field(description="Project that owns the pipeline.")],
        pipeline_id: Annotated[str, Field(description="Id of the pipeline to start.")],
        resume_option: Annotated[
            ResumeOption | None,
            Field(description="What to do with cycles from a previous run."),
        ] = None,
        confirm: Annotated[
            bool,
            Field(description="Must be true: an installed pipeline keeps running and consuming compute."),
        ] = False,
        ctx: Context | None = None,
    ) -> outputs.PipelineStatusSummary:
        """Install a pipeline after confirmation; it keeps processing until paused or deleted."""
        confirm_or_elicit(
            ctx,
            confirm,
            "install this pipeline",
            "it starts processing and keeps consuming compute until it is paused or deleted",
        )
        pipeline = get_pipeline(project_id, pipeline_id)
        report_progress(ctx, 0, 100, f"Installing pipeline {pipeline.name!r}...")
        installed = pipeline.project.pipelines.install(pipeline=pipeline, resume_option=resume_option)
        report_progress(ctx, 100, 100, "Pipeline installed.")
        return serialize.pipeline(installed or pipeline)

    @mcp.tool(title="Pause pipeline", annotations=MUTATING)
    def pipeline_pause(
        project_id: Annotated[str, Field(description="Project that owns the pipeline.")],
        pipeline_id: Annotated[str, Field(description="Id of the pipeline to pause.")],
        keep_triggers_active: Annotated[
            bool | None,
            Field(description="Keep triggers collecting events while the pipeline is paused."),
        ] = None,
    ) -> outputs.PipelineStatusSummary:
        """Pause (uninstall) a running pipeline."""
        pipeline = get_pipeline(project_id, pipeline_id)
        pipeline.project.pipelines.pause(pipeline=pipeline, keep_triggers_active=keep_triggers_active)
        return serialize.pipeline(get_pipeline(project_id, pipeline_id))

    @mcp.tool(title="Reset pipeline counters", annotations=MUTATING)
    def pipeline_reset(
        project_id: Annotated[str, Field(description="Project that owns the pipeline.")],
        pipeline_id: Annotated[str, Field(description="Id of the pipeline to reset.")],
        stop_if_running: Annotated[
            bool, Field(description="Pause the pipeline first if it is currently installed.")
        ] = False,
    ) -> dict[str, Any]:
        """Reset a pipeline's execution counters."""
        pipeline = get_pipeline(project_id, pipeline_id)
        ok = pipeline.project.pipelines.reset(pipeline=pipeline, stop_if_running=stop_if_running)
        return {"reset": bool(ok), "id": pipeline_id}

    @mcp.tool(title="Pipeline statistics", annotations=READ_ONLY)
    def pipeline_stats(
        project_id: Annotated[str, Field(description="Project that owns the pipeline.")],
        pipeline_id: Annotated[str, Field(description="Id of the pipeline.")],
    ) -> dict[str, Any]:
        """Execution counters and timing averages, overall and per node."""
        pipeline = get_pipeline(project_id, pipeline_id)
        return _stats(pipeline.project.pipelines.stats(pipeline=pipeline))

    @mcp.tool(title="Update pipeline settings", annotations=MUTATING)
    def pipeline_update_settings(
        project_id: Annotated[str, Field(description="Project that owns the pipeline.")],
        pipeline_id: Annotated[str, Field(description="Id of the pipeline.")],
        default_resume_option: Annotated[
            ResumeOption | None, Field(description="Default handling of existing cycles on install.")
        ] = None,
        keep_triggers_active: Annotated[
            bool | None, Field(description="Keep triggers active while the pipeline is paused.")
        ] = None,
        active_trigger_ask_again: Annotated[
            bool | None, Field(description="Re-prompt about active triggers on the next pause.")
        ] = None,
    ) -> dict[str, Any]:
        """Update a pipeline's settings."""
        settings: dict[str, Any] = {}
        if default_resume_option is not None:
            settings["defaultResumeOption"] = default_resume_option
        if keep_triggers_active is not None:
            settings["keepTriggersActive"] = keep_triggers_active
        if active_trigger_ask_again is not None:
            settings["activeTriggerAskAgain"] = active_trigger_ask_again
        if not settings:
            raise ValueError("Provide at least one setting to change.")

        pipeline = get_pipeline(project_id, pipeline_id)
        return serialize.pipeline(_patch_settings(pipeline, settings))
