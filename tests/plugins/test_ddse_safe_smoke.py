# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import os
import secrets
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_ddse_safe_smoke.py"


def load_module():
    spec = importlib.util.spec_from_file_location("run_ddse_safe_smoke", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_tool_result_summary_omits_raw_content():
    module = load_module()
    # Random per run: no credential-shaped literal in source, and no coincidental match.
    secret_value = secrets.token_urlsafe(16)
    body = {
        "result": {
            "content": [{"type": "text", "text": secret_value}],
            "structuredContent": {"value": secret_value},
        }
    }

    summary = module.summarize_tool_result("case", "tool", body, 12)

    assert summary == {
        "case_id": "case",
        "tool": "tool",
        "outcome": "success",
        "latency_ms": 12,
        "content_items": 1,
        "content_types": ["text"],
        "structured_content_present": True,
    }
    assert secret_value not in json.dumps(summary)


def test_tool_error_is_preserved_without_error_text():
    module = load_module()
    body = {
        "result": {
            "isError": True,
            "content": [{"type": "text", "text": "sensitive backend error"}],
        }
    }

    summary = module.summarize_tool_result("negative", "tool", body, 3)

    assert summary["outcome"] == "tool_error"
    assert "sensitive backend error" not in json.dumps(summary)


def test_report_writer_uses_private_permissions(tmp_path):
    module = load_module()
    path = tmp_path / "smoke.json"
    report = {"engine": "ddse", "checks": []}

    module.write_report(path, report)

    assert json.loads(path.read_text(encoding="utf-8")) == report
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_script_contains_only_synthetic_or_index_free_live_cases():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "openai-review-no-match-20260910-*" in text
    assert "openai-review-nonexistent-20260910" in text
    assert "ROW validation_value = 1 | LIMIT 1" in text
    assert "tools/call" in text
    assert "--authorization" not in text
    assert "--token" not in text
