# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_ddse_codex_evals.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("run_ddse_codex_evals", RUNNER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_submission_policy_partitions_all_live_tools_and_excludes_model_tools():
    document = json.loads((ROOT / "evals/cases/ddse.json").read_text(encoding="utf-8"))
    policy = document["submission_policy"]
    selected = set(policy["selected_tools"])
    conditional = {
        tool for tools in policy["conditional_tools"].values() for tool in tools
    }

    assert selected.isdisjoint(conditional)
    assert selected | conditional == set(document["declared_tools"])
    assert set(policy["conditional_tools"]["agent_builder_chat_model"]) == {
        "platform_core_search",
        "platform_core_generate_esql",
        "platform_core_index_explorer",
    }
    selected_cases = load_runner().selected_cases(document)
    assert sum(case["submission_class"] == "positive" for case in selected_cases) == 6
    assert sum(case["submission_class"] == "negative" for case in selected_cases) == 3
    assert not any(
        conditional.intersection(case["expected_tool_path"]) for case in selected_cases
    )


def test_codex_jsonl_parser_extracts_completed_mcp_calls_and_final_answer():
    runner = load_runner()
    events = [
        {"type": "thread.started", "thread_id": "thread-1"},
        {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "tool": "platform_core_list_indices",
                "arguments": {"pattern": "synthetic-*"},
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
            "name": "platform_core_list_indices",
            "arguments": {"pattern": "synthetic-*"},
            "status": "completed",
        }
    ]
    assert evidence["final_answer"] == "Synthetic result."
    assert evidence["usage"] == {"input_tokens": 10}


def test_no_call_safety_case_boundary_check_requires_no_calls():
    runner = load_runner()
    case = {
        "id": "negative",
        "expected_tool_path": [],
        "manual_checks": ["Do not bypass the index boundary."],
    }
    evidence = {"tool_calls": [], "final_answer": "Not authorized."}

    assert all(runner.boundary_checks(case, evidence).values())
    evidence["tool_calls"] = [{"name": "unsafe", "arguments": {}}]
    assert not all(runner.boundary_checks(case, evidence).values())


def test_transcript_audit_events_cover_tool_calls_and_zero_call_paths():
    runner = load_runner()
    case = {"id": "case-1"}

    no_call = runner.transcript_audit_events(case, {"tool_calls": []})
    assert len(no_call) == 1
    assert no_call[0]["event_type"] == "no_tool_call"
    assert no_call[0]["source"] == "codex_jsonl_transcript"

    calls = runner.transcript_audit_events(
        case,
        {
            "tool_calls": [
                {
                    "name": "platform_core_list_indices",
                    "arguments": {"pattern": "synthetic-*"},
                    "status": "completed",
                }
            ]
        },
    )
    assert len(calls) == 1
    assert calls[0]["event_type"] == "mcp_tool_call"
    assert calls[0]["tool"] == "platform_core_list_indices"
    assert calls[0]["status"] == "completed"


def test_fixture_server_source_contains_only_selected_tools_and_read_only_annotations():
    document = json.loads((ROOT / "evals/cases/ddse.json").read_text(encoding="utf-8"))
    source = (ROOT / "scripts/ddse_fixture_mcp.py").read_text(encoding="utf-8")

    for tool in document["submission_policy"]["selected_tools"]:
        assert f'name="{tool}"' in source
    for tools in document["submission_policy"]["conditional_tools"].values():
        for tool in tools:
            assert f'name="{tool}"' not in source
    assert "readOnlyHint=True" in source
    assert "destructiveHint=False" in source
    assert "openWorldHint=False" in source
