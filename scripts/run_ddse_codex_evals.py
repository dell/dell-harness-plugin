#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Capture genuine Codex JSONL evidence against the synthetic DDSE fixture MCP."""

from __future__ import annotations

import importlib.util
import subprocess  # noqa: F401  (re-exported so tests can patch subprocess.run)
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("codex_eval_runner", ROOT / "scripts" / "codex_eval_runner.py")
assert _spec is not None and _spec.loader is not None
shared = sys.modules.setdefault("codex_eval_runner", importlib.util.module_from_spec(_spec))
if not hasattr(shared, "PluginEval"):
    _spec.loader.exec_module(shared)

CASES_PATH = ROOT / "evals" / "cases" / "ddse.json"
FIXTURE_PATH = ROOT / "evals" / "fixtures" / "ddse-review-fixture.json"
FIXTURE_SERVER = ROOT / "scripts" / "ddse_fixture_mcp.py"
SAFETY = shared.SAFETY
load_json = shared.load_json
selected_cases = shared.selected_cases
parse_events = shared.parse_events
extract_evidence = shared.extract_evidence

SERVER = "dell_data_search_engine"
WRITE_TOOLS = {"platform_core_create_case"}
INDEX = "synthetic-orders-v1"
WINDOWED = {
    "ddse-service-dependencies",
    "ddse-service-trace-logs",
    "ddse-log-rate-categories",
    "ddse-log-analysis-misspelled",
}


def _window_respected(arguments: dict[str, Any], window: dict[str, str]) -> bool:
    bounds = {key: value for key, value in arguments.items() if "start" in key or "end" in key}
    allowed = set(window.values())
    return bool(bounds) and all(str(value) in allowed for value in bounds.values())


def _reported(result: dict[str, Any] | None, answer: str) -> bool:
    """The answer restates the values the tool actually returned (counts, ids, categories)."""
    if not isinstance(result, dict):
        return False
    values = []
    for key, value in result.items():
        if key in {"fixture_id", "audit_event_id", "window", "columns"}:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(str(value))
        elif isinstance(value, list):
            values.extend(str(v) for row in value for v in (row if isinstance(row, list) else [row])
                          if isinstance(v, (str, int)) and not isinstance(v, bool))
    return bool(values) and all(value.lower() in answer for value in values if value)


def boundary_checks(case: dict[str, Any], evidence: dict[str, Any]) -> dict[str, bool]:
    """Every manual check is derived from the trace and the returned results, never assumed."""
    calls = evidence["tool_calls"]
    answer = evidence["final_answer"].lower()
    window = load_json(FIXTURE_PATH)["fixed_window"]
    arguments = [call.get("arguments", {}) for call in calls]
    no_writes = not any(call.get("name") in WRITE_TOOLS for call in calls)
    intended_endpoint = all(call.get("server", SERVER) == SERVER for call in calls)
    checks: dict[str, bool] = {}
    for requirement in case["manual_checks"]:
        passed = no_writes and intended_endpoint
        if not case["expected_tool_path"]:
            passed = passed and not calls
        elif case["id"] == "ddse-core-data-esql":
            esql = [call for call in calls if call.get("name") == "platform_core_execute_esql"]
            queries = [call.get("arguments", {}).get("query", "") for call in esql]
            passed = passed and bool(queries) and all(
                INDEX in query
                and "limit" in query.lower()
                and "@timestamp" in query
                and window["current_start"] in query
                and window["current_end"] in query
                for query in queries
            ) and all(_reported(call.get("result"), answer) for call in esql)
        elif case["id"] == "ddse-core-data-document":
            passed = passed and all(arg.get("index") == INDEX for arg in arguments) and all(
                (call.get("result") or {}).get("found") is True for call in calls
            )
        elif case["id"] in WINDOWED:
            passed = passed and all(_window_respected(arg, window) for arg in arguments)
        if "hypoth" in requirement.lower():
            passed = passed and (
                "hypoth" in answer or "correlat" in answer or "does not prove" in answer
            )
        if case["id"] == "ddse-core-data-restricted":
            passed = (
                passed
                and "authoriz" in answer
                and any(term in answer for term in ("can't", "can\u2019t", "cannot", "not ", "isn't", "isn\u2019t"))
            )
        elif case["id"] == "ddse-cases-write":
            passed = (
                passed
                and ("read-only" in answer or "not available" in answer or "cannot" in answer)
                and "what title" not in answer
                and "what description" not in answer
            )
        elif case["id"] == "ddse-infrastructure-ambiguous":
            passed = passed and all(term in answer for term in ("environment", "entity", "measure", "time"))
        checks[requirement] = passed
    return checks


def prompt(case: dict[str, Any], fixture: dict[str, Any]) -> str:
    """A normal user request: the case prompt alone, with no harness instructions."""
    return case["prompt"]


PLUGIN = shared.PluginEval(
    slug="ddse",
    plugin="dell-data-search-engine",
    cases_path=CASES_PATH,
    fixture_path=FIXTURE_PATH,
    fixture_server=FIXTURE_SERVER,
    server_name="dell_data_search_engine",
    audit_env="DDSE_FIXTURE_AUDIT_LOG",
    prompt=prompt,
    boundary_checks=boundary_checks,
)


def transcript_audit_events(case: dict[str, Any], evidence: dict[str, Any]) -> list[dict[str, Any]]:
    return shared.transcript_audit_events(PLUGIN.slug, case, evidence)


def run_case(case: dict[str, Any], output_dir: Path, timeout: float) -> dict[str, Any]:
    return shared.run_case(PLUGIN, case, output_dir, timeout)


if __name__ == "__main__":
    raise SystemExit(shared.main(PLUGIN, __doc__))
