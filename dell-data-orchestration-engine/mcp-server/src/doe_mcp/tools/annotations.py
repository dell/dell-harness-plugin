# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Reading and writing annotations on an item.

``Annotations`` has no ``create``: writing goes through ``upload()`` with an
``AnnotationCollection``, which forces a choice of annotation *type*. Spec 26-08-20-DOEMCP-002
resolves that choice (ADR-002) as **classification only** for v1 — whole-image labels carry no
coordinates, so the write path can be validated end to end without depending on what a given
model emits. Geometry types are refused rather than approximated.

Labels are checked against the dataset's own ontology before writing (Ambiguity 8): DOE's
behaviour for unknown labels is unverified, and quietly accumulating labels the ontology cannot
resolve is worse than one extra read per write.

Deletion supports the operation that actually matters after a bad classifier run — remove
everything a given model wrote — which is why ``annotation_delete_by_model`` exists alongside the
single-annotation delete.
"""

from typing import Annotated, Any

import dtlpy as dl
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import Field

from .. import outputs, paging, serialize
from ..context import dataloop_call, get_dataset
from ..guards import confirm_or_elicit
from ..safety import ADDITIVE, DELETE, READ_ONLY

# Where dtlpy records model attribution; see AnnotationCollection.add(model_info=...).
MODEL_ID_FIELD = "metadata.user.model.model_id"
# Cap on labels inlined into an error message.
LABELS_IN_ERROR = 25


def count_matches(listed: Any) -> tuple[int, bool]:
    """Number of matching annotations, and whether that number is exact.

    A paged response that reports no total tells us only what one page holds. Quoting a page
    size as if it were the total would put a number in a confirmation prompt that the user
    then authorises — while the real deletion is unbounded.
    """
    total = getattr(listed, "items_count", None)
    if total is not None:
        return int(total), True
    page = getattr(listed, "items", None) or getattr(listed, "annotations", None) or []
    return len(page), False


def ontology_labels(dataset: Any) -> list[str]:
    """Every label the dataset's recipe defines, flattened."""
    flat = getattr(dataset, "labels_flat_dict", None) or {}
    return sorted(flat.keys())


def validate_label(dataset: Any, label: str) -> None:
    """Refuse a label the dataset's ontology cannot resolve, before any write."""
    available = ontology_labels(dataset)
    if not available:
        raise ValueError(
            f"Dataset {getattr(dataset, 'id', '?')} has no ontology labels defined, so there is "
            "nothing to validate this annotation against. Add labels to the dataset's recipe first."
        )
    if label not in available:
        # Bounded on purpose: an ontology can carry thousands of labels, and §4.2's
        # bounded-response guarantee has to hold on the error path too.
        shown = available[:LABELS_IN_ERROR]
        more = len(available) - len(shown)
        suffix = f" (+{more} more; call annotation_labels for the full list)" if more else ""
        raise ValueError(
            f"Label {label!r} is not in this dataset's ontology. "
            f"Available labels: {', '.join(shown)}{suffix}."
        )


