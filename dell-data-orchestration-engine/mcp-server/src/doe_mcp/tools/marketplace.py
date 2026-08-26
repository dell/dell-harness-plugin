"""Browsing the Dataloop marketplace.

There is no distinct "marketplace" API. The marketplace is the app registry: published
DPKs (Dataloop Package Kits) queried through ``/app-registry``. A DPK carrying model
components is what ``model_install`` turns into a usable model.
"""

from typing import Annotated, Any

import dtlpy as dl
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from .. import outputs, paging, serialize
from ..context import marketplace_dpks

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)

# Cap on how much of the catalogue a name search will page through, so one tool call
# cannot walk an unbounded registry.
SCAN_PAGE_SIZE = 100
MAX_SCAN = 1000


def build_dpk_filters(category: str | None, public: bool) -> dl.entities.Filters:
    """Build the DPK query.

    ``system_space=True`` adds ``systemSpace: true`` to the request, which is how dtlpy
    reaches entities that live outside a single project; for DPKs that is the shared
    catalogue rather than a project's own published packages.

    Name matching is deliberately absent: the endpoint supports only exact equality.
    ``$match``, ``$regex``, ``$like`` and ``$contains`` were all tried against a live
    instance and every one returned an empty list — even ``$match`` on a name known to
    exist — so a server-side name filter would silently hide the whole catalogue.
    """
    filters = dl.Filters(resource=dl.FiltersResource.DPK)
    if public:
        filters.system_space = True
    if category:
        filters.add(field="attributes.Category", values=category)
    return filters


def matches_name(dpk: Any, needle: str) -> bool:
    """Case-insensitive substring match over the names a user would search by."""
    return any(needle in (getattr(dpk, field, None) or "").casefold() for field in ("name", "display_name"))


def provides_models(dpk: Any) -> bool:
    """Whether a package actually ships model components.

    This is the trustworthy way to find models. The ``attributes.Category`` attribute is
    *not*: on a live instance 51 of the first 200 packages carry no ``Category`` at all,
    and the entire ``yolov8`` family is among them, so filtering on ``Category: Model``
    hides the most sought-after models.
    """
    return bool(getattr(getattr(dpk, "components", None), "models", None))


def provides_pipeline_templates(dpk: Any) -> bool:
    """Whether a package ships pipeline templates.

    Template-only packages carry no ``components.models``, so the ``models_only`` default
    hides them entirely. ``templates_only`` is the way to find them.
    """
    return bool(getattr(getattr(dpk, "components", None), "pipeline_templates", None))


def scan_catalogue(
    dpks: Any,
    filters: dl.entities.Filters,
    name: str | None = None,
    models_only: bool = False,
    templates_only: bool = False,
) -> tuple[list[Any], dict[str, Any]]:
    """Page through the catalogue and filter it locally."""
    needle = name.casefold() if name else None
    found: list[Any] = []
    scanned = 0
    truncated = False
    for page_index in range(MAX_SCAN // SCAN_PAGE_SIZE):
        filters.page = page_index
        filters.page_size = SCAN_PAGE_SIZE
        page = dpks.list(filters=filters)
        page_items = list(page.items or [])
        scanned += len(page_items)
        for dpk in page_items:
            if needle is not None and not matches_name(dpk, needle):
                continue
            if models_only and not provides_models(dpk):
                continue
            if templates_only and not provides_pipeline_templates(dpk):
                continue
            found.append(dpk)
        if not getattr(page, "has_next_page", False):
            break
    else:
        truncated = bool(page_items) and bool(getattr(page, "has_next_page", False))
    return found, {"scanned": scanned, "scan_limit": MAX_SCAN, "scan_truncated": truncated}


def register(mcp: MCPServer) -> None:
    @mcp.tool(title="Search marketplace", annotations=READ_ONLY)
    def marketplace_search(
        name: Annotated[
            str | None,
            Field(description="Case-insensitive substring of the name/display name. Omit to browse all."),
        ] = None,
        models_only: Annotated[
            bool,
            Field(description="Only packages providing model components. Set false to browse everything."),
        ] = True,
        templates_only: Annotated[
            bool,
            Field(
                description=(
                    "Only packages providing pipeline templates. Overrides models_only, since "
                    "template packages usually ship no models."
                )
            ),
        ] = False,
        category: Annotated[
            str | None,
            Field(
                description=(
                    "Optional DPK category, e.g. 'Application'. Unreliable for models: many have "
                    "no Category at all, so prefer models_only."
                )
            ),
        ] = None,
        public: Annotated[
            bool, Field(description="Search the shared catalogue, not only your own published DPKs.")
        ] = True,
        page: Annotated[int, Field(ge=0, description="Zero-based page number.")] = 0,
        page_size: Annotated[int, Field(ge=1, le=paging.MAX_LIMIT, description="DPKs per page.")] = (
            paging.DEFAULT_LIMIT
        ),
        limit: Annotated[int, Field(ge=1, le=paging.MAX_LIMIT, description="Max DPKs to return.")] = (
            paging.DEFAULT_LIMIT
        ),
    ) -> paging.ListResult:
        """Search the marketplace for installable packages (DPKs).

        Defaults to packages that provide models. Set templates_only to find pipeline
        template packages instead. Follow up with marketplace_get_dpk to see which model
        components or pipeline templates a package offers before installing it.
        """
        # Template packages ship no model components, so the models_only default would hide
        # every one of them. Asking for templates therefore relaxes the models filter.
        if templates_only:
            models_only = False
        dpks = marketplace_dpks()
        filters = build_dpk_filters(category=category, public=public)
        if name or models_only or templates_only:
            # Filtered locally: the platform supports no partial-name operator (and returns
            # nothing at all for one rather than failing), and neither model components nor
            # pipeline templates are filterable fields.
            matches, scan = scan_catalogue(
                dpks, filters, name=name, models_only=models_only, templates_only=templates_only
            )
            result = paging.from_list(matches, serialize.dpk, page=page, page_size=page_size, limit=limit)
            result["scanned"] = scan["scanned"]
            result["scan_limit"] = scan["scan_limit"]
            result["scan_truncated"] = scan["scan_truncated"]
            result["has_more"] = bool(result["has_more"] or scan["scan_truncated"])
            return result
        filters.page = page
        filters.page_size = page_size
        return paging.from_paged(dpks.list(filters=filters), serialize.dpk, limit=limit)

    @mcp.tool(title="Get marketplace package", annotations=READ_ONLY)
    def marketplace_get_dpk(
        dpk_id: Annotated[str | None, Field(description="Id of the DPK. Provide this or dpk_name.")] = None,
        dpk_name: Annotated[str | None, Field(description="Name of the DPK.")] = None,
        dpk_version: Annotated[
            str | None, Field(description="Specific version. Defaults to the latest.")
        ] = None,
    ) -> outputs.DpkSummary:
        """Fetch one marketplace package, including the model components it provides.

        The `models[].name` values here are what model_create takes as dpk_model_name.
        """
        if not dpk_id and not dpk_name:
            raise ValueError("Provide either dpk_id or dpk_name.")
        dpks = marketplace_dpks()
        dpk = dpks.get(dpk_id=dpk_id, dpk_name=dpk_name, dpk_version=dpk_version)
        return serialize.dpk(dpk, include_components=True)
