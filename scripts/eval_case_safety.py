# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Input validation shared by the Codex evaluation runners.

Evaluation cases are JSON files, so every value that reaches the filesystem or a
child process is validated here first. Prompts are never placed on a command
line: callers pass them to ``codex exec -`` on stdin, so no case text can be
parsed as an option or argument.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

CASE_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,79}")
MAX_PROMPT_CHARS = 4000
# Printable text plus ordinary whitespace; rejects NUL and terminal control sequences.
DISALLOWED_PROMPT_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class UnsafeCaseError(ValueError):
    """An evaluation case failed validation and must not be executed."""


def validate_case(case: Any, invocations: frozenset[str]) -> dict[str, Any]:
    """Return the case unchanged if every field the runner uses is well formed."""
    if not isinstance(case, dict):
        raise UnsafeCaseError("evaluation case must be an object")
    case_id = case.get("id")
    if not isinstance(case_id, str) or not CASE_ID.fullmatch(case_id):
        raise UnsafeCaseError(f"invalid case id {case_id!r}: use lowercase letters, digits, and hyphens")
    prompt = case.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise UnsafeCaseError(f"{case_id}: prompt must be a non-empty string")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise UnsafeCaseError(f"{case_id}: prompt exceeds {MAX_PROMPT_CHARS} characters")
    if DISALLOWED_PROMPT_CHARS.search(prompt):
        raise UnsafeCaseError(f"{case_id}: prompt contains control characters")
    if case.get("invocation") not in invocations:
        raise UnsafeCaseError(f"{case_id}: unknown invocation {case.get('invocation')!r}")
    return case


def case_directory(output_dir: Path, case_id: str) -> Path:
    """Resolve a per-case directory and refuse anything outside ``output_dir``."""
    if not CASE_ID.fullmatch(case_id):
        raise UnsafeCaseError(f"invalid case id {case_id!r}")
    root = output_dir.resolve()
    target = (root / case_id).resolve()
    if target.parent != root:
        raise UnsafeCaseError(f"case directory escapes the output directory: {case_id!r}")
    return target


def resolve_codex() -> str:
    """Absolute path of the Codex CLI; never fall back to an unresolved name."""
    found = shutil.which("codex")
    if not found:
        raise FileNotFoundError("codex CLI not found on PATH; install it before running evaluations")
    # Keep the PATH entry (an npm shim may be a symlink to a script), but make it absolute.
    path = os.path.abspath(found)
    if not os.path.isfile(path) or not os.access(path, os.X_OK):
        raise FileNotFoundError(f"codex CLI at {found!r} is not an executable file")
    return path
