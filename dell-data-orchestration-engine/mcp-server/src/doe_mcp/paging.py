# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Uniform, bounded list results.

dtlpy returns two different shapes from its ``list`` methods and neither is safe to hand
to a model verbatim:

* ``PagedEntities`` (pipelines, DPKs, apps, models) holds one page plus paging metadata.
* ``datasets.list()`` returns a plain list and *ignores* the caller's paging: it forces
  ``page_size=1000``, then loops until ``hasNextPage`` is false. Bounding that result is
  entirely up to us.
"""

from collections.abc import Callable, Sequence
from typing import Any, TypedDict

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

FIELDS_DESCRIPTION = "Return only these fields per entity (id always included). Omit for all fields."


class ListResult(TypedDict):
    """A bounded slice of a dtlpy listing."""

    items: list[dict[str, Any]]
    returned: int
    total: int | None
    has_more: bool
    page: int | None
    page_size: int | None
    scanned: int | None
    scan_limit: int | None
    scan_truncated: bool | None


def page_entities(listed: Any) -> list[Any]:
    """Entities out of a dtlpy list result, whether it is paged or already a plain list.

    Written once because the obvious form is wrong: an empty page is falsy, so
    ``getattr(result, "items", None) or result`` falls through to the page object itself and
    raises on iteration. That bug shipped twice — in trigger duplicate detection and again in
    feature-set listing — before becoming this function.
    """
    if hasattr(listed, "items"):
        return list(listed.items or [])
    return list(listed or [])


def mask_fields(entity: dict[str, Any], fields: list[str] | None) -> dict[str, Any]:
    """Project a serialized entity down to the caller-requested fields.

    ``id`` is always kept even if not requested: every follow-up tool call addresses an entity
    by id, so a leaned-down result that dropped it would be a dead end.
    """
    if fields is None:
        return entity
    keep = set(fields) | {"id"}
    return {key: value for key, value in entity.items() if key in keep}


def _bounded(
    entities: Sequence[Any],
    limit: int,
    serializer: Callable[[Any], dict[str, Any]],
    fields: list[str] | None = None,
):
    kept = list(entities)[:limit]
    return [mask_fields(serializer(entity), fields) for entity in kept]


def from_list(
    entities: Sequence[Any],
    serializer: Callable[[Any], dict[str, Any]],
    limit: int = DEFAULT_LIMIT,
    page: int = 0,
    page_size: int = DEFAULT_LIMIT,
    fields: list[str] | None = None,
) -> ListResult:
    """Bound an already-materialised dtlpy list (e.g. ``datasets.list()``).

    ``page`` and ``page_size`` are applied client-side so callers can page through results
    even when the upstream SDK does not expose paging on the returned shape. ``fields``, when
    given, projects each returned entity down to just those keys (plus ``id``).
    """
    total = len(entities)
    start = page * page_size
    page_items = list(entities)[start : start + page_size]
    items = _bounded(page_items, limit, serializer, fields)
    has_more = start + len(page_items) < total or len(items) < len(page_items)
    return ListResult(
        items=items,
        returned=len(items),
        total=total,
        has_more=has_more,
        page=page,
        page_size=page_size,
        scanned=None,
        scan_limit=None,
        scan_truncated=None,
    )


def from_paged(
    paged: Any,
    serializer: Callable[[Any], dict[str, Any]],
    limit: int = DEFAULT_LIMIT,
    fields: list[str] | None = None,
) -> ListResult:
    """Bound one page of a dtlpy ``PagedEntities``. ``fields`` projects each entity's keys."""
    page_items = list(paged.items or [])
    items = _bounded(page_items, limit, serializer, fields)
    truncated_page = len(items) < len(page_items)
    return ListResult(
        items=items,
        returned=len(items),
        total=getattr(paged, "items_count", None),
        has_more=bool(getattr(paged, "has_next_page", False)) or truncated_page,
        page=getattr(paged, "page_offset", None),
        page_size=getattr(paged, "page_size", None),
        scanned=None,
        scan_limit=None,
        scan_truncated=None,
    )
