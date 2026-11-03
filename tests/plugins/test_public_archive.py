# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for validation of the exact public source archive."""
from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/validate_public_archive.py"
SPEC = importlib.util.spec_from_file_location("validate_public_archive", SCRIPT)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def archive(tmp_path: Path, files: dict[str, str]) -> Path:
    path = tmp_path / "source.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
        for name, content in files.items():
            package.writestr(f"dell-harness-plugin/{name}", content)
    return path


def test_valid_archive_passes(tmp_path):
    path = archive(tmp_path, {
        "README.md": "public package\n",
        "plugin.json": '{"license": "Apache-2.0"}',
        "script.py": "print('ok')\n",
        "config.toml": 'name = "example"\n',
    })
    assert VALIDATOR.validate_archive(path) == []


@pytest.mark.parametrize(("name", "content", "expected"), [
    (".devin/mcp_config.local.json", "{}", "local or generated file"),
    ("README.md", "https://service." + "lab9" + "bgp.example", "private infrastructure hostname"),
    ("plugin.json", '{"license": "Proprietary"}', "proprietary plugin license"),
    ("broken.py", "def invalid(:\n", "invalid .py file"),
])
def test_unsafe_archive_content_is_rejected(tmp_path, name, content, expected):
    problems = VALIDATOR.validate_archive(archive(tmp_path, {name: content}))
    assert any(expected in problem for problem in problems)


def test_multiple_top_level_directories_are_rejected(tmp_path):
    path = tmp_path / "source.zip"
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("one/README.md", "one")
        package.writestr("two/README.md", "two")
    assert "archive must contain exactly one top-level directory" in VALIDATOR.validate_archive(path)
