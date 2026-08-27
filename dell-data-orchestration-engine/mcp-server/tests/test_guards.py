"""Confirmation gates: the `confirm=true` fallback, and elicitation for clients that support it.

Tool bodies are sync functions running in a worker thread (see server.py's module docstring), so
`confirm_or_elicit`'s elicitation path bridges back to the event loop with `anyio.from_thread.run`.
That bridge only works from inside a thread `anyio.to_thread.run_sync` spawned, so these tests
drive the function the same way production does: through `anyio.to_thread.run_sync`, not a bare
call.
"""

import asyncio
from types import SimpleNamespace

import anyio.to_thread
import pytest
from mcp.server.elicitation import AcceptedElicitation, CancelledElicitation, DeclinedElicitation

from doe_mcp import guards
from doe_mcp.guards import LOCAL_COMPUTE_VECTOR_BUDGET, gate_local_compute
from doe_mcp.server import build_server


def run_in_worker_thread(fn, *args):
    """Drive a sync function the way a tool body actually runs, so anyio.from_thread.run works."""
    return asyncio.run(anyio.to_thread.run_sync(fn, *args))


def fake_ctx(*, elicitation_declared: bool, response=None):
    """A minimal stand-in for mcp.server.mcpserver.Context, covering only what guards.py reads."""

    async def elicit(message, schema):
        return response

    capabilities = SimpleNamespace(elicitation=object()) if elicitation_declared else None
    return SimpleNamespace(client_capabilities=capabilities, elicit=elicit)


# --- require_confirmation (characterization: unchanged fallback behaviour) --------------------


def test_require_confirmation_passes_when_confirmed():
    guards.require_confirmation(True, "delete something", "it is gone for good")


def test_require_confirmation_refuses_when_not_confirmed():
    with pytest.raises(ValueError, match="Refusing to delete something"):
        guards.require_confirmation(False, "delete something", "it is gone for good")


# --- confirm_or_elicit -------------------------------------------------------------------------


def test_confirm_or_elicit_short_circuits_on_an_explicit_confirm():
    """confirm=true already means the human agreed upstream; no need to ask again."""
    guards.confirm_or_elicit(None, True, "delete something", "it is gone for good")


def test_confirm_or_elicit_falls_back_to_confirm_flag_without_a_context():
    with pytest.raises(ValueError, match="Refusing to delete something"):
        guards.confirm_or_elicit(None, False, "delete something", "it is gone for good")


def test_confirm_or_elicit_falls_back_when_client_has_no_elicitation_capability():
    ctx = fake_ctx(elicitation_declared=False)

    with pytest.raises(ValueError, match="Refusing to delete something"):
        guards.confirm_or_elicit(ctx, False, "delete something", "it is gone for good")


def test_confirm_or_elicit_proceeds_when_the_human_accepts():
    ctx = fake_ctx(
        elicitation_declared=True,
        response=AcceptedElicitation(data=guards.ConfirmationRequest(confirmed=True)),
    )

    run_in_worker_thread(guards.confirm_or_elicit, ctx, False, "delete something", "it is gone for good")


def test_confirm_or_elicit_refuses_when_the_human_declines_the_form():
    ctx = fake_ctx(
        elicitation_declared=True,
        response=AcceptedElicitation(data=guards.ConfirmationRequest(confirmed=False)),
    )

    with pytest.raises(ValueError, match="did not confirm"):
        run_in_worker_thread(guards.confirm_or_elicit, ctx, False, "delete something", "it is gone for good")


def test_confirm_or_elicit_refuses_when_the_client_declines_the_elicitation():
    ctx = fake_ctx(elicitation_declared=True, response=DeclinedElicitation())

    with pytest.raises(ValueError, match="did not confirm"):
        run_in_worker_thread(guards.confirm_or_elicit, ctx, False, "delete something", "it is gone for good")


def test_confirm_or_elicit_refuses_when_the_client_cancels_the_elicitation():
    ctx = fake_ctx(elicitation_declared=True, response=CancelledElicitation())

    with pytest.raises(ValueError, match="did not confirm"):
        run_in_worker_thread(guards.confirm_or_elicit, ctx, False, "delete something", "it is gone for good")


# --- local computation this server performs, and the limits on it ------------------------
#
# Three tools pull feature vectors out of DOE and compute over them in the `doe-mcp` process
# rather than on the platform: `dataset_cluster` (k-means, ADR-017), `dataset_visual_search` and
# `rag_query` (cosine similarity, because dtlpy has no vector-distance query). That is a reviewed
# exception to the project's "run computations on DOE" convention, and it is bounded.
#
# What is *not* covered here, and cannot be: computation the *agent* runs in its own sandbox
# between tool calls. An MCP server observes tool calls and nothing else. If an agent lists items
# and then runs its own code over them, nothing in this repo sees it, and no test can assert
# otherwise. The controls that exist are the bound below, the skill's declared tool contract, and
# after-the-fact detection in the eval harness -- see docs/eval.md.

