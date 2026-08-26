"""Event triggers: making something run when items arrive, without a human in the loop.

A trigger is the only unattended thing this server can create. It outlives the conversation that
created it, so:

* ``trigger_create`` is confirmation-gated (spec FR-005 / ADR-009), like training and deployment;
* ``trigger_list`` returns every trigger in the project, not only ones the caller remembers —
  the operationally important case is finding an orphan somebody else left running;
* ``trigger_get`` reports whether a trigger is still active, because an unnoticed dead trigger
  looks exactly like a classifier that never had anything to do.
"""

import threading
from collections import defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Annotated, Any, Literal

import dtlpy as dl
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from .. import paging, serialize
from ..context import dataloop_call, get_project
from ..guards import confirm_or_elicit

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
MUTATING = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False
)

ACTIONS = {
    "created": dl.TriggerAction.CREATED,
    "updated": dl.TriggerAction.UPDATED,
    "deleted": dl.TriggerAction.DELETED,
}


_project_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)


@contextmanager
def project_trigger_lock(project_id: str) -> Iterator[None]:
    """Serialise check-then-create for one project.

    The duplicate check is a read followed by a write. The MCP SDK runs tools in worker
    threads, so without this two concurrent creates both see "no duplicate" and both create —
    and every matching event then fires the pipeline twice, at double cost.

    In-process only, and deliberately so: this server is one process serving one identity.
    It does not protect against a second doe-mcp instance or the web console creating the same
    trigger concurrently — only the platform could, and it does not.
    """
    with _project_locks[project_id]:
        yield


def duplicate_of(existing: Any, resource: str, actions: Sequence[str], target: dict[str, Any]) -> bool:
    """Whether an existing trigger already covers the same event on the same target."""
    same_target = all(
        getattr(existing, key, None) == value for key, value in target.items() if value is not None
    )
    existing_actions = {str(action).lower() for action in (getattr(existing, "actions", None) or [])}
    return (
        same_target
        and str(getattr(existing, "resource", "")).lower() == resource.lower()
        and existing_actions == {action.lower() for action in actions}
    )


