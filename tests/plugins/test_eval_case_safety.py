# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for command and path handling in the Codex evaluation runners."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INVOCATIONS = frozenset({"explicit", "implicit", "ambiguous", "misspelled", "negative"})


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SAFETY = load("eval_case_safety")


def case(**changes):
    return {"id": "ddae-discover-explicit", "invocation": "explicit", "prompt": "Find data products.", **changes}


@pytest.mark.parametrize("case_id", ["../escape", "a/b", "..", "", "UPPER", "-leading", "x" * 81, 7])
def test_unsafe_case_ids_are_rejected(case_id):
    with pytest.raises(SAFETY.UnsafeCaseError):
        SAFETY.validate_case(case(id=case_id), INVOCATIONS)


@pytest.mark.parametrize("prompt", ["", "   ", "a\x00b", "a\x1b[2Jb", "x" * 4001, None])
def test_unsafe_prompts_are_rejected(prompt):
    with pytest.raises(SAFETY.UnsafeCaseError):
        SAFETY.validate_case(case(prompt=prompt), INVOCATIONS)


def test_unknown_invocation_is_rejected():
    with pytest.raises(SAFETY.UnsafeCaseError):
        SAFETY.validate_case(case(invocation="shell"), INVOCATIONS)


def test_case_directory_stays_inside_the_output_directory(tmp_path):
    assert SAFETY.case_directory(tmp_path, "ddae-case-1") == (tmp_path / "ddae-case-1").resolve()
    with pytest.raises(SAFETY.UnsafeCaseError):
        SAFETY.case_directory(tmp_path, "../outside")


def test_codex_must_resolve_to_an_absolute_executable(monkeypatch):
    monkeypatch.setattr(SAFETY.shutil, "which", lambda name: None)
    with pytest.raises(FileNotFoundError):
        SAFETY.resolve_codex()


@pytest.mark.parametrize("runner_name", ["run_ddae_codex_evals", "run_ddse_codex_evals"])
def test_prompt_is_sent_on_stdin_and_never_in_argv(runner_name, tmp_path, monkeypatch):
    runner = load(runner_name)
    hostile = "--dangerously-bypass-approvals-and-sandbox; rm -rf /"
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        raise subprocess.TimeoutExpired(command, 0)

    monkeypatch.setattr(runner.SAFETY, "resolve_codex", lambda: "/usr/bin/codex")
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    selected = runner.selected_cases(runner.load_json(runner.CASES_PATH))[0]
    with pytest.raises(subprocess.TimeoutExpired):
        runner.run_case({**selected, "prompt": hostile}, tmp_path, timeout=1)

    command = captured["command"]
    assert command[0] == "/usr/bin/codex"
    assert command[-1] == "-"
    assert all(hostile not in part for part in command)
    assert hostile in captured["kwargs"]["input"]
    assert captured["kwargs"].get("shell", False) is False
