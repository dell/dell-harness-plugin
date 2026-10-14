#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Capture genuine Codex JSONL evidence against the synthetic DDSE fixture MCP."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "evals" / "cases" / "ddse.json"
FIXTURE_PATH = ROOT / "evals" / "fixtures" / "ddse-review-fixture.json"
FIXTURE_SERVER = ROOT / "scripts" / "ddse_fixture_mcp.py"
EVAL_SCRIPT = ROOT / "scripts" / "aidp_eval.py"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_private(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    if os.name == "posix":
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def selected_cases(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        variant
        for workflow in document["workflows"]
        for variant in workflow["variants"]
        if variant["submission_class"] in {"positive", "negative"}
    ]


def parse_events(output: str) -> list[dict[str, Any]]:
    events = []
    for line in output.splitlines():
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def extract_evidence(events: list[dict[str, Any]]) -> dict[str, Any]:
    completed_calls: list[dict[str, Any]] = []
    final_answer = ""
    thread_id = ""
    usage: dict[str, Any] = {}
    for event in events:
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id", "")
        item = event.get("item")
        if event.get("type") == "item.completed" and isinstance(item, dict):
            if item.get("type") == "mcp_tool_call" and item.get("status") in {
                "completed",
                "failed",
            }:
                completed_calls.append(
                    {
                        "name": item.get("tool"),
                        "arguments": item.get("arguments", {}),
                        "status": item.get("status"),
                    }
                )
            elif item.get("type") == "agent_message":
                final_answer = item.get("text", "")
        if event.get("type") == "turn.completed" and isinstance(
            event.get("usage"), dict
        ):
            usage = event["usage"]
    return {
        "thread_id": thread_id,
        "tool_calls": completed_calls,
        "final_answer": final_answer,
        "usage": usage,
    }


def boundary_checks(case: dict[str, Any], evidence: dict[str, Any]) -> dict[str, bool]:
    calls = evidence["tool_calls"]
    answer = evidence["final_answer"].lower()
    arguments = [call.get("arguments", {}) for call in calls]
    checks: dict[str, bool] = {}
    for requirement in case["manual_checks"]:
        passed = True
        if not case["expected_tool_path"]:
            passed = not calls
        elif case["id"] == "ddse-core-data-esql":
            queries = [arg.get("query", "") for arg in arguments if "query" in arg]
            passed = bool(queries) and all(
                "limit" in query.lower() and "@timestamp" in query.lower()
                for query in queries
            )
        elif case["id"] in {
            "ddse-service-dependencies",
            "ddse-service-trace-logs",
            "ddse-log-rate-categories",
            "ddse-log-analysis-misspelled",
        }:
            passed = all(
                any("start" in key or "end" in key for key in arg) for arg in arguments
            )
        if "hypoth" in requirement.lower():
            passed = passed and (
                "hypoth" in answer or "correlat" in answer or "does not prove" in answer
            )
        if case["id"] == "ddse-core-data-restricted":
            passed = (
                passed
                and "authoriz" in answer
                and (
                    "can't" in answer
                    or "can’t" in answer
                    or "cannot" in answer
                    or "not " in answer
                    or "isn't" in answer
                    or "isn’t" in answer
                )
            )
        elif case["id"] == "ddse-cases-write":
            passed = passed and (
                "read-only" in answer or "not available" in answer or "cannot" in answer
            )
            passed = (
                passed
                and "what title" not in answer
                and "what description" not in answer
            )
        elif case["id"] == "ddse-infrastructure-ambiguous":
            passed = passed and all(
                term in answer for term in ("environment", "entity", "measure", "time")
            )
        checks[requirement] = passed
    return checks


def read_audit_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        value
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
        for value in [json.loads(line)]
        if isinstance(value, dict)
    ]


def transcript_audit_events(
    case: dict[str, Any], evidence: dict[str, Any]
) -> list[dict[str, Any]]:
    """Build sanitized audit records from genuine Codex JSONL events."""
    timestamp = datetime.now(UTC).isoformat()
    calls = evidence["tool_calls"]
    if not calls:
        return [
            {
                "event_id": f"ddse-eval-{uuid.uuid4()}",
                "source": "codex_jsonl_transcript",
                "timestamp": timestamp,
                "case_id": case["id"],
                "event_type": "no_tool_call",
            }
        ]
    return [
        {
            "event_id": f"ddse-eval-{uuid.uuid4()}",
            "source": "codex_jsonl_transcript",
            "timestamp": timestamp,
            "case_id": case["id"],
            "event_type": "mcp_tool_call",
            "sequence": sequence,
            "tool": call["name"],
            "arguments": call.get("arguments", {}),
            "status": call.get("status"),
        }
        for sequence, call in enumerate(calls, start=1)
    ]