def register(mcp: MCPServer) -> None:
    @mcp.tool(title="List item annotations", annotations=READ_ONLY)
    def annotation_list(
        project_id: Annotated[str, Field(description="Project that owns the dataset.")],
        dataset_id: Annotated[str, Field(description="Dataset that holds the item.")],
        item_id: Annotated[str, Field(description="Item whose annotations to list.")],
        label: Annotated[str | None, Field(description="Only annotations carrying this label.")] = None,
        page: Annotated[int, Field(ge=0, description="Zero-based page number.")] = 0,
        page_size: Annotated[
            int, Field(ge=1, le=paging.MAX_LIMIT, description="Annotations per page.")
        ] = paging.DEFAULT_LIMIT,
        limit: Annotated[int, Field(ge=1, le=paging.MAX_LIMIT, description="Max annotations to return.")] = (
            paging.DEFAULT_LIMIT
        ),
        fields: Annotated[list[str] | None, Field(description=paging.FIELDS_DESCRIPTION)] = None,
    ) -> paging.ListResult:
        """List the annotations on one item, e.g. to review what a classifier tagged it with."""
        dataset = get_dataset(project_id, dataset_id)
        item = dataloop_call("item lookup", lambda: dataset.items.get(item_id=item_id))
        filters = dl.Filters(resource=dl.FiltersResource.ANNOTATION)
        if label is not None:
            filters.add(field="label", values=label)
        listed = dataloop_call(
            "annotation list",
            lambda: item.annotations.list(filters=filters, page_offset=page, page_size=page_size),
        )
        # item.annotations.list returns an AnnotationCollection (or a paged shape when filtered);
        # both expose the entities through .items.
        entities = getattr(listed, "annotations", None) or getattr(listed, "items", None) or []
        if hasattr(listed, "items"):
            return paging.from_paged(listed, serialize.annotation, limit=limit, fields=fields)
        return paging.from_list(
            entities,
            serialize.annotation,
            limit=limit,
            page=page,
            page_size=page_size,
            fields=fields,
        )

    @mcp.tool(title="Get annotation", annotations=READ_ONLY)
    def annotation_get(
        project_id: Annotated[str, Field(description="Project that owns the dataset.")],
        dataset_id: Annotated[str, Field(description="Dataset that holds the item.")],
        annotation_id: Annotated[str, Field(description="Id of the annotation to fetch.")],
    ) -> outputs.AnnotationSummary:
        """Fetch one annotation by id."""
        dataset = get_dataset(project_id, dataset_id)
        return serialize.annotation(
            dataloop_call("annotation lookup", lambda: dataset.annotations.get(annotation_id=annotation_id))
        )

    @mcp.tool(title="List dataset ontology labels", annotations=READ_ONLY)
    def annotation_labels(
        project_id: Annotated[str, Field(description="Project that owns the dataset.")],
        dataset_id: Annotated[str, Field(description="Dataset whose ontology to read.")],
    ) -> outputs.AnnotationLabelsResult:
        """List the labels this dataset's recipe defines.

        Only these labels can be applied to items, so check here before tagging.
        """
        dataset = get_dataset(project_id, dataset_id)
        labels = dataloop_call("ontology read", lambda: ontology_labels(dataset))
        return {"dataset_id": dataset_id, "labels": labels, "count": len(labels)}

    @mcp.tool(title="Create classification annotation", annotations=ADDITIVE)
    def annotation_create(
        project_id: Annotated[str, Field(description="Project that owns the dataset.")],
        dataset_id: Annotated[str, Field(description="Dataset that holds the item.")],
        item_id: Annotated[str, Field(description="Item to annotate.")],
        label: Annotated[
            str, Field(min_length=1, description="Label to apply. Must exist in the dataset's ontology.")
        ],
        model_id: Annotated[
            str | None,
            Field(description="Model this prediction came from, recorded for review and bulk removal."),
        ] = None,
        model_name: Annotated[str | None, Field(description="Name of that model.")] = None,
        confidence: Annotated[
            float | None,
            Field(ge=0, le=1, description="Model confidence 0-1. Required when model_id is given."),
        ] = None,
    ) -> dict[str, Any]:
        """Attach a whole-image classification label to an item.

        Only classification is supported: no boxes, points or polygons. Supply `model_id` and
        `confidence` for machine-generated tags so they can be reviewed, traced, and removed as a
        batch later.
        """
        dataset = get_dataset(project_id, dataset_id)
        validate_label(dataset, label)
        if model_id is not None and confidence is None:
            # dtlpy rejects model_info without confidence; fail here with a clearer reason.
            raise ValueError("Provide `confidence` (0-1) alongside `model_id`.")
        model_info = (
            {"name": model_name or model_id, "model_id": model_id, "confidence": confidence}
            if model_id is not None
            else None
        )

        item = dataloop_call("item lookup", lambda: dataset.items.get(item_id=item_id))
        builder = item.annotations.builder()
        builder.add(annotation_definition=dl.Classification(label=label), model_info=model_info)
        uploaded = dataloop_call("annotation upload", lambda: item.annotations.upload(builder))
        entities = getattr(uploaded, "annotations", None) or list(uploaded or [])
        return {
            "item_id": item_id,
            "created": [serialize.annotation(entity) for entity in entities],
        }

    @mcp.tool(title="Delete annotation", annotations=DELETE)
    def annotation_delete(
        project_id: Annotated[str, Field(description="Project that owns the dataset.")],
        dataset_id: Annotated[str, Field(description="Dataset that holds the annotation.")],
        annotation_id: Annotated[str, Field(description="Id of the annotation to delete.")],
        confirm: Annotated[bool, Field(description="Must be true to proceed.")] = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Delete one annotation."""
        confirm_or_elicit(ctx, confirm, "delete this annotation", "the annotation is removed permanently")
        dataset = get_dataset(project_id, dataset_id)
        deleted = dataloop_call(
            "annotation delete", lambda: dataset.annotations.delete(annotation_id=annotation_id)
        )
        return {"deleted": bool(deleted), "id": annotation_id}

    @mcp.tool(title="Delete a model's annotations", annotations=DELETE)
    def annotation_delete_by_model(
        project_id: Annotated[str, Field(description="Project that owns the dataset.")],
        dataset_id: Annotated[str, Field(description="Dataset to clean up.")],
        model_id: Annotated[str, Field(description="Only annotations attributed to this model are removed.")],
        confirm: Annotated[
            bool, Field(description="Must be true. Call once without it to see how many would go.")
        ] = False,
        expected_count: Annotated[
            int | None,
            Field(
                ge=0,
                description=(
                    "The count the user agreed to. If the set has changed since, the delete is "
                    "refused instead of removing more than was authorised."
                ),
            ),
        ] = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Remove every annotation a given model wrote in a dataset — undo a bad auto-tagging run.

        Annotations authored by people, or by a different model, are untouched. Called without
        confirmation this reports the count and deletes nothing, so the caller can check the blast
        radius first, then repeat the call with `confirm` and the `expected_count` it reported.

        The set can grow between the two calls — the trigger this feature creates keeps writing
        annotations — so passing `expected_count` is what stops a confirmation for 2 authorising
        the deletion of 500.
        """
        dataset = get_dataset(project_id, dataset_id)
        filters = dl.Filters(resource=dl.FiltersResource.ANNOTATION)
        filters.add(field=MODEL_ID_FIELD, values=model_id)
        listed = dataloop_call("annotation list", lambda: dataset.annotations.list(filters=filters))
        count, exact = count_matches(listed)

        if not confirm:
            phrasing = (
                f"{count} annotation(s)"
                if exact
                else f"at least {count} annotation(s) (the platform reported no total, so this is "
                "one page and the true number is unknown)"
            )
            # No expected_count needed on the elicitation path: the human sees this live count
            # directly in the prompt, rather than a count reported by an earlier, separate call.
            confirm_or_elicit(
                ctx,
                False,
                f"delete annotations from model {model_id}",
                f"{phrasing} from this dataset would be removed. Re-call with confirm=true and "
                f"expected_count={count} once the user has agreed to that number.",
            )
        elif expected_count is not None and expected_count != count:
            raise ValueError(
                f"Refusing to delete: {count} annotation(s) now match, but the user agreed to "
                f"{expected_count}. The set changed between confirmation and deletion — most "
                "likely the classifier trigger is still writing. Re-check before deleting."
            )
        if not exact:
            raise ValueError(
                "Refusing to delete: the platform did not report how many annotations match, so "
                "the blast radius is unknown. Narrow the dataset or delete individually."
            )
        deleted = dataloop_call("annotation delete", lambda: dataset.annotations.delete(filters=filters))
        return {"deleted": bool(deleted), "model_id": model_id, "matched": count}
