# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Item-level access: listing, inspecting, uploading and patching dataset items.

Two dtlpy behaviours shape this module.

``Items.update`` takes no patch dictionary for a single item. Its ``update_values`` /
``system_update_values`` parameters are bulk-only and raise when combined with a specific item;
the single-item path instead diffs the *mutated entity* against its platform state. So
``item_update`` mutates allow-listed attributes on the entity and never forwards a caller dict,
which is a different shape from ``dataset_update``.

``Items.upload`` swallows per-file failures by default and returns whatever succeeded, so a
partly-failed batch is indistinguishable from a whole one. Uploads are therefore issued one file
at a time and each outcome is reported separately.
"""

import os
from collections import Counter
from typing import Annotated, Any

import dtlpy as dl
from mcp.server import MCPServer
from pydantic import Field

from .. import outputs, paging, serialize
from ..context import dataloop_call, get_dataset
from ..safety import ADDITIVE, OVERWRITE, READ_ONLY

# Profiling counts client-side because dtlpy exposes no aggregation, group-by or facet API —
# there is no way to ask the platform "how many of each mimetype". The scan is therefore
# bounded and its limits reported, the same contract marketplace_search uses.
PROFILE_PAGE_SIZE = 100
PROFILE_MAX_SCAN = 1000

# `metadata` on an item means *user* metadata: the platform's own `system` block is reachable
# through the same attribute and is never writable from a tool. The writable set is enforced
# structurally by `item_update`'s signature rather than by an allow-list constant -- there was one
# here, unreferenced since it was written, so the check it implied never actually ran.
SYSTEM_METADATA_KEY = "system"


def validate_item_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Reject a metadata patch that would write into the platform's own namespace."""
    if SYSTEM_METADATA_KEY in metadata:
        raise ValueError(
            "Refusing to patch 'system' metadata: that namespace belongs to the platform. "
            f"Writable metadata keys are anything except '{SYSTEM_METADATA_KEY}'."
        )
    return dict(metadata)


