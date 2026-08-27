"""Golden intent -> tool-call traces for `doe-eval` (plan §4 Phase 5).

Reuses the same three `docs/guides/*.md` workflows already measured for tokens/turns in
`scripts/measure_workflow_cost.py` and already wrapped as MCP Prompts in `doe_mcp.prompts` — this
is the third leg of that stool, not a fourth set of scenarios. `intent` is imported directly from
`doe_mcp.prompts` so there is exactly one copy of each guide's opening prompt.

`required_tools` is **derived from, not equal to**, the happy-path sequence
`measure_workflow_cost.py`'s `WORKFLOWS` drive. Every difference is commented at the trace below;
an earlier version of this docstring claimed the two were the same, which was never true and made
later prunes look like they had a provenance they did not have. The exact tuples are pinned by
`tests/test_eval_harness.py`, so changing a scoring criterion is a deliberate, reviewed diff
rather than something that can be quietly relaxed after seeing a run fail.

**Pass@1 definition** (see `harness._score`) — a trace passes when all four hold:

1. every name in `required_tools` was called, in that relative order, **counting repeats** (a
   name listed four times needs four calls); other calls in between are fine,
2. nothing in `forbidden_tools` was called at all,
3. no tool call returned an error,
4. `outcome_check` accepts the recorded results, where the trace defines one.

(1) is deliberately looser than exact-match: grading turn-for-turn adherence to the guide's prose
would grade conversational style, not task completion, and the guides invite branching. (4) exists
because (1)-(3) together still only measure choreography — a run once scored PASS on six correctly
ordered, error-free calls while the underlying question went unanswered (`docs/eval.md`). An
outcome check is what distinguishes "called the right tools" from "did the job".
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..prompts import BUILD_RAG_PIPELINE_PROMPT, EXPLORE_DATASET_PROMPT, FIND_AND_HANDLE_PII_PROMPT

if TYPE_CHECKING:
    from .harness import ToolCall

#: Tools that create, mutate, deploy or bill. Both human-gated intents forbid every one of these
#: before the operator replies "approved", so a trace scoring only the pre-approval leg lists them
#: as forbidden — that is the single most important thing those two guides exist to enforce, and
#: no amount of correct tool *ordering* can detect an agent that ignores it.
BILLABLE_OR_MUTATING = (
    "annotation_create",
    "annotation_delete",
    "annotation_delete_by_model",
    "app_uninstall",
    "dataset_create",
    "dataset_delete",
    "dataset_embed",
    "item_upload",
    "model_create",
    "model_delete",
    "model_deploy",
    "model_install",
    "model_predict",
    "model_train",
    "pipeline_create",
    "pipeline_create_from_template",
    "pipeline_delete",
    "pipeline_install",
    "rag_pipeline_create",
    "trigger_create",
    "trigger_delete",
)


@dataclass(frozen=True)
class GoldenTrace:
    id: str
    guide: str
    intent: str
    required_tools: tuple[str, ...]
    max_turns: int
    #: Tools that must not be called at all for this trace to pass.
    forbidden_tools: tuple[str, ...] = ()
    #: Tools that must not be called *before* the operator's first scripted approval. For a trace
    #: that is meant to build something, "never" is wrong but "not until I say so" is exactly the
    #: gate the guide teaches -- and ordering alone cannot express it, because the required tools
    #: are the very ones being gated.
    forbidden_before_approval: tuple[str, ...] = ()
    #: Scripted operator replies, injected as user turns when the model stops before
    #: `required_tools` are satisfied. Two of the three intents explicitly instruct the agent to
    #: present a plan and wait for approval, so without these `run_trace` scores an agent FAIL for
    #: obeying its own instructions and PASS for ignoring the gate.
    operator_replies: tuple[str, ...] = ()
    #: Checks what the run actually achieved, from the recorded tool results. Returns an empty
    #: string when satisfied, or a short reason why not.
    outcome_check: Callable[[Sequence[ToolCall]], str] | None = field(default=None, compare=False)


def _last_payload(calls: Sequence[ToolCall], name: str) -> object:
    for call in reversed(calls):
        if call.name == name and not call.is_error:
            return call.payload
    return None


def _explored_a_dataset(calls: Sequence[ToolCall]) -> str:
    """The point of the guide is a profile of a real dataset, not a tour of the tool surface."""
    profile = _last_payload(calls, "dataset_profile")
    if not isinstance(profile, dict):
        return "dataset_profile never returned a payload"
    if not profile.get("by_mimetype") and not profile.get("by_folder"):
        return "dataset_profile came back empty: nothing was actually profiled"
    return ""


def _reported_on_pii(calls: Sequence[ToolCall]) -> str:
    """A report that never verified the marker convention is the guide's documented false negative."""
    report = _last_payload(calls, "pii_report")
    if not isinstance(report, dict):
        return "pii_report never returned a payload"
    if _last_payload(calls, "item_get") is None:
        return "no item_get: the marker convention was never verified against a known-PII item"
    return ""


def _built_a_working_rag_graph(calls: Sequence[ToolCall]) -> str:
    """Five nodes and one edge is not a RAG pipeline; the graph has to be fully wired."""
    edges = sum(1 for call in calls if call.name == "pipeline_connect_nodes" and not call.is_error)
    if edges < 4:
        return f"only {edges} of the 4 edges a five-node RAG graph needs were connected"
    installed = _last_payload(calls, "pipeline_install")
    if not isinstance(installed, dict):
        return "pipeline_install never returned a payload"
    return ""


