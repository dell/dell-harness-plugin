"""doe-eval: golden intent -> execution traces (plan §4 Phase 5).

Measures Pass@1, tool-call turns, and SDK error rate for a model driving `doe-mcp`'s real tools
against one of the three documented `docs/guides/*.md` workflows. Token usage is reported too,
when the connected `EvalModel` provides it.

This package ships **no LLM client library and no provider adapter** — see `model.py`'s
docstring for why — so nothing here can actually run until a caller supplies one via
`load_model("package.module:ClassName")`. `scripts/doe_eval.py` is the CLI entry point; see
`docs/eval.md` for how to write an adapter and run it against a live DOE project.
"""

from .harness import (
    ERROR_KINDS,
    HarnessError,
    ToolCall,
    ToolRunner,
    TraceResult,
    classify_error,
    run_trace,
    score_transcript,
)
from .model import (
    EvalModel,
    ModelNotConfiguredError,
    ModelTurn,
    StopAction,
    TokenUsage,
    ToolCallAction,
    load_model,
)
from .traces import GOLDEN_TRACES, GoldenTrace, criteria_baseline

__all__ = [
    "ERROR_KINDS",
    "GOLDEN_TRACES",
    "EvalModel",
    "GoldenTrace",
    "HarnessError",
    "ModelNotConfiguredError",
    "ModelTurn",
    "StopAction",
    "TokenUsage",
    "ToolCall",
    "ToolCallAction",
    "ToolRunner",
    "TraceResult",
    "classify_error",
    "criteria_baseline",
    "load_model",
    "run_trace",
    "score_transcript",
]
