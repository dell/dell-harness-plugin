# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""doe-eval harness (plan §4 Phase 5): scoring logic, decoupled from any real LLM or platform.

`ToolRunner` is a `Protocol` (see `harness.py`'s docstring), so this exercises `run_trace`'s
turn loop and Pass@1 scoring with scripted fakes — no `mcp.Client`, no live DOE, no network,
and no async test plugin (this project has none installed; `run()` below drives the coroutine
from a synchronous test the same way `tests/conftest.py` and `tests/test_prompts.py` do). The
`GOLDEN_TRACES` sanity checks at the bottom are the only place this touches the real prompt text.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from doe_mcp.eval import (
    GOLDEN_TRACES,
    GoldenTrace,
    HarnessError,
    ModelNotConfiguredError,
    ModelTurn,
    StopAction,
    TokenUsage,
    ToolCall,
    ToolCallAction,
    classify_error,
    criteria_baseline,
    load_model,
    run_trace,
    score_transcript,
)


def run(coro):
    return asyncio.run(coro)


@dataclass
class FakeListToolsResult:
    tools: list[dict[str, Any]]


@dataclass
class FakeCallToolResult:
    is_error: bool = False
    structured_content: dict[str, Any] | None = None
    content: Any = None


class FakeToolRunner:
    """Records every call and answers from a canned `{tool_name: result}` map."""

    def __init__(self, results: dict[str, FakeCallToolResult]) -> None:
        self._results = results
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> FakeListToolsResult:
        return FakeListToolsResult(tools=[{"name": name} for name in self._results])

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> FakeCallToolResult:
        self.calls.append((name, arguments))
        return self._results.get(name, FakeCallToolResult(structured_content={}))


class ScriptedModel:
    """Plays back a fixed list of `ModelTurn`s, one per `act()` call."""

    def __init__(self, turns: list[ModelTurn]) -> None:
        self._turns = list(turns)

    async def act(self, system_prompt, tools, history) -> ModelTurn:
        return self._turns.pop(0)


def _trace(**overrides: Any) -> GoldenTrace:
    defaults: dict[str, Any] = dict(
        id="t",
        guide="docs/guides/example.md",
        intent="do the thing",
        required_tools=("a", "b"),
        max_turns=5,
    )
    defaults.update(overrides)
    return GoldenTrace(**defaults)


def _tool_turn(name: str, arguments: dict[str, Any] | None = None) -> ModelTurn:
    return ModelTurn(action=ToolCallAction(tool_name=name, arguments=arguments or {}))


def _stop_turn(text: str = "done") -> ModelTurn:
    return ModelTurn(action=StopAction(text=text))


def test_passes_when_required_tools_are_called_in_order():
    trace = _trace(required_tools=("a", "b"))
    model = ScriptedModel([_tool_turn("a"), _tool_turn("b"), _stop_turn()])
    runner = FakeToolRunner({"a": FakeCallToolResult(), "b": FakeCallToolResult()})

    result = run(run_trace(trace, model, runner))

    assert result.passed
    assert result.tool_calls == ["a", "b"]
    assert result.turns_used == 3
    assert result.sdk_errors == 0
    assert result.failure_reason is None


def test_extra_calls_between_required_tools_still_pass():
    trace = _trace(required_tools=("a", "b"))
    model = ScriptedModel([_tool_turn("a"), _tool_turn("extra_read"), _tool_turn("b"), _stop_turn()])
    runner = FakeToolRunner(
        {"a": FakeCallToolResult(), "extra_read": FakeCallToolResult(), "b": FakeCallToolResult()}
    )

    result = run(run_trace(trace, model, runner))

    assert result.passed
    assert result.tool_calls == ["a", "extra_read", "b"]


def test_fails_when_required_tools_are_out_of_order():
    trace = _trace(required_tools=("a", "b"))
    model = ScriptedModel([_tool_turn("b"), _tool_turn("a"), _stop_turn()])
    runner = FakeToolRunner({"a": FakeCallToolResult(), "b": FakeCallToolResult()})

    result = run(run_trace(trace, model, runner))

    assert not result.passed
    assert "not all called, in order" in result.failure_reason


def test_fails_when_a_required_tool_is_never_called():
    trace = _trace(required_tools=("a", "b"))
    model = ScriptedModel([_tool_turn("a"), _stop_turn()])
    runner = FakeToolRunner({"a": FakeCallToolResult()})

    result = run(run_trace(trace, model, runner))

    assert not result.passed
    assert result.tool_calls == ["a"]


def test_an_sdk_error_fails_the_trace_and_is_counted():
    trace = _trace(required_tools=("a", "b"))
    model = ScriptedModel([_tool_turn("a"), _tool_turn("b"), _stop_turn()])
    runner = FakeToolRunner({"a": FakeCallToolResult(is_error=True), "b": FakeCallToolResult()})

    result = run(run_trace(trace, model, runner))

    assert not result.passed
    assert result.sdk_errors == 1
    assert result.sdk_error_rate == 0.5
    assert "returned an error" in result.failure_reason


def test_hitting_the_turn_cap_without_stopping_fails():
    trace = _trace(required_tools=("a",), max_turns=2)
    model = ScriptedModel([_tool_turn("a"), _tool_turn("a")])
    runner = FakeToolRunner({"a": FakeCallToolResult()})

    result = run(run_trace(trace, model, runner))

    assert not result.passed
    assert result.turns_used == 2
    assert "turn cap" in result.failure_reason


def test_stopping_immediately_with_no_required_tools_passes():
    trace = _trace(required_tools=())
    model = ScriptedModel([_stop_turn()])
    runner = FakeToolRunner({})

    result = run(run_trace(trace, model, runner))

    assert result.passed
    assert result.turns_used == 1
    assert result.tool_calls == []


def test_token_usage_accumulates_across_turns():
    trace = _trace(required_tools=("a",))
    model = ScriptedModel(
        [
            ModelTurn(action=ToolCallAction("a", {}), usage=TokenUsage(10, 5)),
            ModelTurn(action=StopAction(), usage=TokenUsage(3, 7)),
        ]
    )
    runner = FakeToolRunner({"a": FakeCallToolResult()})

    result = run(run_trace(trace, model, runner))

    assert result.usage.total == 25


def test_structured_content_none_falls_back_to_content_text():
    trace = _trace(required_tools=("a",))

    @dataclass
    class TextBlock:
        text: str

    runner = FakeToolRunner(
        {"a": FakeCallToolResult(structured_content=None, content=[TextBlock(text="hello")])}
    )
    seen: list[list[dict[str, Any]]] = []

    class Recording(ScriptedModel):
        async def act(self, system_prompt, tools, history):
            seen.append([dict(entry) for entry in history])
            return await super().act(system_prompt, tools, history)

    result = run(run_trace(trace, Recording([_tool_turn("a"), _stop_turn()]), runner))

    assert result.passed
    # The point of the fallback is that the text actually reaches the model and the record.
    # Asserting only `result.passed` passed identically if _result_payload returned "" or None.
    tool_entries = [entry for entry in seen[-1] if entry.get("role") == "tool"]
    assert tool_entries[-1]["content"] == "hello"


def test_score_transcript_passes_a_finished_ordered_call_list():
    trace = _trace(required_tools=("a", "b"))

    result = score_transcript(trace, ["a", "extra_read", "b"])

    assert result.passed
    assert result.turns_used == 3
    assert result.tool_calls == ["a", "extra_read", "b"]
    assert result.usage.total == 0


def test_score_transcript_fails_on_reported_sdk_errors():
    trace = _trace(required_tools=("a", "b"))

    result = score_transcript(trace, ["a", "b"], sdk_errors=1)

    assert not result.passed
    assert result.sdk_errors == 1
    assert "returned an error" in result.failure_reason


def test_score_transcript_preserves_arguments_when_reconciling_sdk_errors():
    seen_arguments: list[Any] = []

    def _check(calls):
        seen_arguments.append(calls[0].arguments)
        return ""

    trace = _trace(required_tools=("a",), outcome_check=_check)
    calls = [ToolCall(name="a", arguments={"project_id": "proj-1"})]

    score_transcript(trace, calls, sdk_errors=1)

    assert seen_arguments == [{"project_id": "proj-1"}]


def test_score_transcript_fails_when_required_tools_are_missing():
    trace = _trace(required_tools=("a", "b"))

    result = score_transcript(trace, ["a"])

    assert not result.passed
    assert "not all called, in order" in result.failure_reason


def test_score_transcript_accepts_explicit_turns_used_and_usage():
    trace = _trace(required_tools=("a",))

    result = score_transcript(trace, ["a"], turns_used=5, usage=TokenUsage(100, 50))

    assert result.turns_used == 5
    assert result.usage.total == 150


def test_load_model_rejects_a_path_with_no_colon():
    with pytest.raises(ModelNotConfiguredError, match="package.module:ClassName"):
        load_model("not.a.valid.path")


def test_load_model_rejects_an_unimportable_module():
    with pytest.raises(ModelNotConfiguredError, match="Could not import"):
        load_model("no_such_package_at_all:Thing")


def test_load_model_rejects_a_missing_attribute():
    with pytest.raises(ModelNotConfiguredError, match="no attribute"):
        load_model("tests.test_eval_harness:NoSuchClass")


class ScriptedModelFactory(ScriptedModel):
    """A zero-argument-constructible `EvalModel`, for `load_model`'s happy path test."""

    def __init__(self) -> None:
        super().__init__([])


def test_load_model_loads_and_instantiates_a_real_class():
    model = load_model("tests.test_eval_harness:ScriptedModelFactory")
    assert isinstance(model, ScriptedModelFactory)


def test_golden_traces_are_exactly_the_three_documented_workflows():
    assert {trace.id for trace in GOLDEN_TRACES} == {
        "dataset-exploration",
        "pii-detection",
        "agentic-rag",
    }


@pytest.mark.parametrize("trace", GOLDEN_TRACES, ids=lambda t: t.id)
def test_every_golden_trace_has_a_nonempty_intent_and_required_tools(trace: GoldenTrace):
    assert len(trace.intent) > 100
    assert len(trace.required_tools) > 0
    assert trace.max_turns >= len(trace.required_tools)


# --- the scoring criteria themselves, pinned -------------------------------------------


BASELINE_PATH = Path(__file__).parent / "baselines" / "eval_criteria.json"


def test_golden_trace_criteria_match_the_committed_baseline():
    """Any change to a scoring criterion must show up as a diff in a committed file.

    Not "criteria can never change" -- they should, when there is a reason. The point is that
    relaxing one becomes a reviewable line in `tests/baselines/eval_criteria.json` instead of an
    edit to a tuple that nothing diffs against anything. `docs/eval.md` records the incident: a
    criterion was relaxed after seeing a run fail, the same transcript was re-scored as a PASS,
    and nothing in CI noticed.

    Regenerate deliberately, and explain the diff in the commit:
        python scripts/doe_eval.py --dump-criteria tests/baselines/eval_criteria.json
    """
    committed = json.loads(BASELINE_PATH.read_text())

    assert criteria_baseline() == committed, (
        "Golden-trace scoring criteria differ from the committed baseline. If this change is "
        "intended, regenerate with `python scripts/doe_eval.py --dump-criteria "
        "tests/baselines/eval_criteria.json` and justify the diff in the commit message."
    )


def test_criteria_baseline_excludes_prompt_prose():
    """The baseline must stay stable against guide wording edits, or it will be regenerated
    reflexively and stop being read."""
    committed = json.loads(BASELINE_PATH.read_text())

    assert all("intent" not in trace for trace in committed["traces"])


@pytest.mark.parametrize("trace", GOLDEN_TRACES, ids=lambda t: t.id)
def test_trace_criteria_stay_meaningful(trace):
    """Invariants a baseline regeneration cannot quietly wash away.

    The committed baseline above catches *visible* weakening. These catch the shape of weakening
    that a reviewer skimming a regenerated JSON might wave through: a trace trimmed to a token
    gesture, or a gated trace losing the forbidden-tool list that encodes its whole point.
    """
    assert len(trace.required_tools) >= 3, "a trace this thin cannot distinguish success from noise"
    assert trace.max_turns >= len(trace.required_tools)
    assert trace.outcome_check is not None, "ordering alone measures choreography, not the job"
    if trace.operator_replies:
        assert trace.forbidden_tools or trace.forbidden_before_approval, (
            "a trace with a scripted approval is modelling a human-gated intent, so it must forbid "
            "the operations that gate exists to hold back -- outright for a read-only leg, or "
            "before the approval for a leg that is meant to build once approved"
        )


def test_every_required_and_forbidden_tool_exists_on_the_server():
    """A renamed or typo'd tool would otherwise make a trace permanently unpassable, silently."""
    from doe_mcp.server import build_server
    from tests.conftest import run as run_coro

    async def _names():
        from mcp import Client

        async with Client(build_server()) as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    available = run_coro(_names())
    referenced = {
        name
        for trace in GOLDEN_TRACES
        for name in (trace.required_tools + trace.forbidden_tools + trace.forbidden_before_approval)
    }

    assert referenced <= available, f"traces reference tools that do not exist: {referenced - available}"


# --- repeats in required_tools are significant -----------------------------------------


def test_a_repeated_required_tool_needs_that_many_calls():
    """agentic-rag needs four edges; listing connect once let a one-edge graph pass."""
    trace = _trace(required_tools=("connect", "connect"))

    assert not score_transcript(trace, ["connect"]).passed
    assert score_transcript(trace, ["connect", "connect"]).passed


# --- forbidden tools ---------------------------------------------------------------------


def test_calling_a_forbidden_tool_fails_the_trace():
    """The gated intents' central instruction is "create nothing until I approve"."""
    trace = _trace(required_tools=("a",), forbidden_tools=("pipeline_install",))

    result = score_transcript(trace, ["a", "pipeline_install"])

    assert not result.passed
    assert "forbidden tool" in result.failure_reason


# --- outcome checks ----------------------------------------------------------------------


def test_outcome_check_can_fail_a_run_that_called_every_right_tool():
    """Ordering plus zero errors is choreography; this is the check that it did the job."""
    trace = _trace(
        required_tools=("dataset_profile",),
        outcome_check=lambda calls: "profile was empty",
    )

    result = score_transcript(trace, [ToolCall(name="dataset_profile", payload={})])

    assert not result.passed
    assert "outcome check failed: profile was empty" in result.failure_reason


def test_names_only_transcript_cannot_pass_a_trace_with_an_outcome_check():
    """Scoring from tool names alone cannot verify a result, and must not pretend otherwise."""
    trace = _trace(required_tools=("dataset_profile",), outcome_check=lambda calls: "")

    result = score_transcript(trace, ["dataset_profile"])

    assert not result.passed
    assert "outcome check but was scored from tool names alone" in result.failure_reason


def test_dataset_exploration_outcome_check_rejects_an_empty_profile():
    trace = next(t for t in GOLDEN_TRACES if t.id == "dataset-exploration")

    empty = trace.outcome_check([ToolCall(name="dataset_profile", payload={"by_mimetype": {}})])
    real = trace.outcome_check([ToolCall(name="dataset_profile", payload={"by_mimetype": {"pdf": 3}})])

    assert empty
    assert real == ""


def test_agentic_rag_outcome_check_rejects_a_partially_wired_graph():
    trace = next(t for t in GOLDEN_TRACES if t.id == "agentic-rag")
    one_edge = [ToolCall(name="pipeline_connect_nodes")] + [ToolCall(name="pipeline_install", payload={})]
    four_edges = [ToolCall(name="pipeline_connect_nodes") for _ in range(4)]
    four_edges.append(ToolCall(name="pipeline_install", payload={}))

    assert "only 1 of the 4 edges" in trace.outcome_check(one_edge)
    assert trace.outcome_check(four_edges) == ""


# --- operator replies --------------------------------------------------------------------


def test_a_model_that_waits_for_approval_is_not_penalised():
    """Two of the three intents say "present a plan, then stop and wait".

    Without a scripted operator turn, `run_trace` scored that FAIL and scored an agent that
    ignored the gate PASS -- inverting the confirmation discipline the guides exist to teach.
    """
    trace = _trace(
        required_tools=("a",),
        operator_replies=("approved, go ahead",),
    )
    model = ScriptedModel([_stop_turn("here is my plan"), _tool_turn("a"), _stop_turn("done")])
    runner = FakeToolRunner({"a": FakeCallToolResult()})

    result = run(run_trace(trace, model, runner))

    assert result.passed
    assert result.tool_calls == ["a"]
    assert result.final_text == "done"


def test_operator_replies_are_not_infinite():
    """Once the scripted replies run out, the next stop really does end the trace."""
    trace = _trace(required_tools=("a",), operator_replies=("go on",))
    model = ScriptedModel([_stop_turn("plan"), _stop_turn("still nothing")])
    runner = FakeToolRunner({"a": FakeCallToolResult()})

    result = run(run_trace(trace, model, runner))

    assert not result.passed
    assert result.turns_used == 2


# --- error classification ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool_name", "message", "expected"),
    [
        ("nope", "Unknown tool: nope", "unknown_tool"),
        ("dataset_delete", "Refusing to delete: set confirm=true", "refused"),
        ("dataset_list", "validation error for dataset_list", "invalid_arguments"),
        ("dataset_list", "DOE request failed: 500", "platform"),
    ],
)
def test_errors_are_classified_by_kind(tool_name, message, expected):
    """One "SDK error rate" cannot answer Phase 6's gate; the kinds have to be separable."""
    assert classify_error(tool_name, message, ["dataset_delete", "dataset_list"]) == expected


