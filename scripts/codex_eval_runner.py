#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared harness that captures genuine Codex JSONL evidence against a synthetic fixture MCP.

Plugin runners (`run_ddae_codex_evals.py`, `run_ddse_codex_evals.py`) supply a `PluginEval`
with their paths, MCP server name, prompt, and boundary checks.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import stat
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVAL_SCRIPT = ROOT / "scripts" / "aidp_eval.py"
SAFETY_SCRIPT = ROOT / "scripts" / "eval_case_safety.py"
INVOCATIONS = frozenset({"explicit", "implicit", "ambiguous", "misspelled", "negative"})


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SAFETY = _load("eval_case_safety", SAFETY_SCRIPT)


@dataclass(frozen=True)
class PluginEval:
    slug: str
    cases_path: Path
    fixture_path: Path
    fixture_server: Path
    server_name: str
    audit_env: str
    prompt: Callable[[dict[str, Any], dict[str, Any]], str]
    boundary_checks: Callable[[dict[str, Any], dict[str, Any]], dict[str, bool]]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_private(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    if os.name == "posix":
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def selected_cases(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        SAFETY.validate_case(variant, INVOCATIONS)
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
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            usage = event["usage"]
    return {
        "thread_id": thread_id,
        "tool_calls": completed_calls,
        "final_answer": final_answer,
        "usage": usage,
    }


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
    slug: str, case: dict[str, Any], evidence: dict[str, Any]
) -> list[dict[str, Any]]:
    """Build sanitized audit records from genuine Codex JSONL events."""
    timestamp = datetime.now(UTC).isoformat()
    calls = evidence["tool_calls"]
    if not calls:
        return [
            {
                "event_id": f"{slug}-eval-{uuid.uuid4()}",
                "source": "codex_jsonl_transcript",
                "timestamp": timestamp,
                "case_id": case["id"],
                "event_type": "no_tool_call",
            }
        ]
    return [
        {
            "event_id": f"{slug}-eval-{uuid.uuid4()}",
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


def run_case(
    plugin: PluginEval, case: dict[str, Any], output_dir: Path, timeout: float
) -> dict[str, Any]:
    SAFETY.validate_case(case, INVOCATIONS)
    case_dir = SAFETY.case_directory(output_dir, case["id"])
    case_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = case_dir / "transcript.jsonl"
    result_path = case_dir / "result.json"
    audit_path = case_dir / "audit.jsonl"
    final_path = case_dir / "final.txt"
    audit_path.unlink(missing_ok=True)
    environment = dict(os.environ)
    environment[plugin.audit_env] = str(audit_path)
    fixture = load_json(plugin.fixture_path)
    prompt = plugin.prompt(case, fixture)
    # The prompt is sent on stdin (`-`), never as an argument, so case text cannot become a
    # Codex option. Every argv element below is a constant or a path this script controls.
    command = [
        SAFETY.resolve_codex(),
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
        f'mcp_servers.{plugin.server_name}.command="python3"',
        "--config",
        f'mcp_servers.{plugin.server_name}.args=["{plugin.fixture_server}"]',
        "-",
    ]
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        input=prompt,
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
    evidence = extract_evidence(parse_events(completed.stdout))
    audit_events = read_audit_events(audit_path)
    audit_source = "fixture_mcp"
    if not audit_events:
        audit_events = transcript_audit_events(plugin.slug, case, evidence)
        write_private(
            audit_path,
            "".join(json.dumps(event, sort_keys=True) + "\n" for event in audit_events),
        )
        audit_source = "evaluation_harness_from_codex_jsonl"
    audit_ids = [event.get("event_id") for event in audit_events if event.get("event_id")]
    result = {
        "case_id": case["id"],
        "surface": "codex",
        "fixture": load_json(plugin.cases_path)["fixture"],
        "fixture_data_classification": fixture["data_classification"],
        "tool_calls": evidence["tool_calls"],
        "final_answer": evidence["final_answer"],
        "latency_ms": latency_ms,
        "transcript_url": transcript_path.resolve().relative_to(ROOT).as_posix(),
        "audit_event_id": ",".join(audit_ids),
        "audit_source": audit_source,
        "manual_checks": plugin.boundary_checks(case, evidence),
        "thread_id": evidence["thread_id"],
        "usage": evidence["usage"],
        "codex_exit_code": completed.returncode,
    }
    write_private(result_path, json.dumps(result, indent=2, sort_keys=True) + "\n")
    grade = _load("aidp_eval", EVAL_SCRIPT).grade_result(result_path)
    grade["codex_exit_code"] = completed.returncode
    grade["result"] = result_path.resolve().relative_to(ROOT).as_posix()
    return grade


def main(plugin: PluginEval, description: str | None = None) -> int:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    document = load_json(plugin.cases_path)
    cases = selected_cases(document)
    if args.case_ids:
        requested = set(args.case_ids)
        unknown = requested - {case["id"] for case in cases}
        if unknown:
            parser.error(f"unknown or unselected case(s): {sorted(unknown)}")
        cases = [case for case in cases if case["id"] in requested]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    grades = []
    for index, case in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] running {case['id']}", flush=True)
        try:
            grade = run_case(plugin, case, args.output_dir, args.timeout)
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
