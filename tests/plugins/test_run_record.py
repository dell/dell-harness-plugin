# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("record_run_metadata", ROOT / "scripts/record_run_metadata.py")
rec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rec)

ARGS = [
    "--plugin", "dell-data-search-engine", "--profile", "n/a", "--client", "codex=0.0.0",
    "--model", "m", "--effort", "medium", "--fixture", "synthetic-v1", "--identity", "read-only-role",
]


def test_package_hash_is_stable_and_detects_changes(tmp_path):
    (tmp_path / "a.txt").write_text("one")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.pyc").write_bytes(b"ignored")
    first, count = rec.package_hash(tmp_path)
    assert count == 1 and rec.package_hash(tmp_path)[0] == first
    (tmp_path / "a.txt").write_text("two")
    assert rec.package_hash(tmp_path)[0] != first


def test_record_has_required_fields_and_no_local_paths(tmp_path):
    out = tmp_path / "run.json"
    assert rec.main([*ARGS, "--out", str(out)]) == 0
    record = json.loads(out.read_text())
    for key in ("package_sha256", "plugin_version", "git", "runtime", "effective_profile", "model",
                "effort", "fixture", "identity", "limits"):
        assert key in record
    assert len(record["package_sha256"]) == 64
    assert str(ROOT) not in out.read_text() and str(tmp_path) not in out.read_text()


def test_tool_catalog_summary_hashes_and_lists_names(tmp_path):
    capture = tmp_path / "tools.json"
    capture.write_text(json.dumps({"tools": [{"name": "b"}, {"name": "a"}]}))
    summary = rec.catalog_summary(capture)
    assert summary["names"] == ["a", "b"] and summary["count"] == 2 and len(summary["sha256"]) == 64


@pytest.mark.parametrize(
    "result,fragment",
    [
        ({"case_id": "c", "run_status": "completed", "quality": "pass", "evidence": "row count 3"}, None),
        ({"case_id": "c", "run_status": "timeout", "quality": "pass", "evidence": "x"}, "cannot pass"),
        ({"case_id": "c", "run_status": "completed", "quality": "pass", "evidence": ""}, "needs evidence"),
        ({"case_id": "c", "run_status": "done", "quality": "pass", "evidence": "x"}, "run_status"),
        ({"case_id": "c", "run_status": "timeout", "quality": "not_graded", "evidence": ""}, None),
    ],
)
def test_case_results_keep_run_status_apart_from_quality(result, fragment):
    problems = rec.validate_case_result(result)
    if fragment is None:
        assert problems == []
    else:
        assert any(fragment in p for p in problems)
