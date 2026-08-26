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