def scan_items(dataset: Any, max_scan: int) -> tuple[list[Any], outputs.DatasetScanResult]:
    """Page through a dataset's items up to a cap, reporting how far it got."""
    seen: list[Any] = []
    truncated = False
    pages = max(1, max_scan // PROFILE_PAGE_SIZE)
    for page_index in range(pages):
        filters = dl.Filters(resource=dl.FiltersResource.ITEM)
        filters.page = page_index
        filters.page_size = PROFILE_PAGE_SIZE
        page = dataset.items.list(filters=filters)
        page_items = list(getattr(page, "items", None) or [])
        seen.extend(page_items)
        if not getattr(page, "has_next_page", False):
            break
        if len(seen) >= max_scan:
            truncated = True
            break
    else:
        truncated = True
    return seen[:max_scan], {
        "scanned": len(seen[:max_scan]),
        "scan_limit": max_scan,
        "scan_truncated": truncated,
    }


def register(mcp: MCPServer) -> None:
    @mcp.tool(title="Profile a dataset's contents", annotations=READ_ONLY)
    def dataset_profile(
        project_id: Annotated[str, Field(description="Project that owns the dataset.")],
        dataset_id: Annotated[str, Field(description="Dataset to profile.")],
        max_scan: Annotated[
            int,
            Field(
                ge=1,
                le=PROFILE_MAX_SCAN,
                description="Cap on items inspected. Counts are a sample above this.",
            ),
        ] = 500,
        sample: Annotated[int, Field(ge=0, le=25, description="How many example filenames to include.")] = 5,
    ) -> outputs.DatasetProfileResult:
        """Summarise what a dataset holds: file types, folders, and how much is annotated.

        Answers "what kind of files are in here" without running any model, so it costs nothing
        and mutates nothing. Feed the result to a caller that can write prose — this returns
        facts, not a narrative.

        The platform offers no aggregation API, so counts come from a bounded client-side scan.
        Check `scan_truncated`: above the cap the numbers are proportions of a sample, not a
        census of the dataset, and must be described that way.
        """
        dataset = get_dataset(project_id, dataset_id)
        items, scan = dataloop_call("item scan", lambda: scan_items(dataset, max_scan))

        mimetypes: Counter[str] = Counter()
        kinds: Counter[str] = Counter()
        directories: Counter[str] = Counter()
        annotated = 0
        for entity in items:
            mimetypes[getattr(entity, "mimetype", None) or "unknown"] += 1
            kinds[getattr(entity, "type", None) or "unknown"] += 1
            directories[getattr(entity, "dir", None) or "/"] += 1
            if getattr(entity, "annotated", False):
                annotated += 1

        total = getattr(dataset, "items_count", None)
        caveat = None
        if scan["scan_truncated"]:
            caveat = (
                f"Counts describe the first {scan['scanned']} items, not the whole dataset"
                f"{f' of {total}' if total else ''}. Report them as a sample; raise max_scan for "
                "a deeper look, or treat the proportions as indicative only."
            )
        return {
            "dataset": {
                "id": dataset_id,
                "name": getattr(dataset, "name", None),
                "items_count": total,
                "annotations_count": getattr(dataset, "annotations_count", None),
            },
            "file_types": dict(mimetypes.most_common()),
            "item_kinds": dict(kinds.most_common()),
            "directories": dict(directories.most_common(20)),
            "annotated_items": annotated,
            "unannotated_items": scan["scanned"] - annotated,
            "sample_filenames": [getattr(e, "name", None) for e in items[:sample]],
            **scan,
            "caveat": caveat,
        }

    @mcp.tool(title="List items", annotations=READ_ONLY)
    def item_list(
        project_id: Annotated[str, Field(description="Project that owns the dataset.")],
        dataset_id: Annotated[str, Field(description="Dataset whose items to list.")],
        name: Annotated[
            str | None,
            Field(description="Filter by filename, e.g. '/folder/photo.jpg' or a '*.pdf' pattern."),
        ] = None,
        annotated: Annotated[
            bool | None, Field(description="Only annotated items, or only unannotated ones.")
        ] = None,
        page: Annotated[int, Field(ge=0, description="Zero-based page number.")] = 0,
        page_size: Annotated[int, Field(ge=1, le=paging.MAX_LIMIT, description="Items per page.")] = (
            paging.DEFAULT_LIMIT
        ),
        limit: Annotated[int, Field(ge=1, le=paging.MAX_LIMIT, description="Max items to return.")] = (
            paging.DEFAULT_LIMIT
        ),
        fields: Annotated[list[str] | None, Field(description=paging.FIELDS_DESCRIPTION)] = None,
    ) -> paging.ListResult:
        """List items in a dataset, newest first.

        Responses are bounded, so check `has_more` and page rather than assuming a short list
        means a small dataset.
        """
        dataset = get_dataset(project_id, dataset_id)
        filters = dl.Filters(resource=dl.FiltersResource.ITEM)
        if name is not None:
            filters.add(field="filename", values=name)
        if annotated is not None:
            filters.add(field="annotated", values=annotated)
        filters.page = page
        filters.page_size = page_size
        paged = dataloop_call("item list", lambda: dataset.items.list(filters=filters))
        return paging.from_paged(paged, serialize.item, limit=limit, fields=fields)

    @mcp.tool(title="Get item", annotations=READ_ONLY)
    def item_get(
        project_id: Annotated[str, Field(description="Project that owns the dataset.")],
        dataset_id: Annotated[str, Field(description="Dataset that holds the item.")],
        item_id: Annotated[str, Field(description="Id of the item to fetch.")],
    ) -> outputs.ItemSummary:
        """Fetch one item by id."""
        dataset = get_dataset(project_id, dataset_id)
        return serialize.item(dataloop_call("item lookup", lambda: dataset.items.get(item_id=item_id)))

    @mcp.tool(title="Upload items", annotations=ADDITIVE)
    def item_upload(
        project_id: Annotated[str, Field(description="Project that owns the dataset.")],
        dataset_id: Annotated[str, Field(description="Dataset to upload into.")],
        local_paths: Annotated[
            list[str],
            Field(min_length=1, description="Local file paths to upload. Each is reported separately."),
        ],
        remote_path: Annotated[str, Field(description="Folder within the dataset, e.g. '/incoming'.")] = "/",
        item_metadata: Annotated[
            dict[str, Any] | None, Field(description="User metadata to attach to every uploaded item.")
        ] = None,
        overwrite: Annotated[
            bool, Field(description="Replace an existing item with the same remote name.")
        ] = False,
    ) -> dict[str, Any]:
        """Upload local files into a dataset.

        Not atomic: each file is uploaded independently, so a batch can partly succeed. The
        response lists `uploaded` and `failed` separately — check both, and never infer success
        for the batch from a non-empty `uploaded`.
        """
        missing = [path for path in local_paths if not os.path.isfile(path)]
        if missing:
            # Fail before touching the platform, so a typo cannot leave a half-uploaded batch.
            raise ValueError(f"No such file(s): {', '.join(missing)}.")
        metadata = validate_item_metadata(item_metadata or {}) if item_metadata else None

        dataset = get_dataset(project_id, dataset_id)
        uploaded: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for path in local_paths:
            # Summarising is inside the try on purpose: an entity that uploads but cannot be
            # summarised must be recorded as one file's failure, not abort the batch and
            # destroy the record of files already uploaded (the caller could not then tell
            # what to retry).
            try:
                result = dataset.items.upload(
                    local_path=path,
                    remote_path=remote_path,
                    item_metadata=metadata,
                    overwrite=overwrite,
                    raise_on_error=True,
                )
                # upload() returns None when it declined the file without raising.
                if result is None:
                    failed.append({"local_path": path, "error": "DOE accepted no item for this file."})
                    continue
                entity = result[0] if isinstance(result, list) else result
                summary = serialize.item(entity)
            except Exception as exc:  # one bad file must not hide the rest of the batch
                failed.append({"local_path": path, "error": str(exc).strip() or type(exc).__name__})
                continue
            uploaded.append({"local_path": path, **summary})
        return {
            "dataset_id": dataset_id,
            "requested": len(local_paths),
            "uploaded": uploaded,
            "failed": failed,
            "all_succeeded": not failed,
        }

    @mcp.tool(title="Update item", annotations=OVERWRITE)
    def item_update(
        project_id: Annotated[str, Field(description="Project that owns the dataset.")],
        dataset_id: Annotated[str, Field(description="Dataset that holds the item.")],
        item_id: Annotated[str, Field(description="Id of the item to update.")],
        name: Annotated[str | None, Field(description="New filename for the item.")] = None,
        description: Annotated[str | None, Field(description="New description.")] = None,
        metadata: Annotated[
            dict[str, Any] | None,
            Field(description="User metadata to merge in. The 'system' namespace is not writable."),
        ] = None,
    ) -> dict[str, Any]:
        """Update an item's own fields.

        Only `name`, `description` and user `metadata` are writable. Platform-internal `system`
        metadata is deliberately unreachable from this tool.
        """
        if name is None and description is None and metadata is None:
            raise ValueError("Provide `name`, `description` or `metadata`: nothing to update otherwise.")
        patch = validate_item_metadata(metadata) if metadata is not None else None

        dataset = get_dataset(project_id, dataset_id)
        item = dataloop_call("item lookup", lambda: dataset.items.get(item_id=item_id))
        if name is not None:
            item.name = name
        if description is not None:
            item.description = description
        if patch is not None:
            # Under `user`, not at the top level. DOE namespaces caller-owned metadata
            # there, and every query this server builds targets `metadata.user.<field>` —
            # model_set_subset's review-marker filter above all. Writing to the top level
            # produced a marker no filter could ever match, which live smoke exposed.
            metadata = dict(getattr(item, "metadata", None) or {})
            user = dict(metadata.get("user") or {})
            user.update(patch)
            metadata["user"] = user
            item.metadata = metadata
        # system_metadata is intentionally not passed: dtlpy would then send `?system=true` and
        # persist whatever sits under metadata['system'].
        updated = dataloop_call("item update", lambda: dataset.items.update(item=item))
        return serialize.item(updated if updated is not None else item)
