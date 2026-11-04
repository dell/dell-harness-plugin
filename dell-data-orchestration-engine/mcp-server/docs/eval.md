# doe-eval

A small harness that drives a tool-use loop through one of the documented workflows in
[`guides/`](guides/) and scores it: pass or fail, turns, tool errors by kind, and token usage when
the model reports it. It evaluates the server profile the plugin ships, `plugin-v1`, unless told
otherwise.

Installed-plugin validation in Codex (fresh chats, fixed fixtures, retained transcripts) is a
separate layer, run from the plugin repository root; see its
[`docs/validation/VALIDATION_PLAN.md`](../../../docs/validation/VALIDATION_PLAN.md).

## What it measures

Each golden trace (`doe_mcp.eval.GOLDEN_TRACES`) pairs one guide's opening prompt with the tools
that workflow needs:

| Trace | Guide |
|---|---|
| `dataset-exploration` | [Exploring a dataset](guides/dataset-exploration-workflow.md) |
| `pii-detection` | [PII detection](guides/pii-detection-workflow.md) |
| `agentic-rag` | [Agentic RAG](guides/agentic-rag-workflow.md) |

A trace passes only if all of these hold:

- every one of its `required_tools` was called, in that relative order, with repeats counted
  (four `pipeline_connect_nodes` entries need four calls);
- no tool call returned an error;
- no `forbidden_tools` were called, and none of `forbidden_before_approval` ran before the operator
  approved;
- its outcome check passed, judged on the tool results rather than the call sequence (a non-empty
  profile, a PII marker verified against a real item, four wired edges and an install that returned
  a payload);
- its answer check passed, judged on the model's final answer, where the trace defines one;
- on the live path, the run finished within `max_turns`.

Extra calls in between are allowed, so an agent that double-checks is not penalised. A trace with
an outcome check cannot pass from tool names alone: pass `ToolCall` records carrying results.

Errors are classified as `unknown_tool`, `invalid_arguments`, `refused` (the server's confirmation
gate declining an unapproved call, which is correct behaviour) or `platform`.

## What it does not measure

- Pagination compliance. Arguments are recorded on each `ToolCall`, but no trace asserts on them.
- Answer quality beyond the trace's explicit checks. A pass says the workflow reached a verified
  outcome; it does not grade prose.
- Anything about the client: discovery, skill routing and approval prompts are covered by the
  installed-plugin validation instead.

## Writing an `EvalModel` adapter

The harness ships no model client and no provider choice. `doe_mcp.eval.EvalModel` is a
`Protocol` with one method, and `--model package.module:ClassName` imports whatever class
implements it. Without one the harness fails closed with `ModelNotConfiguredError`.

```python
# my_eval_adapter.py -- lives wherever you keep it, not in this package
from typing import Any

from doe_mcp.eval import ModelTurn, StopAction, TokenUsage, ToolCallAction


class MyModel:
    """Example shape only: wire your own provider SDK and message translation here."""

    def __init__(self) -> None:
        import my_provider_sdk  # your dependency, not this package's

        self._client = my_provider_sdk.Client()  # reads its own credentials from the environment

    async def act(
        self, system_prompt: str, tools: list[dict[str, Any]], history: list[dict[str, Any]]
    ) -> ModelTurn:
        response = await self._client.complete(system_prompt, tools, history)
        usage = TokenUsage(response.input_tokens, response.output_tokens)
        if response.tool_call:
            return ModelTurn(ToolCallAction(response.tool_call.name, response.tool_call.arguments), usage)
        return ModelTurn(StopAction(text=response.text), usage)
```

`load_model` calls the class with no arguments, so read credentials inside `__init__`.

## Scoring a finished transcript

Agents that run their own turn loop (an interactive client, a coding agent) cannot be driven one
turn at a time. Score the finished transcript instead:

```python
from doe_mcp.eval import GOLDEN_TRACES, ToolCall, score_transcript

trace = next(t for t in GOLDEN_TRACES if t.id == "dataset-exploration")
calls = [ToolCall(name="project_list", payload={...}), ...]  # results included
result = score_transcript(trace, tool_calls=calls, final_text="...")
print(result.passed, result.failure_reason)
```

`score_transcript` and `run_trace` share one scoring function. Two differences remain, both
explicit: the turn cap applies on the transcript path only with `enforce_turn_cap=True`, and
`turns_used` there counts tool calls, excluding the final stop turn, so turn counts are not
comparable across the two paths.

## Running it

Traces create real datasets and pipelines, so use a disposable project. Set credentials in the
environment before starting, never on the command line:

```bash
export DATALOOP_API_KEY=... DATALOOP_PATH=~/.doe-mcp/eval-state
python scripts/doe_eval.py --project-id <disposable-project> --model my_eval_adapter:MyModel
python scripts/doe_eval.py --project-id <disposable-project> --model my_eval_adapter:MyModel \
    --trace dataset-exploration --repeat 5 --out results.json
```

`--profile` defaults to `plugin-v1`. `--out` writes a JSON artifact with the git revision,
timestamp, model, and the exact criteria each result was scored against.

Billable operations are declined by default: the harness answers every confirmation prompt with
a refusal, so an unattended model cannot approve its own training or deployment. Pass
`--allow-billable` only for a project you are willing to lose.

Exit codes: `0` all passed, `1` an eval failed, `2` usage or configuration error, `3` the run itself
broke (a provider timeout, a crashed adapter), which says nothing about the agent.

## Scoring criteria are a reviewed baseline

`tests/baselines/eval_criteria.json` holds every trace's `required_tools`, `forbidden_tools`,
`forbidden_before_approval` and `max_turns`, and CI diffs the live criteria against it. Change a
criterion only deliberately, regenerate with
`python scripts/doe_eval.py --dump-criteria tests/baselines/eval_criteria.json`, and justify the
diff. Never regenerate it to turn a failing run green: relaxing a criterion after seeing a failure
and re-scoring the same transcript cannot fail by construction, so it is not evidence. A corrected
criterion needs a fresh run.

## Known platform behaviour the traces depend on

`dtlpy`'s `Pipelines.update` sends the whole node and connection list, not a diff, so a graph edit
built from a stale read can overwrite a node added a moment earlier. Every graph mutation in
`tools/pipeline_nodes.py` therefore re-reads the pipeline independently after writing and retries
until the change is visible; a test pins the whole-list PATCH so a future SDK change surfaces. This
narrows the window but cannot close it without a version precondition on the platform API, which
the SDK does not offer. `scripts/live_smoke.py` repeats an unpaced add, add, connect sequence and
counts nodes and edges afterwards on every live run.
