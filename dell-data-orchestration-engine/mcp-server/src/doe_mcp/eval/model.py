# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pluggable model interface for the doe-eval harness (plan §4 Phase 5).

`EvalModel` is deliberately provider-agnostic: this module ships no LLM client library and no API
key handling. The plan's Phase 5 explicitly deferred choosing a provider (Anthropic, OpenAI, ...)
rather than committing to one without a funded, credentialed run to justify it — see the plan's
Phase 6 gate, which applies the same discipline to SLM choices. `load_model` instead imports
whatever `EvalModel` implementation the caller names, so this package adds zero supply-chain
surface until someone actually runs an eval. See `docs/eval.md` for a worked example adapter.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolCallAction:
    """The model wants to call one tool before continuing."""

    tool_name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class StopAction:
    """The model considers the intent satisfied (or is giving up) and the trace ends here."""

    text: str = ""


Action = ToolCallAction | StopAction


@dataclass(frozen=True)
class TokenUsage:
    """Token counts for one `EvalModel.act` call. Zero fields for a provider that reports none.

    Frozen because it is used as a `field(default_factory=...)` on `TraceResult`: `__add__`
    rebinding rather than mutating is load-bearing, and freezing makes that structural rather
    than a convention someone could quietly break.
    """

    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens)

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class ModelTurn:
    """One `EvalModel.act` response: the action to take, and what it cost."""

    action: Action
    usage: TokenUsage = field(default_factory=TokenUsage)


class EvalModel(Protocol):
    """Drives one turn of the tool-use loop against a golden trace's intent.

    `history` is a provider-agnostic list of role-tagged dicts the harness appends to after every
    turn — `{"role": "user", "content": <str>}` for the opening intent, `{"role": "assistant",
    "tool_call": {"name": ..., "arguments": {...}}}` for a call the model made, and `{"role":
    "tool", "tool_name": ..., "content": ..., "is_error": bool}` for that call's result.
    Implementations translate this into whatever shape their own provider's chat/tool-use API
    expects; `tools` is the live `list_tools()` result (dicts, not the model's own schema dialect)
    so an adapter can translate that too.
    """

    async def act(
        self, system_prompt: str, tools: list[dict[str, Any]], history: list[dict[str, Any]]
    ) -> ModelTurn: ...


class ModelNotConfiguredError(RuntimeError):
    """Raised instead of silently no-op'ing when no real `EvalModel` is wired up.

    Mirrors `scripts/live_smoke.py`'s posture on a missing `DATALOOP_API_KEY`: fail closed with a
    message naming exactly what is missing, not a stack trace three layers into a provider SDK.
    """


def load_model(dotted_path: str) -> EvalModel:
    """Import `"package.module:ClassName"` and instantiate it with no arguments.

    Deliberately dynamic rather than a fixed provider registry: see this module's docstring for
    why doe-eval ships no built-in Anthropic/OpenAI/... choice to pick from.
    """
    if ":" not in dotted_path:
        raise ModelNotConfiguredError(
            f"--model must be 'package.module:ClassName', got {dotted_path!r}. doe-eval ships no "
            "built-in provider; point this at your own EvalModel implementation (see docs/eval.md)."
        )
    module_name, _, class_name = dotted_path.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ModelNotConfiguredError(f"Could not import {module_name!r}: {exc}") from exc
    try:
        model_class = getattr(module, class_name)
    except AttributeError as exc:
        raise ModelNotConfiguredError(f"{module_name!r} has no attribute {class_name!r}") from exc
    try:
        model = model_class()
    except Exception as exc:
        raise ModelNotConfiguredError(
            f"{dotted_path} could not be constructed with no arguments: {exc}"
        ) from exc
    # Check conformance now, not three layers into the first trace. `EvalModel` is a plain
    # Protocol so nothing verifies it structurally, and by the time `run_trace` reached a typo'd
    # method name the harness would already have connected to a live platform and possibly
    # created billable resources.
    if not callable(getattr(model, "act", None)):
        raise ModelNotConfiguredError(
            f"{dotted_path} does not implement EvalModel: no callable 'act' method. It needs "
            "'async def act(self, system_prompt, tools, history) -> ModelTurn' (see docs/eval.md)."
        )
    return model  # type: ignore[no-any-return]
