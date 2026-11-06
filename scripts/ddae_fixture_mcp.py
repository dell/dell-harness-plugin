#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Local read-only MCP server backed only by the synthetic DDAE review fixture."""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "evals" / "fixtures" / "ddae-review-fixture.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
mcp = FastMCP(
    "DDAE Synthetic Review Fixture",
    instructions=(
        "Synthetic, non-customer evaluation data only. All tools are read-only. "
        "Never claim this fixture is a live customer deployment."
    ),
)


def audit(tool: str, arguments: dict[str, Any]) -> str:
    event_id = f"ddae-fixture-{uuid.uuid4()}"
    path = os.environ.get("DDAE_FIXTURE_AUDIT_LOG", "").strip()
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


@mcp.tool(
    name="searchDataProducts",
    annotations=READ_ONLY,
    structured_output=True,
)
def search_data_products(query: str) -> dict[str, Any]:
    """Search only Data Products authorized for this synthetic review identity."""
    normalized = query.strip().lower()
    if not normalized:
        raise ValueError("A narrow business search term is required.")
    restricted = any(
        product.lower() in normalized
        for product in FIXTURE["restricted_data_products"]
    )
    relevant = any(
        term in normalized
        for term in ("fulfillment", "fulfilment", "order", "delay")
    )
    products = [] if restricted or not relevant else FIXTURE["authorized_data_products"]
    return {
        "fixture_id": FIXTURE["fixture_id"],
        "query": query,
        "products": products,
        "match_count": len(products),
        "authorization_scope": "current_review_identity_only",
        "message": (
            "No matching Data Product is available to this identity."
            if not products
            else "Returned only authorized matching Data Products."
        ),
        "audit_event_id": audit("searchDataProducts", {"query": query}),
    }


def validate_read_only_query(query: str) -> str:
    normalized = " ".join(query.strip().split())
    lowered = normalized.lower()
    if not normalized:
        raise ValueError("query is required")
    if not re.match(r"^(select|with)\b", lowered):
        raise ValueError("Only one read-only SELECT statement is allowed.")
    if ";" in normalized.rstrip(";"):
        raise ValueError("Multiple statements are not allowed.")
    if re.search(
        r"\b(insert|update|delete|merge|drop|alter|create|grant|revoke|call|execute)\b",
        lowered,
    ):
        raise ValueError("Write, administrative, and procedure statements are blocked.")
    if re.search(r"select\s+\*", lowered):
        raise ValueError("SELECT * is not allowed; request explicit columns.")
    if " limit " not in f" {lowered} ":
        raise ValueError("A stable LIMIT is required for bounded review queries.")
    if any(
        product.lower() in lowered
        for product in FIXTURE["restricted_data_products"]
    ):
        raise ValueError("The requested Data Product is not authorized.")
    return normalized


AUTHORIZED_TABLE = "synthetic.fulfillment_orders"
QUERY_IDS = {
    "delayed": "ddae-synthetic-delayed-orders-v1",
    "antarctica": "ddae-synthetic-antarctica-v1",
}


def evaluate(lowered: str) -> tuple[str, int]:
    """The query ID and delayed-order count this exact statement would return.

    Only a query on the authorized table that filters delayed status and bounds the fixed window
    on both sides gets the reference answer, so a wrong table, a missing filter, or an open
    window can never produce a passing result.
    """
    tables = set(re.findall(r"\b(?:from|join)\s+([a-z0-9_.\"]+)", lowered))
    tables = {table.strip('"') for table in tables}
    if tables != {AUTHORIZED_TABLE}:
        raise ValueError(
            f"Only {AUTHORIZED_TABLE} is authorized for this review identity; got {sorted(tables) or 'none'}."
        )
    window = FIXTURE["fixed_window"]
    status = re.search(r"fulfillment_status\s*=\s*'delayed'", lowered)
    lower = re.search(r"event_time\s*>=\s*(?:timestamp\s*)?'" + re.escape(window["start"].lower()), lowered)
    upper = re.search(r"event_time\s*<\s*(?:timestamp\s*)?'" + re.escape(window["end"].lower()), lowered)
    if not (status and lower and upper):
        raise ValueError(
            "Bounded review queries must filter fulfillment_status = 'delayed' and the fixed "
            f"window event_time >= '{window['start']}' AND event_time < '{window['end']}'."
        )
    result = FIXTURE["expected_results"]
    if "region" in lowered and "antarctica" in lowered:
        return QUERY_IDS["antarctica"], result["antarctica_delayed_order_count"]
    return QUERY_IDS["delayed"], result["delayed_order_count"]


@mcp.tool(
    name="queryReadOnly",
    annotations=READ_ONLY,
    structured_output=True,
)
def query_read_only(query: str) -> dict[str, Any]:
    """Execute one bounded, read-only query over the authorized synthetic table."""
    normalized = validate_read_only_query(query)
    query_id, delayed_count = evaluate(normalized.lower())
    return {
        "fixture_id": FIXTURE["fixture_id"],
        "data_product_id": "dp_fulfillment_v1",
        "table": AUTHORIZED_TABLE,
        "query_id": query_id,
        "columns": ["delayed_orders"],
        "rows": [[delayed_count]],
        "row_count": 1,
        "truncated": False,
        "audit_event_id": audit("queryReadOnly", {"query": normalized}),
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
