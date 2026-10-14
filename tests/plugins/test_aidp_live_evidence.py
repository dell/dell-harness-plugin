# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_catalog_references_include_current_metadata_gates():
    ddae = (
        ROOT
        / "dell-data-analytics-engine/skills/configure-dell-data-analytics-engine/references/validated-tool-catalog.md"
    ).read_text(encoding="utf-8")
    assert "OpenAI metadata audit — August 12, 2026" in ddae
    assert "Authenticated Codex revalidation — September 10, 2026" in ddae
    assert "does not mark it required" in ddae
    assert "without reading customer data" in ddae
    assert "raw contract confirmation" in ddae
    assert "Omitted-argument execution was intentionally not" in ddae

    live_smoke = json.loads(
        (
            ROOT
            / "evals/results/ddae-codex-initial-review-20260910/live-safe-smoke.json"
        ).read_text(encoding="utf-8")
    )
    assert live_smoke["endpoint"] == "<redacted-private-ddae-endpoint>"
    assert live_smoke["authentication"]["status"] == "success"
    assert live_smoke["safety"]["customer_data_accessed"] is False
    assert [item["tool"] for item in live_smoke["executions"]] == [
        "searchDataProducts",
        "queryReadOnly",
    ]
    assert all(item["structured_output"] for item in live_smoke["executions"])

    ddse = (
        ROOT
        / "dell-data-search-engine/skills/configure-dell-data-search-engine/references/validated-tool-catalog.md"
    ).read_text(encoding="utf-8")
    assert "OpenAI metadata audit — September 10, 2026" in ddse
    assert "None exposes an output schema" in ddse
    assert "authenticated successfully" in ddse
    assert "did not invoke any product tool or read customer data" in ddse
    assert "index-free constant ES|QL expression" in ddse
    assert "`structuredContent`" in ddse
    assert "raw tool content was neither printed nor retained" in ddse
    assert (
        "That textual observation does not replace required server annotations" in ddse
    )