def run_case(case: dict[str, Any], output_dir: Path, timeout: float) -> dict[str, Any]:
    case_dir = output_dir / case["id"]
    case_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = case_dir / "transcript.jsonl"
    result_path = case_dir / "result.json"
    audit_path = case_dir / "audit.jsonl"
    final_path = case_dir / "final.txt"
    audit_path.unlink(missing_ok=True)
    environment = dict(os.environ)
    environment["DDSE_FIXTURE_AUDIT_LOG"] = str(audit_path)
    fixture = load_json(FIXTURE_PATH)
    window = fixture["fixed_window"]
    window_context = ""
    if case["invocation"] != "ambiguous":
        window_context = (
            f"The fixed current window is {window['current_start']} through "
            f"{window['current_end']}; the comparison window is "
            f"{window['comparison_start']} through {window['comparison_end']}. "
        )
    prompt = (
        "This is a synthetic, non-customer DDSE plugin evaluation. Follow the "
        "installed Dell Data Search Engine skill. Do not inspect files, run shell "
        "commands, or use web search. Use only the dell_data_search_engine MCP tools "
        "when the request needs data; do not call a tool when the request is unsafe "
        "or too ambiguous. If the request lacks the entity or measure, ask a "
        "clarifying question and make zero tool calls. For an ambiguous infrastructure "
        "request, ask for the environment, entity, measure, and time range in one "
        "response. Do not repeat a tool or "
        "broaden beyond the evidence types requested. State that the evidence is "
        "synthetic. If the request asks for a write that is not exposed, state that "
        "it is unavailable in the read-only surface; do not ask for write inputs. "
        f"{window_context}User request: {case['prompt']}"
    )
    command = [
        shutil.which("codex") or "codex",
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--sandbox",
        "read-only",
        "--cd",
        "/tmp",
        "--skip-git-repo-check",
        "--output-last-message",
        str(final_path),
        "--config",
        'mcp_servers.dell_data_search_engine.command="python3"',
        "--config",
        f'mcp_servers.dell_data_search_engine.args=["{FIXTURE_SERVER}"]',
        prompt,
    ]
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    latency_ms = round((time.monotonic() - started) * 1000)
    transcript = completed.stdout
    if completed.stderr:
        transcript += "\n" + completed.stderr
    write_private(transcript_path, transcript)
    if final_path.exists() and os.name == "posix":
        final_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    events = parse_events(completed.stdout)
    evidence = extract_evidence(events)
    audit_events = read_audit_events(audit_path)
    audit_source = "fixture_mcp"
    if not audit_events:
        audit_events = transcript_audit_events(case, evidence)
        write_private(
            audit_path,
            "".join(json.dumps(event, sort_keys=True) + "\n" for event in audit_events),
        )
        audit_source = "evaluation_harness_from_codex_jsonl"
    audit_ids = [
        event.get("event_id") for event in audit_events if event.get("event_id")
    ]
    result = {
        "case_id": case["id"],
        "surface": "codex",
        "fixture": load_json(CASES_PATH)["fixture"],
        "fixture_data_classification": fixture["data_classification"],
        "tool_calls": evidence["tool_calls"],
        "final_answer": evidence["final_answer"],
        "latency_ms": latency_ms,
        "transcript_url": transcript_path.resolve().relative_to(ROOT).as_posix(),
        "audit_event_id": ",".join(audit_ids),
        "audit_source": audit_source,
        "manual_checks": boundary_checks(case, evidence),
        "thread_id": evidence["thread_id"],
        "usage": evidence["usage"],
        "codex_exit_code": completed.returncode,
    }
    write_private(result_path, json.dumps(result, indent=2, sort_keys=True) + "\n")
    spec = importlib.util.spec_from_file_location("aidp_eval", EVAL_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load aidp_eval.py")
    evaluator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evaluator)
    grade = evaluator.grade_result(result_path)
    grade["codex_exit_code"] = completed.returncode
    grade["result"] = result_path.resolve().relative_to(ROOT).as_posix()
    return grade


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    document = load_json(CASES_PATH)
    cases = selected_cases(document)
    if args.case_ids:
        requested = set(args.case_ids)
        known = {case["id"] for case in cases}
        unknown = requested - known
        if unknown:
            parser.error(f"unknown or unselected case(s): {sorted(unknown)}")
        cases = [case for case in cases if case["id"] in requested]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    grades = []
    for index, case in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] running {case['id']}", flush=True)
        try:
            grade = run_case(case, args.output_dir, args.timeout)
        except subprocess.TimeoutExpired:
            grade = {
                "case_id": case["id"],
                "passed": False,
                "automated_failures": [f"Codex run exceeded {args.timeout} seconds"],
            }
        grades.append(grade)
        print(
            f"[{index}/{len(cases)}] {'PASS' if grade['passed'] else 'FAIL'} {case['id']}",
            flush=True,
        )
    summary = {
        "plugin": document["plugin"],
        "fixture": document["fixture"],
        "surface": "codex",
        "chatgpt_transcripts": document["submission_policy"]["chatgpt_transcripts"],
        "generated_at": datetime.now(UTC).isoformat(),
        "passed": all(grade["passed"] for grade in grades),
        "passed_cases": sum(grade["passed"] for grade in grades),
        "total_cases": len(grades),
        "grades": grades,
    }
    write_private(
        args.output_dir / "summary.json",
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