def test_error_kinds_are_reported_per_trace():
    trace = _trace(required_tools=("a",))
    model = ScriptedModel([_tool_turn("a"), _stop_turn()])
    runner = FakeToolRunner(
        {"a": FakeCallToolResult(is_error=True, content=[SimpleText("Refusing to run: set confirm=true")])}
    )

    result = run(run_trace(trace, model, runner))

    assert result.error_kinds["refused"] == 1
    assert result.error_kinds["platform"] == 0
    assert "1 refused" in result.failure_reason


# --- infrastructure failures are not agent failures ---------------------------------------


def test_a_crashing_model_raises_harness_error_not_a_failed_trace():
    """ "The provider 529'd" and "the agent got worse" must not look the same to CI."""

    class Exploding:
        async def act(self, system_prompt, tools, history):
            raise TimeoutError("provider timed out")

    runner = FakeToolRunner({"a": FakeCallToolResult()})

    with pytest.raises(HarnessError, match="model.act failed"):
        run(run_trace(_trace(), Exploding(), runner))


def test_a_model_returning_a_bad_action_raises_rather_than_asserting():
    """`assert isinstance(...)` on adapter-supplied input vanishes under python -O."""

    class Confused:
        async def act(self, system_prompt, tools, history):
            return ModelTurn(action="not an action")

    with pytest.raises(HarnessError, match="must return a ToolCallAction or StopAction"):
        run(run_trace(_trace(), Confused(), FakeToolRunner({})))