#: Every tool that reads vectors off the platform and computes over them locally.
LOCAL_COMPUTE_TOOLS = ("dataset_cluster", "dataset_visual_search", "rag_query")


def tool_schemas():
    tools = asyncio.run(build_server().list_tools())
    return {tool.name: (tool.input_schema or {}).get("properties", {}) for tool in tools}


@pytest.mark.parametrize("name", LOCAL_COMPUTE_TOOLS)
def test_local_compute_stays_within_the_budget_by_schema(name):
    """The binding control is the schema cap, not the gate -- and it must stay that way.

    A hard `le=` an agent cannot exceed is stronger than a prompt it can click through, so while
    the caps sit at or below the budget the gate never fires and that is correct. This test is the
    tripwire: raising a cap past the budget is then a deliberate act, and whoever does it has to
    either bring the cap back down or accept that the tool now prompts a human mid-run.
    """
    schema = tool_schemas()[name]["max_vectors"]

    assert schema["maximum"] <= LOCAL_COMPUTE_VECTOR_BUDGET, (
        f"{name} now permits {schema['maximum']} vectors of local computation, above the "
        f"{LOCAL_COMPUTE_VECTOR_BUDGET} budget. Either lower the cap, or accept that this tool "
        "will elicit a human's approval on large runs (guards.gate_local_compute)."
    )


@pytest.mark.parametrize("name", LOCAL_COMPUTE_TOOLS)
def test_local_compute_tools_still_declare_a_bound(name):
    """A tool that lost its bound entirely would pass the cap check above vacuously."""
    assert "max_vectors" in tool_schemas()[name], f"{name} no longer bounds its local computation"


def test_the_gate_passes_anything_within_budget_without_a_client():
    """No prompt, no error, no ctx needed: the routine path must stay unprompted."""
    gate_local_compute(None, requested=LOCAL_COMPUTE_VECTOR_BUDGET, action="cluster", parameter="max_vectors")


def test_the_gate_refuses_an_over_budget_run_on_a_client_that_cannot_ask():
    """Failing closed, and saying exactly which knob to turn.

    A client with no elicitation support cannot put the question to a human, so the only honest
    answers are "refuse" or "silently proceed". It refuses, and names the parameter -- an agent
    that reads the message can lower the bound itself rather than guessing.
    """
    with pytest.raises(ValueError, match="Lower max_vectors to 2000 or less"):
        gate_local_compute(
            None,
            requested=LOCAL_COMPUTE_VECTOR_BUDGET + 1,
            action="cluster this dataset locally",
            parameter="max_vectors",
        )


def test_the_refusal_says_the_work_would_happen_off_platform():
    """The reason has to be legible: this is about *where* the computation runs, not its size."""
    with pytest.raises(ValueError, match="computes over them locally, in the doe-mcp process"):
        gate_local_compute(
            None, requested=99_999, action="cluster this dataset locally", parameter="max_vectors"
        )


# --- running a foreign model locally: elicitation or nothing -----------------------------


def test_elicit_or_refuse_has_no_confirm_escape_hatch():
    """A model must not be able to approve running a third-party model on its own say-so.

    `confirm_or_elicit` deliberately falls back to trusting `confirm=true` when the client cannot
    elicit -- right for a billable op the user asked for, wrong here, where whether a human agrees
    *is* the question. So there is no parameter to set: a client that cannot ask, refuses.
    """
    with pytest.raises(ValueError, match="Refusing to run CLIP locally"):
        guards.elicit_or_refuse(
            None,
            action="run CLIP locally",
            consequence="it downloads a model from HuggingFace",
            remedy="Pass query_vector instead.",
        )


def test_a_refusal_names_the_way_to_proceed_without_it():
    """A dead-end refusal just gets retried; an actionable one redirects to the platform path."""
    with pytest.raises(ValueError, match="Pass query_vector instead"):
        guards.elicit_or_refuse(None, action="x", consequence="y", remedy="Pass query_vector instead.")


def test_visual_search_refuses_a_text_query_it_would_have_to_encode_locally(monkeypatch):
    """The previously-untested path: text query, no query_vector, no elicitation -> refuse.

    Regression guard for a real finding: this ran CLIP in-process via undeclared torch/transformers
    dependencies, downloading the weights at runtime, with no gate at all.
    """
    import asyncio

    from mcp import Client

    from doe_mcp.server import build_server

    async def call():
        async with Client(build_server()) as client:
            return await client.call_tool(
                "dataset_visual_search",
                {"project_id": "proj-1", "dataset_id": "ds-1", "query": "a red car"},
            )

    result = asyncio.run(call())

    assert result.is_error is True
    assert "query_vector" in result.content[0].text
