"""Drives one golden trace's intent through a live tool-use loop and scores it.

Deliberately decoupled from `mcp.Client`: `ToolRunner` below is a `Protocol` matching just the two
calls this needs (`list_tools`, `call_tool`), so tests can exercise the scoring logic with a
scripted fake instead of a real MCP session (see `tests/test_eval_harness.py`), and
`scripts/doe_eval.py` can point it at either an in-process `mcp.Client(build_server())` against
mocked dtlpy or a real one against a live platform.

Scoring lives in one place (`_score`) and both entry points go through it, so `run_trace` and
`score_transcript` cannot drift apart on the rules. They differ on exactly one axis, the turn cap,
and that difference is now explicit (`enforce_turn_cap`) rather than silently baked into one path.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from .model import EvalModel, StopAction, TokenUsage, ToolCallAction
from .traces import GoldenTrace


class ToolCallResult(Protocol):
    """The subset of `mcp.types.CallToolResult` this module reads."""

    is_error: bool
    structured_content: dict[str, Any] | None
    content: Any


class ListToolsResult(Protocol):
    """The subset of `mcp.types.ListToolsResult` this module reads."""

    tools: list[Any]


class ToolRunner(Protocol):
    """The subset of `mcp.Client` a trace is driven through."""

    async def list_tools(self) -> ListToolsResult: ...
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolCallResult: ...


#: How a failed tool call failed. Kept separate because "SDK error rate" as a single number is
#: unreadable: a hallucinated tool name, the model getting an argument wrong, and the server's own
#: confirmation guard correctly refusing an unconfirmed billable op are three different signals,
#: and only `platform` says anything about *this server's* reliability. The plan's Phase 6 gate is
#: phrased as "model-side syntax errors, not schema size, dominate failures", which cannot be read
#: off a conflated count at all.
ERROR_KINDS = ("unknown_tool", "invalid_arguments", "refused", "platform")


@dataclass(frozen=True)
class ToolCall:
    """One recorded tool call: what was asked, and what came back."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    is_error: bool = False
    error_kind: str | None = None
    payload: Any = None


@dataclass
class TraceResult:
    trace_id: str
    passed: bool
    turns_used: int
    tool_calls: list[str] = field(default_factory=list)
    sdk_errors: int = 0
    usage: TokenUsage = field(default_factory=TokenUsage)
    failure_reason: str | None = None
    #: Per-kind breakdown of `sdk_errors`; see `ERROR_KINDS`.
    error_kinds: dict[str, int] = field(default_factory=dict)
    #: The model's closing message, when it produced one. Not scored — recorded so a human can
    #: audit what the agent actually claimed, which a tool-name list cannot show.
    final_text: str = ""
    #: The criteria this result was scored against, captured so a persisted run is auditable even
    #: after the trace definition changes.
    scored_against: dict[str, Any] = field(default_factory=dict)

    @property
    def sdk_error_rate(self) -> float:
        return self.sdk_errors / len(self.tool_calls) if self.tool_calls else 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["usage"] = {"input": self.usage.input_tokens, "output": self.usage.output_tokens}
        data["sdk_error_rate"] = round(self.sdk_error_rate, 4)
        return data


class HarnessError(RuntimeError):
    """The run itself broke — a provider timeout, a crashed adapter — as opposed to a trace failing.

    Kept distinct so a caller can tell "the agent got worse" from "the provider 529'd", which a
    bare non-zero exit cannot.
    """


def _is_ordered_subsequence(required: tuple[str, ...], actual: Sequence[str]) -> bool:
    """True if every name in `required` appears in `actual`, in order, **counting repeats**.

    Extra calls in between (reads, retries) are fine. Repeats are significant: a name listed four
    times needs four separate calls, which is how `agentic-rag` requires all four edges of its
    five-node graph rather than accepting any one of them.
    """
    it = iter(actual)
    # `name in it` on an iterator advances it past the first match, so requiring the same name
    # twice consumes two distinct elements — subsequence containment over a multiset.
    return all(name in it for name in required)


def _tool_def(tool: Any) -> dict[str, Any]:
    if hasattr(tool, "model_dump"):
        return tool.model_dump(exclude_none=True)
    return dict(tool)


def _result_payload(result: ToolCallResult) -> Any:
    if result.structured_content is not None:
        return result.structured_content
    blocks = getattr(result, "content", None) or []
    return "".join(getattr(block, "text", "") for block in blocks)