DATASET_EXPLORATION = GoldenTrace(
    id="dataset-exploration",
    guide="docs/guides/dataset-exploration-workflow.md",
    intent=EXPLORE_DATASET_PROMPT,
    # Differs from the measured sequence by dropping dataset_get: dataset_list's items already
    # carry the full serialize.dataset shape (id, name, items_count, ...) that the guide's Phase 1
    # needs to "confirm the choice by name and id", so a dataset_get straight afterwards re-fetches
    # data already in hand. Note the narrow scope of that claim -- dataset_list accepts a `fields`
    # projection and pages, so the two are *not* interchangeable in general.
    required_tools=("project_list", "dataset_list", "dataset_profile"),
    # The intent says "Do not run any model over the data unless I ask", and this leg never asks.
    forbidden_tools=BILLABLE_OR_MUTATING,
    # Guide's Phases 1-3 read-mostly path is 4 turns (measure_workflow_cost.py); headroom for a
    # model that double-checks something before profiling.
    max_turns=8,
    outcome_check=_explored_a_dataset,
)

PII_DETECTION = GoldenTrace(
    id="pii-detection",
    guide="docs/guides/pii-detection-workflow.md",
    intent=FIND_AND_HANDLE_PII_PROMPT,
    # Differs from the measured sequence by dropping pipeline_list: the measurement lists pipelines
    # during Phase 0 discovery, but the guide only requires it when the operator asks what already
    # exists, and this leg's scored question is "does the dataset contain PII".
    required_tools=(
        "project_list",
        "dataset_list",
        "marketplace_search",
        "item_list",
        "item_get",
        "pii_report",
    ),
    # Phase 0 is planning-only until the operator replies "approved"; this trace scores only that
    # read-only leg, so any write at all is a failure.
    forbidden_tools=BILLABLE_OR_MUTATING,
    operator_replies=(
        "Use the first project and dataset you listed. The item to verify the marker convention "
        "against is the first one item_list returns. Continue with the read-only investigation "
        "only -- do not create, install or run anything.",
    ),
    # Measured happy path is 7 turns; the guide's Phase 0 planning gate adds a round-trip a
    # scripted measurement skips, so the cap allows for that.
    max_turns=14,
    outcome_check=_reported_on_pii,
)

AGENTIC_RAG = GoldenTrace(
    id="agentic-rag",
    guide="docs/guides/agentic-rag-workflow.md",
    intent=BUILD_RAG_PIPELINE_PROMPT,
    # Differs from the measured sequence by dropping app_list, dataset_get and pipeline_stats --
    # all discovery or reporting steps the guide treats as optional. pipeline_connect_nodes is
    # listed four times because a five-node chain needs four edges, and the subsequence match
    # counts repeats: listing it once let a graph with one edge of four score a pass.
    required_tools=(
        "project_list",
        "dataset_list",
        "marketplace_search",
        "model_list",
        "rag_pipeline_create",
        "rag_add_pdf_filter_node",
        "rag_add_pdf_to_text_node",
        "rag_add_text_to_chunks_node",
        "rag_add_embedding_node",
        "rag_add_output_node",
        "pipeline_connect_nodes",
        "pipeline_connect_nodes",
        "pipeline_connect_nodes",
        "pipeline_connect_nodes",
        "pipeline_install",
    ),
    # This trace is *meant* to build, so nothing is forbidden outright -- but not before the
    # operator approves. That gate is the guide's Phase 0 and the whole point of the intent's
    # "do not create, update, delete, install or deploy anything until I reply approved".
    forbidden_before_approval=BILLABLE_OR_MUTATING,
    operator_replies=(
        "Approved -- build it with the direct rag_* tools. Use the first project and dataset you "
        "listed, and the first deployed model you found for embedding.",
    ),
    # Measured happy path is 18 turns; generous headroom for the guide's planning gate and a model
    # that inspects the graph before installing.
    max_turns=28,
    outcome_check=_built_a_working_rag_graph,
)

GOLDEN_TRACES: tuple[GoldenTrace, ...] = (DATASET_EXPLORATION, PII_DETECTION, AGENTIC_RAG)


def criteria_baseline() -> dict[str, Any]:
    """Every trace's scoring criteria, as a deterministic, diffable snapshot.

    Committed to `tests/baselines/eval_criteria.json` and compared in CI. The point is not that a
    criterion can never change — it is that changing one shows up as a reviewable diff in a
    committed file, rather than as an edit to a tuple in a source file that nobody diffs against
    anything. `docs/eval.md` records why: a criterion was once relaxed after seeing a run fail, and
    the same transcript was then re-scored as a PASS, with nothing in CI to notice.

    `intent` is deliberately excluded — it is prompt prose, owned by the guides, and would make
    this file churn on unrelated wording changes. Only the pass/fail levers are here.
    """
    return {
        "traces": [
            {
                "id": trace.id,
                "guide": trace.guide,
                "required_tools": list(trace.required_tools),
                "forbidden_tools": list(trace.forbidden_tools),
                "forbidden_before_approval": list(trace.forbidden_before_approval),
                "max_turns": trace.max_turns,
                "operator_replies": len(trace.operator_replies),
                "outcome_check": getattr(trace.outcome_check, "__name__", None),
            }
            for trace in GOLDEN_TRACES
        ]
    }
