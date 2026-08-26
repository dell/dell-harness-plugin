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
