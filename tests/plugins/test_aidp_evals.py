# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EVAL_SCRIPT = ROOT / "scripts/aidp_eval.py"


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_eval_definitions_validate_and_cover_every_ddse_tool():
    completed = subprocess.run(
        [sys.executable, str(EVAL_SCRIPT), "validate"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    summary = json.loads(completed.stdout)
    assert summary["plugins"]["dell-data-analytics-engine"]["declared_tools"] == 2
    assert summary["plugins"]["dell-data-search-engine"]["declared_tools"] == 25

    ddse = load_json(ROOT / "evals/cases/ddse.json")
    covered = {
        tool
        for workflow in ddse["workflows"]
        for tool in workflow["covered_tools"]
    }
    expected_paths = {
        tool
        for workflow in ddse["workflows"]
        for variant in workflow["variants"]
        if variant["kind"] in {"success", "edge"}
        for tool in variant["expected_tool_path"]
    }
    assert covered == set(ddse["declared_tools"])
    assert expected_paths == set(ddse["declared_tools"])


def test_eval_grader_accepts_complete_result_and_rejects_wrong_path(tmp_path):
    passing = {
        "case_id": "ddae-discover-explicit",
        "surface": "codex",
        "fixture": "ddae-synthetic-commerce-v1",
        "tool_calls": [
            {"name": "searchDataProducts", "arguments": {"query": "fulfillment"}}
        ],
        "final_answer": "fulfillment_metrics has ID dp_fulfillment_v1.",
        "latency_ms": 100,
        "transcript_url": "private-transcript-ref",
        "audit_event_id": "audit-ref",
        "manual_checks": {"safety": True, "grounding": True},
    }
    path = tmp_path / "result.json"
    path.write_text(json.dumps(passing), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(EVAL_SCRIPT), "grade", str(path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout)["passed"] is True

    passing["tool_calls"] = []
    path.write_text(json.dumps(passing), encoding="utf-8")
    failed = subprocess.run(
        [sys.executable, str(EVAL_SCRIPT), "grade", str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert failed.returncode == 1
    assert json.loads(failed.stdout)["passed"] is False


def test_contract_checker_detects_vendor_tool_drift(tmp_path):
    document = load_json(ROOT / "evals/cases/ddae.json")
    snapshot = {
        "tools": [
            {
                "name": name,
                "inputSchema": {"type": "object"},
                "outputSchema": {"type": "object"},
                "annotations": {
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    "openWorldHint": False,
                },
            }
            for name in document["declared_tools"]
        ]
    }
    for tool in snapshot["tools"]:
        if tool["name"] == "queryReadOnly":
            tool["inputSchema"] = {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            }
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(EVAL_SCRIPT),
            "contract",
            "dell-data-analytics-engine",
            str(path),
            "--review-ready",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout)["passed"] is True

    snapshot["tools"].pop()
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    failed = subprocess.run(
        [
            sys.executable,
            str(EVAL_SCRIPT),
            "contract",
            "dell-data-analytics-engine",
            str(path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert failed.returncode == 1
    assert "missing tools" in failed.stdout


def test_outcome_skills_are_present():
    skills = [
        ROOT
        / "dell-data-analytics-engine/skills/analyze-governed-data-products/SKILL.md",
        ROOT
        / "dell-data-search-engine/skills/investigate-dell-search-data/SKILL.md",
    ]
    for path in skills:
        text = path.read_text(encoding="utf-8")
        assert "[TODO:" not in text
        assert "## Preserve boundaries" in text
        assert "## Format the answer" in text
