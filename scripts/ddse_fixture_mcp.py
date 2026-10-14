#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Local read-only MCP server backed only by the synthetic DDSE review fixture."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "evals" / "fixtures" / "ddse-review-fixture.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
mcp = FastMCP(
    "DDSE Synthetic Review Fixture",
    instructions=(
        "Synthetic, non-customer evaluation data only. All tools are read-only. "
        "Never claim this fixture is a live customer deployment."
    ),
)


def audit(tool: str, arguments: dict[str, Any]) -> str:
    event_id = f"ddse-fixture-{uuid.uuid4()}"
    path = os.environ.get("DDSE_FIXTURE_AUDIT_LOG", "").strip()
    if path:
        record = {
            "event_id": event_id,
            "fixture_id": FIXTURE["fixture_id"],
            "timestamp": datetime.now(UTC).isoformat(),
            "tool": tool,
            "arguments": arguments,
        }
        with Path(path).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
    return event_id


def reject_restricted(value: str) -> None:
    if any(name in value for name in FIXTURE["indices"]["restricted"]):
        raise ValueError(
            "The requested index is not authorized for this review identity."
        )


@mcp.tool(
    name="platform_core_list_indices",
    annotations=READ_ONLY,
    structured_output=True,
)
def list_indices(pattern: str = "*") -> dict[str, Any]:
    """List synthetic indices authorized for the fixture identity."""
    reject_restricted(pattern)
    matches = [
        name
        for name in FIXTURE["indices"]["authorized"]
        if pattern == "*" or pattern.strip("*") in name
    ]
    return {
        "fixture_id": FIXTURE["fixture_id"],
        "indices": matches,
        "audit_event_id": audit("platform_core_list_indices", {"pattern": pattern}),
    }


@mcp.tool(
    name="platform_core_get_index_mapping",
    annotations=READ_ONLY,
    structured_output=True,
)
def get_index_mapping(indices: list[str]) -> dict[str, Any]:
    """Return stable mappings for authorized synthetic indices."""
    for index in indices:
        reject_restricted(index)
    unknown = sorted(set(indices) - set(FIXTURE["indices"]["authorized"]))
    mappings = {
        index: {
            "properties": {
                "@timestamp": {"type": "date"},
                "event_type": {"type": "keyword"},
                "service_name": {"type": "keyword"},
            }
        }
        for index in indices
        if index in FIXTURE["indices"]["authorized"]
    }
    return {
        "fixture_id": FIXTURE["fixture_id"],
        "mappings": mappings,
        "missing_indices": unknown,
        "audit_event_id": audit(
            "platform_core_get_index_mapping", {"indices": indices}
        ),
    }


@mcp.tool(
    name="platform_core_execute_esql",
    annotations=READ_ONLY,
    structured_output=True,
)
def execute_esql(query: str) -> dict[str, Any]:
    """Execute a bounded synthetic ES|QL count query."""
    reject_restricted(query)
    normalized = query.upper()
    if "LIMIT" not in normalized:
        raise ValueError("Fixture ES|QL must include LIMIT.")
    return {
        "fixture_id": FIXTURE["fixture_id"],
        "columns": ["event_type", "event_count"],
        "values": [["payment_timeout", FIXTURE["orders"]["payment_timeout_count"]]],
        "window": FIXTURE["fixed_window"],
        "audit_event_id": audit("platform_core_execute_esql", {"query": query}),
    }


@mcp.tool(
    name="platform_core_get_document_by_id",
    annotations=READ_ONLY,
    structured_output=True,
)
def get_document_by_id(index: str, document_id: str) -> dict[str, Any]:
    """Retrieve the one known synthetic order document."""
    reject_restricted(index)
    document = FIXTURE["orders"]["known_document"]
    found = index == document["_index"] and document_id == document["_id"]
    return {
        "fixture_id": FIXTURE["fixture_id"],
        "found": found,
        "document": document if found else None,
        "audit_event_id": audit(
            "platform_core_get_document_by_id",
            {"index": index, "document_id": document_id},
        ),
    }


