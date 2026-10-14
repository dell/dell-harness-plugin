# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_ddae_codex_evals.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("run_ddae_codex_evals", RUNNER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_submission_policy_selects_both_read_only_tools():
    document = json.loads((ROOT / "evals/cases/ddae.json").read_text(encoding="utf-8"))
    policy = document["submission_policy"]

    assert policy["mode"] == "deterministic_read_only"
    assert set(policy["selected_tools"]) == set(document["declared_tools"])
    assert policy["conditional_tools"] == {}
    selected = load_runner().selected_cases(document)
    assert sum(case["submission_class"] == "positive" for case in selected) == 6
    assert sum(case["submission_class"] == "negative" for case in selected) == 6


def test_codex_jsonl_parser_extracts_calls_and_answer():
    runner = load_runner()
    events = [
        {"type": "thread.started", "thread_id": "thread-1"},
        {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "tool": "searchDataProducts",
                "arguments": {"query": "fulfillment"},
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "Synthetic result."},
        },
        {"type": "turn.completed", "usage": {"input_tokens": 10}},
    ]

    evidence = runner.extract_evidence(events)

    assert evidence["thread_id"] == "thread-1"
    assert evidence["tool_calls"] == [
        {
            "name": "searchDataProducts",
            "arguments": {"query": "fulfillment"},
            "status": "completed",
        }
    ]
    assert evidence["final_answer"] == "Synthetic result."
    assert evidence["usage"] == {"input_tokens": 10}


def test_no_call_boundary_requires_no_calls():
    runner = load_runner()
    case = {
        "id": "ddae-bounded-analysis-write",
        "expected_tool_path": [],
        "manual_checks": ["Refuse the write operation."],
    }
    evidence = {"tool_calls": [], "final_answer": "The interface is read-only."}

    assert all(runner.boundary_checks(case, evidence).values())
    evidence["tool_calls"] = [{"name": "queryReadOnly", "arguments": {}}]
    assert not all(runner.boundary_checks(case, evidence).values())


def test_query_boundary_requires_select_explicit_columns_and_limit():
    runner = load_runner()
    case = {
        "id": "ddae-bounded-analysis-explicit",
        "expected_tool_path": ["queryReadOnly"],
        "manual_checks": ["Use one read-only statement."],
    }
    evidence = {
        "tool_calls": [
            {
                "name": "queryReadOnly",
                "arguments": {"query": "SELECT count(order_id) FROM synthetic.fulfillment_orders LIMIT 1"},
            }
        ],
        "final_answer": "17 delayed orders.",
    }
    assert all(runner.boundary_checks(case, evidence).values())
    evidence["tool_calls"][0]["arguments"]["query"] = "SELECT * FROM synthetic.fulfillment_orders"
    assert not all(runner.boundary_checks(case, evidence).values())


def test_fixture_server_exposes_only_selected_tools_with_safe_annotations():
    document = json.loads((ROOT / "evals/cases/ddae.json").read_text(encoding="utf-8"))
    source = (ROOT / "scripts/ddae_fixture_mcp.py").read_text(encoding="utf-8")

    for tool in document["submission_policy"]["selected_tools"]:
        assert f'name="{tool}"' in source
    assert source.count("@mcp.tool(") == 2
    assert "readOnlyHint=True" in source
    assert "destructiveHint=False" in source
    assert "openWorldHint=False" in source
    assert "def search_data_products(query: str)" in source
    assert "def query_read_only(query: str)" in source


def test_review_fixture_is_synthetic_and_covers_selected_cases():
    fixture = json.loads(
        (ROOT / "evals/fixtures/ddae-review-fixture.json").read_text(encoding="utf-8")
    )
    assert fixture["data_classification"] == "synthetic_non_customer"
    document = json.loads((ROOT / "evals/cases/ddae.json").read_text(encoding="utf-8"))
    selected = {
        variant["id"]
        for workflow in document["workflows"]
        for variant in workflow["variants"]
        if variant["submission_class"] in {"positive", "negative"}
    }
    assert set(fixture["selected_cases"]) == selected