def classify_error(tool_name: str, payload: Any, known_tools: Sequence[str]) -> str:
    """Bucket a failed call into one of `ERROR_KINDS` from its message.

    Text matching, because MCP flattens every tool exception into a message string — there is no
    structured error code to read. Deliberately conservative: anything unrecognised is reported as
    `platform` rather than quietly attributed to the model.
    """
    if known_tools and tool_name not in known_tools:
        return "unknown_tool"
    text = payload if isinstance(payload, str) else str(payload)
    lowered = text.lower()
    if "set confirm" in lowered or "refusing to" in lowered or "requires confirmation" in lowered:
        return "refused"
    if "validation error" in lowered or "input should be" in lowered or "field required" in lowered:
        return "invalid_arguments"
    return "platform"


async def run_trace(
    trace: GoldenTrace,
    model: EvalModel,
    tool_runner: ToolRunner,
    system_prompt: str = "",
) -> TraceResult:
    """Run `trace.intent` through `model`'s tool-use loop against `tool_runner`, and score it.

    Stops when the model returns a `StopAction` with no scripted operator replies left, or after
    `trace.max_turns` turns — the latter always fails, since it means the model never finished.

    When the model stops early and the trace has `operator_replies` outstanding, the next reply is
    injected as a user turn and the loop continues. Without that, an agent obeying the two gated
    intents ("present a plan, then stop and wait") scores FAIL for doing as it was told, while one
    that ignores the gate and creates billable resources unattended scores PASS.
    """
    try:
        tools_result = await tool_runner.list_tools()
    except Exception as exc:  # noqa: BLE001 - re-raised as HarnessError below
        raise HarnessError(f"could not list tools: {exc}") from exc
    tool_defs = [_tool_def(tool) for tool in tools_result.tools]
    known_tools = [str(definition.get("name", "")) for definition in tool_defs]

    history: list[dict[str, Any]] = [{"role": "user", "content": trace.intent}]
    calls: list[ToolCall] = []
    usage = TokenUsage()
    replies = list(trace.operator_replies)
    final_text = ""
    approved_after: int | None = None

    for turn in range(trace.max_turns):
        try:
            model_turn = await model.act(system_prompt, tool_defs, history)
        except Exception as exc:  # noqa: BLE001 - infrastructure failure, not a trace failure
            raise HarnessError(f"{trace.id}: model.act failed on turn {turn + 1}: {exc}") from exc
        usage = usage + model_turn.usage
        action = model_turn.action

        if isinstance(action, StopAction):
            history.append({"role": "assistant", "content": action.text})
            final_text = action.text
            if replies:
                if approved_after is None:
                    # Everything called up to here happened before the operator said anything.
                    approved_after = len(calls)
                history.append({"role": "user", "content": replies.pop(0)})
                continue
            return _score(
                trace,
                turns_used=turn + 1,
                calls=calls,
                usage=usage,
                final_text=final_text,
                approved_after=approved_after,
            )

        if not isinstance(action, ToolCallAction):
            raise HarnessError(
                f"EvalModel.act must return a ToolCallAction or StopAction, got {type(action)!r}."
            )
        history.append(
            {"role": "assistant", "tool_call": {"name": action.tool_name, "arguments": action.arguments}}
        )
        try:
            result = await tool_runner.call_tool(action.tool_name, action.arguments)
        except Exception as exc:  # noqa: BLE001 - a client that raises rather than flagging is_error
            raise HarnessError(f"{trace.id}: call_tool({action.tool_name!r}) raised: {exc}") from exc
        payload = _result_payload(result)
        calls.append(
            ToolCall(
                name=action.tool_name,
                arguments=action.arguments,
                is_error=result.is_error,
                error_kind=classify_error(action.tool_name, payload, known_tools)
                if result.is_error
                else None,
                payload=payload,
            )
        )
        history.append(
            {
                "role": "tool",
                "tool_name": action.tool_name,
                "content": payload,
                "is_error": result.is_error,
            }
        )

    return _score(
        trace,
        turns_used=trace.max_turns,
        calls=calls,
        usage=usage,
        final_text=final_text,
        cap_hit=True,
        approved_after=approved_after,
    )