def register(mcp: MCPServer) -> None:
    @mcp.tool(title="Create trigger", annotations=MUTATING)
    def trigger_create(
        project_id: Annotated[str, Field(description="Project to create the trigger in.")],
        name: Annotated[str, Field(min_length=1, description="Name for the trigger.")],
        dataset_id: Annotated[
            str,
            Field(min_length=1, description="Dataset to watch. Required: project-wide triggers are refused."),
        ],
        pipeline_id: Annotated[
            str | None, Field(description="Pipeline to run. Provide this or service_id.")
        ] = None,
        service_id: Annotated[
            str | None, Field(description="Service to invoke. Provide this or pipeline_id.")
        ] = None,
        function_name: Annotated[str, Field(description="Function to invoke on the service.")] = "run",
        actions: Annotated[
            list[Literal["created", "updated", "deleted"]],
            Field(min_length=1, description="Item events that fire the trigger."),
        ] = ["created"],  # noqa: B006 — MCP needs a concrete default in the schema
        confirm: Annotated[
            bool,
            Field(description="Must be true: a trigger keeps firing after this conversation ends."),
        ] = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Run a pipeline or service automatically whenever items change in one dataset.

        This is standing automation: it keeps firing, and each firing can cost money if it
        reaches a deployed model. `dataset_id` is mandatory — scoping is the only cost limit this
        server can actually impose, so a project-wide item trigger is not offered.
        """
        if bool(pipeline_id) == bool(service_id):
            raise ValueError("Provide exactly one of pipeline_id or service_id.")
        confirm_or_elicit(
            ctx,
            confirm,
            "create this trigger",
            "it runs unattended on every matching event, outliving this conversation, and each "
            "firing can incur platform cost",
        )
        project = get_project(project_id)

        target = {"pipeline_id": pipeline_id, "service_id": service_id}
        # Check and create under one lock: separately they are a race that produces exactly the
        # duplicate automation the check exists to prevent.
        with project_trigger_lock(project_id):
            existing = dataloop_call("trigger list", lambda: project.triggers.list())
            for candidate in paging.page_entities(existing):
                if duplicate_of(candidate, "Item", actions, target):
                    raise ValueError(
                        f"A trigger for the same events on this target already exists "
                        f"(id {getattr(candidate, 'id', 'unknown')}). Delete it first, or reuse "
                        "it, rather than running the same automation twice per event."
                    )

            filters = dl.Filters(resource=dl.FiltersResource.ITEM)
            filters.add(field="datasetId", values=dataset_id)
            created = dataloop_call(
                "trigger create",
                lambda: project.triggers.create(
                    name=name,
                    trigger_type=dl.TriggerType.EVENT,
                    resource=dl.TriggerResource.ITEM,
                    actions=[ACTIONS[action] for action in actions],
                    pipeline_id=pipeline_id,
                    service_id=service_id,
                    function_name=function_name,
                    filters=filters,
                    project_id=project_id,
                ),
            )
        # dtlpy returns a bare `True`, not a Trigger entity, when the target pipeline is not
        # currently installed: creation is deferred until the next `pipeline_install`, and there
        # is no entity to serialize yet. `serialize.trigger` assumes an entity and raises
        # `AttributeError` on a bool, which reads as an unrelated crash rather than "not done yet".
        # Verified against dtlpy 1.125.6's `Triggers.create`.
        if created is True:
            return {
                "created": True,
                "pending_install": True,
                "note": (
                    "The target pipeline is not installed, so the platform deferred creating "
                    "this trigger. Call pipeline_install next to finish creating it, then "
                    "trigger_list to confirm it exists."
                ),
            }
        return serialize.trigger(created)

    @mcp.tool(title="List triggers", annotations=READ_ONLY)
    def trigger_list(
        project_id: Annotated[str, Field(description="Project whose triggers to list.")],
        limit: Annotated[int, Field(ge=1, le=paging.MAX_LIMIT, description="Max triggers.")] = (
            paging.DEFAULT_LIMIT
        ),
    ) -> paging.ListResult:
        """List every trigger in a project.

        Scoped to the project, not to this session, so automation left behind by someone else is
        still discoverable.
        """
        project = get_project(project_id)
        listed = dataloop_call("trigger list", lambda: project.triggers.list())
        if hasattr(listed, "items"):
            return paging.from_paged(listed, serialize.trigger, limit=limit)
        return paging.from_list(list(listed or []), serialize.trigger, limit=limit, page_size=limit)

    @mcp.tool(title="Get trigger", annotations=READ_ONLY)
    def trigger_get(
        project_id: Annotated[str, Field(description="Project that owns the trigger.")],
        trigger_id: Annotated[str, Field(description="Id of the trigger.")],
    ) -> dict[str, Any]:
        """Fetch one trigger, including whether it is still active."""
        project = get_project(project_id)
        return serialize.trigger(
            dataloop_call("trigger lookup", lambda: project.triggers.get(trigger_id=trigger_id))
        )

    @mcp.tool(title="Delete trigger", annotations=DESTRUCTIVE)
    def trigger_delete(
        project_id: Annotated[str, Field(description="Project that owns the trigger.")],
        trigger_id: Annotated[str, Field(description="Id of the trigger to delete.")],
        confirm: Annotated[
            bool, Field(description="Must be true to proceed. Stops the automation firing.")
        ] = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Delete a trigger, stopping the automation it drives."""
        confirm_or_elicit(
            ctx,
            confirm,
            "delete this trigger",
            "whatever it was keeping up to date stops being updated",
        )
        project = get_project(project_id)
        deleted = dataloop_call("trigger delete", lambda: project.triggers.delete(trigger_id=trigger_id))
        return {"deleted": bool(deleted), "id": trigger_id}