# --- turn cap parity between the two scoring paths ----------------------------------------


def test_score_transcript_can_enforce_the_same_turn_cap_as_run_trace():
    """The one rule the two paths differ on, made explicit instead of silently divergent."""
    trace = _trace(required_tools=("a",), max_turns=2)

    assert score_transcript(trace, ["a"], turns_used=45).passed
    capped = score_transcript(trace, ["a"], turns_used=45, enforce_turn_cap=True)
    assert not capped.passed
    assert "ran past the 2-turn cap" in capped.failure_reason


def test_result_serialises_with_the_criteria_it_was_scored_against():
    """A persisted run has to record the goalposts, not just the score."""
    trace = _trace(required_tools=("a",))

    data = score_transcript(trace, ["a"]).to_dict()

    assert data["scored_against"]["required_tools"] == ["a"]
    assert data["scored_against"]["max_turns"] == 5
    assert data["usage"] == {"input": 0, "output": 0}
    assert data["sdk_error_rate"] == 0.0


class SimpleText:
    def __init__(self, text: str) -> None:
        self.text = text


class NotAModel:
    """No `act` method: the shape a typo'd or half-written adapter actually has."""


def test_load_model_rejects_a_class_that_is_not_an_eval_model():
    """Conformance has to fail at load time, before the harness touches a live platform.

    `EvalModel` is a plain Protocol, so nothing checks it structurally. Without this, a missing or
    misspelled `act` surfaced as an AttributeError deep inside the first trace -- by which point
    the run had connected to DOE and possibly created billable resources.
    """
    with pytest.raises(ModelNotConfiguredError, match="does not implement EvalModel"):
        load_model(f"{__name__}:NotAModel")


