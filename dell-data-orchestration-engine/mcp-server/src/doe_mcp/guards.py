"""Confirmation gates for calls the caller cannot undo cheaply.

Two different things are gated in this server and they are easy to conflate:

* ``validate_overrides`` in ``tools/models.py`` gates *raw platform overrides* — it asks the
  caller to confirm the shape of a dict being passed through to dtlpy. It returns early when
  no overrides are supplied, so it says nothing about whether the call itself is safe.
* ``require_confirmation`` here gates *the call*, whatever its arguments. It is for work that
  spends money or leaves something running after the conversation ends.

The distinction was originally missing: ``model_train`` and ``model_deploy`` took a ``confirm``
argument that only ever reached ``validate_overrides``, so a plain training run with no overrides
started a paid job with ``confirm=False``.

``confirm_or_elicit`` closes a real hole in ``require_confirmation`` alone: a model satisfies
``confirm=true`` by setting its own argument, so nothing here proves a human actually agreed. Where
the connected client declares elicitation support (verified usable —
docs/plans/AGENT_OPTIMIZATION_HARNESS_SLM_PLAN.md §2), it asks the human directly instead, and
falls back to the ``confirm=true`` parameter for clients that do not.
"""

from typing import TYPE_CHECKING

import anyio.from_thread
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from mcp.server.mcpserver import Context


def require_confirmation(confirm: bool, action: str, consequence: str) -> None:
    """Refuse an unconfirmed call, naming what it would have cost or left behind."""
    if not confirm:
        raise ValueError(f"Refusing to {action}: {consequence}. Set confirm=true once the user has agreed.")


class ConfirmationRequest(BaseModel):
    """The yes/no a human answers when a gated tool call needs their say-so, not the model's."""

    confirmed: bool = Field(description="True to proceed, false to refuse.")


def _elicitation_supported(ctx: "Context | None") -> bool:
    if ctx is None:
        return False
    capabilities = ctx.client_capabilities
    return capabilities is not None and capabilities.elicitation is not None


def confirm_or_elicit(ctx: "Context | None", confirm: bool, action: str, consequence: str) -> None:
    """Gate a call that spends money or leaves something running.

    Prefers asking the connected human directly through elicitation over trusting the model's own
    ``confirm=true``, since only the former proves a human actually agreed. Degrades to
    ``require_confirmation`` for a ``ctx`` that is absent (e.g. a resolver call site) or for a
    client that has not declared elicitation support, rather than breaking those callers.
    """
    if confirm:
        return
    if not _elicitation_supported(ctx):
        require_confirmation(confirm, action, consequence)
        return

    assert ctx is not None  # _elicitation_supported already ruled out None
    message = f"About to {action}: {consequence}. Proceed?"
    # Tool bodies are sync functions run in a worker thread (see server.py's module docstring);
    # anyio.from_thread.run calls back into the event loop that spawned that thread.
    result = anyio.from_thread.run(ctx.elicit, message, ConfirmationRequest)
    if result.action != "accept" or not result.data.confirmed:
        raise ValueError(f"Refusing to {action}: the user did not confirm.")


#: The most feature vectors a tool may pull off the platform and process in *this* process without
#: a human agreeing first.
#:
#: Set equal to the `le=` cap those tools already advertise, which is the point: today the schema
#: cap is the binding control and this gate never fires. That is the correct state -- a hard cap an
#: agent cannot exceed is stronger than a prompt it can click through -- and the gate exists as a
#: tripwire for the day someone raises a cap. `tests/test_guards.py` pins the relationship, so raising
#: `max_vectors` past this number is a deliberate act that either lowers the ceiling back or
#: accepts the elicitation prompt.
LOCAL_COMPUTE_VECTOR_BUDGET = 2_000


def elicit_or_refuse(ctx: "Context | None", *, action: str, consequence: str, remedy: str) -> None:
    """Ask the human, or refuse. No `confirm=true` escape hatch.

    For decisions a model must not be able to take on its own behalf. `confirm_or_elicit` falls
    back to trusting the model's own `confirm=true` when the client cannot elicit, which is the
    right trade for a billable operation the user asked for — but not for "download and run a
    third-party model in this process", where the whole question is whether a human agrees to it.

    Costs nothing on the advertised surface either, since there is no parameter to declare, and
    the tool-surface budget has no headroom. `remedy` must name the way to proceed *without* the
    gated behaviour, so a refusal is actionable rather than a dead end.
    """
    if not _elicitation_supported(ctx):
        raise ValueError(f"Refusing to {action}: {consequence}. {remedy}")
    confirm_or_elicit(ctx, False, action, consequence)


def gate_local_compute(ctx: "Context | None", *, requested: int, action: str, parameter: str) -> None:
    """Gate work this server performs **in its own process** on data pulled off the platform.

    A few tools (`dataset_cluster`, `dataset_visual_search`, `rag_query`) read feature vectors out
    of DOE and compute over them here — k-means, cosine similarity — rather than on the
    platform. That is a deliberate, reviewed exception (ADR-017) and it stays available, but past a
    volume it stops being "a bounded diagnostic read" and becomes exactly the local computation the
    project's conventions say a human should agree to first.

    Gated on the caller's existing bound (`max_vectors`, and friends) rather than on a new
    `confirm` parameter, for two reasons: it costs nothing on the advertised tool surface, which
    has no headroom; and it keeps routine small reads unprompted, so the gate stays meaningful
    instead of becoming a reflex to click through.

    **What this cannot do**, and no MCP server can: gate computation the *agent* runs on its own,
    in its own sandbox, between tool calls. The server sees tool calls and nothing else. If an
    agent lists items and then runs its own code over them, nothing here observes it. The reachable
    controls are this gate, the tool annotations, and the skill's declared contract — see
    `docs/eval.md` on detection after the fact.
    """
    if requested <= LOCAL_COMPUTE_VECTOR_BUDGET:
        return
    consequence = (
        f"it pulls up to {requested} feature vectors out of DOE and computes over them "
        f"locally, in the doe-mcp process, rather than on the platform"
    )
    if not _elicitation_supported(ctx):
        raise ValueError(
            f"Refusing to {action}: {consequence}. Lower {parameter} to "
            f"{LOCAL_COMPUTE_VECTOR_BUDGET} or less, or connect a client that supports "
            "elicitation so the user can be asked directly."
        )
    confirm_or_elicit(ctx, False, action, consequence)
