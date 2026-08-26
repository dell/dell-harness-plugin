"""Answering "does this dataset contain PII?", and acting on the answer.

Dataloop ships a *PII Detection & Redaction (Microsoft Presidio)* solution in its marketplace.
It is a pipeline template, not a single node, so it is found, installed and run through the
existing `marketplace_*` and `pipeline_*` tools — the same route the RAG templates take
(ADR-006/ADR-007). This module deliberately does **not** own that scan.

What it owns is the part that comes after: reading the scan's verdict, and turning it into
something actionable.

**The marker is caller-supplied on purpose.** How the Presidio solution records a finding — an
annotation carrying a label, or a field in item metadata — could not be verified against a live
catalogue while this was written. Hard-coding a guess is exactly the failure this project's
review process has caught before, so instead the caller states the convention once and every
tool here derives its query from that. Confirm the convention with `pii_report` against a known
dataset before trusting a bulk action.
"""

from typing import Annotated, Any

import dtlpy as dl
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from .. import outputs, paging, serialize
from ..context import dataloop_call, get_dataset
from ..schemas import PiiFindingsSpec, PiiMarkerSpec

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
MUTATING = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)

SAMPLE_SIZE = 5
# Canonical location of scan findings: a flat map of detected entity type to true, e.g.
# metadata.user.pii = {"US_SSN": true}. An undetected type is absent rather than false, so a
# query is always "<TYPE> equals true" — which negates cleanly for exclusions.
FINDINGS_KEY = "pii"

# Source for the pipeline code node that performs the writeback on Dataloop's runners. Kept as
# one top-level function with imports inside it, which is what CodeNodeSpec requires.
WRITEBACK_CODE = '''
def write_pii_findings(item):
    """Record which kinds of PII Presidio found, in the item's own user metadata.

    Writes metadata.user.pii as a flat map of detected entity type to true, e.g.
    {"US_SSN": true, "EMAIL_ADDRESS": true}. Types that were not detected are absent rather
    than false, and no matched value is ever written: the values are the PII itself, and
    metadata is queryable and exportable.

    An item scanned with nothing found carries an empty map. That is deliberate — the map's
    presence is the only record that a scan ran at all.
    """
    results = ((item.metadata or {}).get("user", {}) or {}).get("presidio_results") or []
    detected = {}
    for finding in results:
        entity = finding.get("entity_type") or finding.get("entity")
        if entity:
            detected[entity] = True

    metadata = item.metadata or {}
    user = metadata.setdefault("user", {})
    user["pii"] = detected
    item.metadata = metadata
    item.update()
    return item
'''


def build_marker_filters(marker: PiiMarkerSpec, *, flagged: bool) -> dl.Filters:
    """A filter selecting items that do (or do not) carry the PII marker.

    Two shapes, because the marker can be either kind:

    * **metadata** — a plain field comparison on the item, negatable with `ne`.
    * **annotation label** — a join onto the item's annotations. Negating a join is the weaker
      of the two: DQL evaluates it per annotation, so "no annotation has label X" is not
      reliably expressible this way. `pii_filter` says so rather than returning a filter that
      quietly means something else.
    """
    filters = dl.Filters(resource=dl.FiltersResource.ITEM)
    operator = dl.FiltersOperations.EQUAL if flagged else dl.FiltersOperations.NOT_EQUAL
    if marker.entity_types:
        # `metadata.user.pii` holds only detected types, so each clause is "<TYPE> is true".
        # Selecting is any-of (OR); excluding is none-of, which is AND over the negations —
        # NOT(a OR b) is (NOT a) AND (NOT b), so the method flips with the operator.
        method = dl.FiltersMethod.OR if flagged else dl.FiltersMethod.AND
        for entity in marker.entity_types:
            filters.add(
                field=f"metadata.user.{FINDINGS_KEY}.{entity}",
                values=True,
                operator=operator,
                method=method,
            )
    elif marker.metadata_field is not None:
        filters.add(
            field=f"metadata.user.{marker.metadata_field}",
            values=marker.metadata_value,
            operator=operator,
        )
    else:
        filters.add_join(field="label", values=marker.annotation_label, operator=operator)
    return filters