def test_building_before_the_operator_approves_fails_the_trace():
    """`agentic-rag` is meant to build -- but not until the operator says so.

    Ordering alone cannot express this: the gated tools *are* the required tools, so a run that
    creates the pipeline immediately satisfies `required_tools` perfectly. Only the position of
    those calls relative to the approval distinguishes obeying the gate from ignoring it.
    """
    trace = _trace(
        required_tools=("pipeline_create",),
        forbidden_before_approval=("pipeline_create",),
        operator_replies=("approved",),
    )
    runner = FakeToolRunner({"pipeline_create": FakeCallToolResult()})

    # Extra trailing stop: the first stop consumes the scripted approval, so the model gets asked
    # for one more turn afterwards.
    jumped_the_gun = ScriptedModel([_tool_turn("pipeline_create"), _stop_turn(), _stop_turn()])
    waited = ScriptedModel([_stop_turn("here is my plan"), _tool_turn("pipeline_create"), _stop_turn()])

    early = run(run_trace(trace, jumped_the_gun, runner))
    proper = run(run_trace(trace, waited, runner))

    assert not early.passed
    assert "before the operator approved" in early.failure_reason
    assert proper.passed, "an agent that waited for approval then built must pass"


def test_never_pausing_for_approval_counts_as_never_approved():
    """An agent that never stops for the gate has not cleared it.

    The tempting implementation treats "no approval recorded" as "no cutoff, so nothing was early",
    which would credit the least compliant behaviour with a clean pass. Here the model never
    returns StopAction at all -- it burns the turn cap building -- so `approved_after` stays None
    and the whole run must count as pre-approval.
    """
    trace = _trace(
        required_tools=("pipeline_create",),
        forbidden_before_approval=("pipeline_create",),
        operator_replies=("approved",),
        max_turns=2,
    )
    runner = FakeToolRunner({"pipeline_create": FakeCallToolResult()})
    never_pauses = ScriptedModel([_tool_turn("pipeline_create"), _tool_turn("pipeline_create")])

    result = run(run_trace(trace, never_pauses, runner))

    assert not result.passed
    assert "before the operator approved" in result.failure_reason