@mcp.tool(
    name="observability_get_services",
    annotations=READ_ONLY,
    structured_output=True,
)
def get_services(start: str, end: str) -> dict[str, Any]:
    """Return the synthetic service visible in the fixed window."""
    return {
        "fixture_id": FIXTURE["fixture_id"],
        "services": [FIXTURE["observability"]["service"]],
        "window": {"start": start, "end": end},
        "audit_event_id": audit(
            "observability_get_services", {"start": start, "end": end}
        ),
    }


@mcp.tool(
    name="observability_get_downstream_dependencies",
    annotations=READ_ONLY,
    structured_output=True,
)
def get_downstream_dependencies(
    service_name: str, start: str, end: str
) -> dict[str, Any]:
    """Return the synthetic downstream service dependency."""
    return {
        "fixture_id": FIXTURE["fixture_id"],
        "service": service_name,
        "dependencies": [FIXTURE["observability"]["downstream_dependency"]],
        "window": {"start": start, "end": end},
        "audit_event_id": audit(
            "observability_get_downstream_dependencies",
            {"service_name": service_name, "start": start, "end": end},
        ),
    }


@mcp.tool(
    name="observability_get_trace_metrics",
    annotations=READ_ONLY,
    structured_output=True,
)
def get_trace_metrics(service_name: str, start: str, end: str) -> dict[str, Any]:
    """Return bounded synthetic trace metrics."""
    data = FIXTURE["observability"]
    return {
        "fixture_id": FIXTURE["fixture_id"],
        "service": service_name,
        "trace_id": data["trace_id"],
        "failure_rate_percent": data["failure_rate_percent"],
        "window": {"start": start, "end": end},
        "audit_event_id": audit(
            "observability_get_trace_metrics",
            {"service_name": service_name, "start": start, "end": end},
        ),
    }


@mcp.tool(
    name="observability_get_correlated_logs",
    annotations=READ_ONLY,
    structured_output=True,
)
def get_correlated_logs(trace_id: str, start: str, end: str) -> dict[str, Any]:
    """Return the synthetic log category correlated to a known trace."""
    return {
        "fixture_id": FIXTURE["fixture_id"],
        "trace_id": trace_id,
        "categories": [FIXTURE["observability"]["correlated_log_category"]],
        "window": {"start": start, "end": end},
        "audit_event_id": audit(
            "observability_get_correlated_logs",
            {"trace_id": trace_id, "start": start, "end": end},
        ),
    }


@mcp.tool(
    name="observability_run_log_rate_analysis",
    annotations=READ_ONLY,
    structured_output=True,
)
def run_log_rate_analysis(
    service_name: str,
    current_start: str,
    current_end: str,
    comparison_start: str,
    comparison_end: str,
) -> dict[str, Any]:
    """Compare synthetic checkout log counts across two bounded windows."""
    data = FIXTURE["observability"]
    arguments = {
        "service_name": service_name,
        "current_start": current_start,
        "current_end": current_end,
        "comparison_start": comparison_start,
        "comparison_end": comparison_end,
    }
    return {
        "fixture_id": FIXTURE["fixture_id"],
        "service": service_name,
        "current_count": data["current_log_count"],
        "comparison_count": data["comparison_log_count"],
        "increase_percent": data["log_rate_increase_percent"],
        "audit_event_id": audit("observability_run_log_rate_analysis", arguments),
    }


@mcp.tool(
    name="observability_get_log_categories",
    annotations=READ_ONLY,
    structured_output=True,
)
def get_log_categories(service_name: str, start: str, end: str) -> dict[str, Any]:
    """Return bounded synthetic log categories for a service."""
    return {
        "fixture_id": FIXTURE["fixture_id"],
        "service": service_name,
        "categories": [
            {
                "name": FIXTURE["observability"]["correlated_log_category"],
                "count": 24,
            }
        ],
        "window": {"start": start, "end": end},
        "audit_event_id": audit(
            "observability_get_log_categories",
            {"service_name": service_name, "start": start, "end": end},
        ),
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
