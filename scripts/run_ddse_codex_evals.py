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


def prompt(case: dict[str, Any], fixture: dict[str, Any]) -> str:
    window = fixture["fixed_window"]
    window_context = ""
    if case["invocation"] != "ambiguous":
        window_context = (
            f"The fixed current window is {window['current_start']} through "
            f"{window['current_end']}; the comparison window is "
            f"{window['comparison_start']} through {window['comparison_end']}. "
        )
    return (
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


PLUGIN = shared.PluginEval(
    slug="ddse",
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
