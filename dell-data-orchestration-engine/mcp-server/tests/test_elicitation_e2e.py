# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""End-to-end proof that a gated tool asks a real elicitation-capable client, not just guards.py.

`tests/test_guards.py` unit-tests `confirm_or_elicit` directly; this drives the whole protocol
stack — a real `mcp.Client` declaring elicitation support, talking to the real `build_server()` —
so a regression in how `Context` is wired into a tool (wrong import, wrong parameter name, capability
plumbing) shows up here even if the guards unit stays green.
"""

import asyncio
from types import SimpleNamespace

import mcp_types as types
import pytest
from mcp import Client

from doe_mcp.server import build_server
from doe_mcp.tools import datasets


def fake_dataset(dataset_id="ds-1", name="images"):
    return SimpleNamespace(id=dataset_id, name=name, project=SimpleNamespace())


async def accept_elicitation(context, params):
    return types.ElicitResult(action="accept", content={"confirmed": True})


async def decline_elicitation(context, params):
    return types.ElicitResult(action="decline")


async def refuse_elicitation(context, params):
    return types.ElicitResult(action="accept", content={"confirmed": False})


def call_with_elicitation(monkeypatch, elicitation_callback, tool_name, arguments):
    fake = fake_dataset()
    deleted = {}

    def fake_get_dataset(project_id, dataset_id):
        return fake

    monkeypatch.setattr(datasets, "get_dataset", fake_get_dataset)
    fake.project.datasets = SimpleNamespace(
        delete=lambda **kwargs: deleted.setdefault("kwargs", kwargs) or True
    )

    async def run():
        # "legacy" mode drives a real (session-based, duplex) InMemoryTransport with an
        # initialize handshake — the modern per-request DirectDispatcher path has no persistent
        # session and so no back-channel for a server-initiated request like elicitation.
        async with Client(build_server(), elicitation_callback=elicitation_callback, mode="legacy") as client:
            return await client.call_tool(tool_name, arguments)

    result = asyncio.run(run())
    return result, deleted


def test_gated_tool_proceeds_when_the_connected_client_accepts_elicitation(monkeypatch):
    result, deleted = call_with_elicitation(
        monkeypatch, accept_elicitation, "dataset_delete", {"project_id": "proj-1", "dataset_id": "ds-1"}
    )

    assert result.is_error is False
    assert deleted  # the delete call reached dtlpy, i.e. the elicitation accept was honoured


def test_gated_tool_refuses_when_the_connected_client_declines_elicitation(monkeypatch):
    result, deleted = call_with_elicitation(
        monkeypatch, decline_elicitation, "dataset_delete", {"project_id": "proj-1", "dataset_id": "ds-1"}
    )

    assert result.is_error is True
    assert "did not confirm" in result.content[0].text
    assert not deleted


def test_gated_tool_refuses_when_the_form_itself_says_no(monkeypatch):
    """The client accepted the elicitation request, but the human answered 'confirmed: false'."""
    result, deleted = call_with_elicitation(
        monkeypatch, refuse_elicitation, "dataset_delete", {"project_id": "proj-1", "dataset_id": "ds-1"}
    )

    assert result.is_error is True
    assert "did not confirm" in result.content[0].text
    assert not deleted


def test_gated_tool_still_honours_a_plain_confirm_true_with_an_elicitation_capable_client(monkeypatch):
    """confirm=true short-circuits even when the client could be asked — no redundant round-trip."""

    async def fail_if_called(context, params):
        pytest.fail("Should not have elicited: confirm=true already authorized the call.")

    result, deleted = call_with_elicitation(
        monkeypatch,
        fail_if_called,
        "dataset_delete",
        {"project_id": "proj-1", "dataset_id": "ds-1", "confirm": True},
    )

    assert result.is_error is False
    assert deleted


def test_progress_notifications_reach_a_client_that_asked_for_them(monkeypatch):
    """embed/deploy/install report start and completion; a real client sees both."""
    fake_pipeline = SimpleNamespace(id="pipe-1", name="rag", project=SimpleNamespace())
    fake_pipeline.project.pipelines = SimpleNamespace(
        install=lambda pipeline, resume_option: SimpleNamespace(
            id="pipe-1",
            name="rag",
            status="installed",
            project_id="proj-1",
            description=None,
            created_at=None,
            updated_at=None,
            nodes=[],
            url=None,
        )
    )
    monkeypatch.setattr("doe_mcp.tools.pipelines.get_pipeline", lambda pid, ppid: fake_pipeline)

    updates = []

    async def on_progress(progress, total, message):
        updates.append((progress, total, message))

    async def run():
        async with Client(build_server(), mode="legacy") as client:
            return await client.call_tool(
                "pipeline_install",
                {
                    "project_id": "proj-1",
                    "pipeline_id": "pipe-1",
                    "confirm": True,
                },
                progress_callback=on_progress,
            )

    result = asyncio.run(run())

    assert result.is_error is False
    assert updates == [(0, 100, "Installing pipeline 'rag'..."), (100, 100, "Pipeline installed.")]
