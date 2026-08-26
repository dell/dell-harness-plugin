"""Progress notifications for tool calls that run one long blocking platform call.

``dtlpy`` gives no callback partway through ``embed_datasets``, ``pipelines.install`` or
``models.deploy`` — they are one blocking call each — so there is no real intermediate progress to
report. What is still worth sending is a start and a completion notification, so a host that
renders progress shows something other than silence for the seconds to minutes these calls take.
``ServerSession.report_progress`` is already a no-op when the caller did not request progress
(see ``mcp/server/session.py``), so this never needs its own capability check.
"""

from typing import TYPE_CHECKING

import anyio.from_thread

if TYPE_CHECKING:
    from mcp.server.mcpserver import Context


def report_progress(
    ctx: "Context | None", progress: float, total: float | None = None, message: str | None = None
) -> None:
    """Report progress on the current call, if a request context is available.

    ``ctx`` is ``None`` in the same situations ``confirm_or_elicit`` tolerates it being
    ``None`` — direct unit-test calls into a tool body, or any future call site with no request
    context — so this degrades to nothing rather than raising.
    """
    if ctx is None:
        return
    # Tool bodies are sync functions run in a worker thread (see server.py's module docstring);
    # anyio.from_thread.run calls back into the event loop that spawned that thread.
    anyio.from_thread.run(ctx.report_progress, progress, total, message)
