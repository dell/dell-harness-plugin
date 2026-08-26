"""progress.report_progress: a thin, always-safe wrapper around Context.report_progress."""

import asyncio
from types import SimpleNamespace

import anyio.to_thread

from doe_mcp import progress


def run_in_worker_thread(fn, *args):
    """Drive a sync function the way a tool body actually runs, so anyio.from_thread.run works."""
    return asyncio.run(anyio.to_thread.run_sync(fn, *args))


def test_report_progress_is_a_no_op_without_a_context():
    progress.report_progress(None, 50, 100, "halfway")


def test_report_progress_forwards_to_the_context():
    calls = []

    async def fake_report_progress(p, total=None, message=None):
        calls.append((p, total, message))

    ctx = SimpleNamespace(report_progress=fake_report_progress)

    run_in_worker_thread(progress.report_progress, ctx, 50, 100, "halfway")

    assert calls == [(50, 100, "halfway")]