def exact_count(listed: Any) -> tuple[int, bool]:
    """Matching item count, and whether it is a true total rather than one page."""
    total = getattr(listed, "items_count", None)
    if total is not None:
        return int(total), True
    page = getattr(listed, "items", None) or []
    return len(page), False


def register(mcp: MCPServer) -> None:
    @mcp.tool(title="Report PII findings in a dataset", annotations=READ_ONLY)
    def pii_report(
        project_id: Annotated[str, Field(description="Project that owns the dataset.")],
        dataset_id: Annotated[str, Field(description="Dataset to report on.")],
        marker: Annotated[
            PiiMarkerSpec,
            Field(description="How a PII finding is recorded — an annotation label or a metadata field."),
        ],
        sample: Annotated[
            int, Field(ge=0, le=paging.MAX_LIMIT, description="How many flagged items to show.")
        ] = SAMPLE_SIZE,
    ) -> outputs.PiiReportResult:
        """Answer "does this dataset contain PII?" for a dataset a PII scan has already run over.

        Reports how many items carry the marker, out of how many, with a sample. A zero count
        means *no item carries the marker* — which is not the same as "no PII is present" if the
        scan never ran or the marker convention is wrong, so the response states both counts and
        the exact filter used, for the caller to sanity-check.
        """
        dataset = get_dataset(project_id, dataset_id)
        flagged_filters = build_marker_filters(marker, flagged=True)
        flagged = dataloop_call("item list", lambda: dataset.items.list(filters=flagged_filters))
        flagged_count, flagged_exact = exact_count(flagged)

        total = getattr(dataset, "items_count", None)
        items = list(getattr(flagged, "items", None) or [])[:sample]

        verdict = "no items carry the PII marker" if flagged_count == 0 else "PII markers found"
        caveat = None
        if flagged_count == 0:
            caveat = (
                "A zero count only means no item carries this marker. If the PII scan has not "
                "run over this dataset, or the marker convention differs from the one supplied, "
                "this is a false negative — confirm the convention on a dataset known to contain "
                "PII before relying on it."
            )
        elif not flagged_exact:
            caveat = (
                "The platform reported no total, so the count is one page and the true number is "
                "higher. Do not use it as a basis for a bulk action."
            )
        return {
            "dataset_id": dataset_id,
            "verdict": verdict,
            "flagged_items": flagged_count,
            "flagged_count_is_exact": flagged_exact,
            "dataset_items": total,
            "marker": marker.model_dump(exclude_none=True),
            "sample": [serialize.item(entity) for entity in items],
            "caveat": caveat,
        }

    @mcp.tool(title="Record PII findings on an item", annotations=MUTATING)
    def pii_set_findings(
        project_id: Annotated[str, Field(description="Project that owns the dataset.")],
        dataset_id: Annotated[str, Field(description="Dataset that holds the item.")],
        item_id: Annotated[str, Field(description="Source item to record findings on.")],
        findings: Annotated[
            PiiFindingsSpec,
            Field(description="Entity types detected. Each becomes a `<TYPE>: true` pair."),
        ],
    ) -> outputs.PiiFindingsWriteResult:
        """Write which kinds of PII were found to the source item's `metadata.user.pii`.

        The map holds detected entity types only — `{"US_SSN": true}` — never counts, scores, or
        matched values. An undetected type is absent rather than false, so a query is always
        "<TYPE> is true", which negates cleanly and gives a dependable exclusion.

        An item scanned with nothing found gets an empty map. That empty map is the only record
        that the scan ran, so do not strip it.
        """
        dataset = get_dataset(project_id, dataset_id)
        item = dataloop_call("item lookup", lambda: dataset.items.get(item_id=item_id))

        payload = findings.as_metadata()
        metadata = dict(getattr(item, "metadata", None) or {})
        user = dict(metadata.get("user") or {})
        user[FINDINGS_KEY] = payload
        metadata["user"] = user
        item.metadata = metadata
        # system_metadata is never passed: findings belong to the user namespace.
        updated = dataloop_call("item update", lambda: dataset.items.update(item=item))
        return {
            "item_id": item_id,
            "written_to": f"metadata.user.{FINDINGS_KEY}",
            "detected_entity_types": sorted(payload),
            "findings": payload,
            "marker_hint": {"entity_types": sorted(payload)} if payload else None,
            "item": serialize.item(updated if updated is not None else item),
        }

    @mcp.tool(title="Get the PII writeback code node source", annotations=READ_ONLY)
    def pii_writeback_code() -> dict[str, Any]:
        """Return the code-node source that writes Presidio findings onto the source item.

        Add it to a pipeline with `pipeline_node_add` (which requires `confirm_code: true`),
        downstream of the Presidio anonymizer node, so every scanned item ends up carrying
        `metadata.user.pii` as a map of detected entity type to true. Review the source first: it
        runs on Dataloop's runners, and the field it reads Presidio's output from is an
        assumption — see `reads_from`.
        """
        return {
            "code": WRITEBACK_CODE.strip(),
            "node_type": "code",
            "reads_from": "metadata.user.presidio_results",
            "writes_to": f"metadata.user.{FINDINGS_KEY}",
            "writes_shape": '{"<ENTITY_TYPE>": true, ...} — detected types only, no values',
            "stores_matched_values": False,
            "caveat": (
                "The input field this reads (metadata.user.presidio_results) is an assumption "
                "about how the Presidio anonymizer node hands its findings on — it was not "
                "verifiable without the live template. Inspect one item the node has processed "
                "and adjust the source before deploying, or the writeback records an empty map "
                "for every item, which reads as 'scanned, nothing found'."
            ),
            "next_step": (
                "Show this code to the user, then deploy with pipeline_node_add using "
                "node_type='code' and confirm_code=true, connected downstream of the anonymizer."
            ),
        }

    @mcp.tool(title="Build a PII include/exclude filter", annotations=READ_ONLY)
    def pii_filter(
        project_id: Annotated[str, Field(description="Project that owns the dataset.")],
        dataset_id: Annotated[str, Field(description="Dataset the filter is meant for.")],
        marker: Annotated[PiiMarkerSpec, Field(description="How a PII finding is recorded.")],
        mode: Annotated[
            str,
            Field(
                pattern="^(only|exclude)$",
                description="'exclude' to skip flagged items; 'only' to select them.",
            ),
        ] = "exclude",
    ) -> dict[str, Any]:
        """Produce a reusable filter that skips (or selects) items carrying the PII marker.

        The returned query can be handed to a pipeline dataset node, or used to scope a model's
        training subset so PII items never reach training — `model_set_subset` accepts the same
        marker directly.
        """
        flagged = mode == "only"
        filters = build_marker_filters(marker, flagged=flagged)
        prepared = dataloop_call("filter prepare", lambda: filters.prepare())

        warning = None
        if mode == "exclude" and marker.annotation_label is not None:
            # Honest about the weaker of the two shapes rather than returning a query whose
            # meaning differs from the caller's intent.
            warning = (
                "Excluding by annotation label uses a negated join, which DQL evaluates per "
                "annotation: an item with both a PII annotation and a non-PII annotation can "
                "still match. For a dependable exclusion, have the scan record its verdict in "
                "item metadata and use metadata_field instead. Verify against a known dataset "
                "before relying on this filter to withhold data."
            )
        return {
            "dataset_id": dataset_id,
            "mode": mode,
            "marker": marker.model_dump(exclude_none=True),
            "filter": prepared,
            "warning": warning,
        }
