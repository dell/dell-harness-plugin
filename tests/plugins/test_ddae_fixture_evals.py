# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
from pathlib import Path

import pytest

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


GOOD_QUERY = (
    "SELECT COUNT(order_id) AS delayed_orders FROM synthetic.fulfillment_orders "
    "WHERE fulfillment_status = 'delayed' AND event_time >= '2026-08-01T00:00:00Z' "
    "AND event_time < '2026-08-08T00:00:00Z' LIMIT 1"
)
RESULT = {"query_id": "ddae-synthetic-delayed-orders-v1", "rows": [[17]], "table": "synthetic.fulfillment_orders"}
CASE = {
    "id": "ddae-bounded-analysis-explicit",
    "expected_tool_path": ["queryReadOnly"],
    "manual_checks": ["Use one read-only statement.", "Report the query ID and the returned count."],
}


def evidence_for(query, answer, result=RESULT, server="dell_data_analytics_engine"):
    call = {"name": "queryReadOnly", "server": server, "arguments": {"query": query}, "status": "completed",
            "result": result}
    return {"tool_calls": [call], "final_answer": answer}


def test_query_boundary_passes_only_the_intended_bounded_query_with_its_query_id():
    runner = load_runner()
    good = evidence_for(GOOD_QUERY, "17 delayed orders (query ID ddae-synthetic-delayed-orders-v1).")
    assert all(runner.boundary_checks(CASE, good).values())


def test_wrong_table_query_fails_every_check():
    runner = load_runner()
    wrong = GOOD_QUERY.replace("synthetic.fulfillment_orders", "synthetic.order_operations")
    evidence = evidence_for(wrong, "17 delayed orders (query ID ddae-synthetic-delayed-orders-v1).")
    assert not any(runner.boundary_checks(CASE, evidence).values())


def test_answer_without_the_returned_query_id_fails():
    runner = load_runner()
    evidence = evidence_for(GOOD_QUERY, "The synthetic evidence returned 17 delayed orders with LIMIT 1.")
    checks = runner.boundary_checks(CASE, evidence)
    assert checks["Report the query ID and the returned count."] is False


def test_answer_must_match_the_returned_result_not_the_fixture_constant():
    runner = load_runner()
    other = {**RESULT, "rows": [[9]]}
    evidence = evidence_for(GOOD_QUERY, "17 delayed orders (query ID ddae-synthetic-delayed-orders-v1).", other)
    assert not all(runner.boundary_checks(CASE, evidence).values())


@pytest.mark.parametrize("query", [
    "SELECT * FROM synthetic.fulfillment_orders LIMIT 1",
    GOOD_QUERY.replace(" LIMIT 1", ""),
    GOOD_QUERY.replace("fulfillment_status = 'delayed' AND ", ""),
    GOOD_QUERY.replace("AND event_time < '2026-08-08T00:00:00Z' ", ""),
])
def test_unbounded_or_unfiltered_queries_fail(query):
    runner = load_runner()
    evidence = evidence_for(query, "17 delayed orders (query ID ddae-synthetic-delayed-orders-v1).")
    assert not all(runner.boundary_checks(CASE, evidence).values())


def test_calls_to_another_endpoint_fail():
    runner = load_runner()
    evidence = evidence_for(GOOD_QUERY, "17 (ddae-synthetic-delayed-orders-v1)", server="other_server")
    assert not any(runner.boundary_checks(CASE, evidence).values())


def test_fixture_answers_only_the_intended_table_and_filters(monkeypatch):
    spec = importlib.util.spec_from_file_location("ddae_fixture_mcp", ROOT / "scripts" / "ddae_fixture_mcp.py")
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    assert fixture.query_read_only(GOOD_QUERY)["query_id"] == "ddae-synthetic-delayed-orders-v1"
    for query in (GOOD_QUERY.replace("synthetic.fulfillment_orders", "synthetic.order_operations"),
                  GOOD_QUERY.replace("fulfillment_status = 'delayed' AND ", "")):
        with pytest.raises(ValueError):
            fixture.query_read_only(query)


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