def score_transcript(
    trace: GoldenTrace,
    tool_calls: Sequence[str | ToolCall],
    *,
    sdk_errors: int | None = None,
    turns_used: int | None = None,
    usage: TokenUsage | None = None,
    enforce_turn_cap: bool = False,
) -> TraceResult:
    """Score an already-finished transcript against `trace`, instead of driving one.

    For a driver that runs the intent to completion on its own rather than exposing a per-turn
    `EvalModel.act()` hook — an interactive agent session with `doe-mcp` reachable as MCP is the
    motivating case.

    Pass `ToolCall` records where you can and bare tool names where you cannot. Names alone are
    enough for the ordering and forbidden-tool rules, but **not** for a trace with an
    `outcome_check`: there are no results to check, so the trace cannot pass, and the failure
    reason says so rather than quietly scoring a pass on choreography alone.

    `enforce_turn_cap` defaults off, because this path is usually handed a transcript from a driver
    whose "turns" are not comparable to `run_trace`'s. Turn it on to apply the same three-condition
    rule `run_trace` uses. Either way `turns_used` is reported.
    """
    calls = [ToolCall(name=call) if isinstance(call, str) else call for call in tool_calls]
    names_only = all(isinstance(call, str) for call in tool_calls) and bool(tool_calls)
    if sdk_errors is not None:
        # Caller counted errors themselves off a transcript that carries no per-call detail.
        for index in range(min(sdk_errors, len(calls))):
            calls[index] = ToolCall(
                name=calls[index].name, is_error=True, error_kind="platform", payload=calls[index].payload
            )
    resolved_turns = turns_used if turns_used is not None else len(calls)
    return _score(
        trace,
        turns_used=resolved_turns,
        calls=calls,
        usage=usage if usage is not None else TokenUsage(),
        cap_hit=enforce_turn_cap and resolved_turns > trace.max_turns,
        outcome_unverifiable=names_only and trace.outcome_check is not None,
    )


def _score(
    trace: GoldenTrace,
    *,
    turns_used: int,
    calls: Sequence[ToolCall],
    usage: TokenUsage,
    final_text: str = "",
    cap_hit: bool = False,
    outcome_unverifiable: bool = False,
    approved_after: int | None = None,
) -> TraceResult:
    names = [call.name for call in calls]
    error_kinds = {kind: 0 for kind in ERROR_KINDS}
    for call in calls:
        if call.is_error:
            error_kinds[call.error_kind or "platform"] += 1
    sdk_errors = sum(error_kinds.values())

    reasons = []
    if cap_hit:
        reasons.append(f"ran past the {trace.max_turns}-turn cap ({turns_used} turns)")
    if sdk_errors:
        breakdown = ", ".join(f"{count} {kind}" for kind, count in error_kinds.items() if count)
        reasons.append(f"{sdk_errors} of {len(names)} tool call(s) returned an error ({breakdown})")
    forbidden = sorted({name for name in names if name in trace.forbidden_tools})
    if forbidden:
        reasons.append(f"called forbidden tool(s) {forbidden} for this trace's read-only/gated leg")
    if trace.forbidden_before_approval:
        # None means the operator never spoke, so nothing was ever approved: the whole run counts
        # as "before approval". That is the right reading -- an agent that built the pipeline and
        # never paused for the gate should not be credited with having cleared it.
        cutoff = len(names) if approved_after is None else approved_after
        premature = sorted({name for name in names[:cutoff] if name in trace.forbidden_before_approval})
        if premature:
            reasons.append(
                f"called {premature} before the operator approved: this trace's intent gates every "
                "create/install/deploy behind an explicit approval"
            )
    if not _is_ordered_subsequence(trace.required_tools, names):
        reasons.append(f"required tools {list(trace.required_tools)} were not all called, in order")
    if outcome_unverifiable:
        reasons.append(
            "this trace has an outcome check but was scored from tool names alone; pass ToolCall "
            "records so the run's result can be verified, not just its tool sequence"
        )
    elif trace.outcome_check is not None:
        outcome = trace.outcome_check(calls)
        if outcome:
            reasons.append(f"outcome check failed: {outcome}")

    return TraceResult(
        trace_id=trace.id,
        passed=not reasons,
        turns_used=turns_used,
        tool_calls=names,
        sdk_errors=sdk_errors,
        usage=usage,
        failure_reason="; ".join(reasons) or None,
        error_kinds=error_kinds,
        final_text=final_text,
        scored_against={
            "required_tools": list(trace.required_tools),
            "forbidden_tools": list(trace.forbidden_tools),
            "max_turns": trace.max_turns,
            "outcome_check": getattr(trace.outcome_check, "__name__", None),
        },
    )
