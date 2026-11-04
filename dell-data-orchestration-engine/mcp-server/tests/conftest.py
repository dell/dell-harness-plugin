# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared test fixtures.

Nothing here touches the network: dtlpy is replaced with fakes at the seams the tools call
through (``doe_mcp.context`` and ``doe_mcp.auth``).
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import pytest
from mcp import Client

from doe_mcp.server import build_server

T = TypeVar("T")


def run(coro: Awaitable[T]) -> T:
    """Drive a coroutine from a synchronous test, so no async plugin is needed."""
    return asyncio.run(coro)  # type: ignore[arg-type]


@pytest.fixture
def call_tool() -> Callable[..., Any]:
    """Call a tool over an in-memory client, exactly as a real host would."""

    def _call(name: str, arguments: dict[str, Any] | None = None):
        async def _run():
            mcp = build_server()
            async with Client(mcp) as client:
                return await client.call_tool(name, arguments or {})

        return run(_run())

    return _call


@pytest.fixture(autouse=True)
def _no_real_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Make sure the tests never touch a real DOE login or shared state directory."""
    monkeypatch.delenv("DATALOOP_API_KEY", raising=False)
    monkeypatch.delenv("DATALOOP_GATE_URL", raising=False)
    monkeypatch.delenv("DATALOOP_VERIFY_SSL", raising=False)
    monkeypatch.setenv("DATALOOP_PATH", str(tmp_path_factory.mktemp("dataloop-state")))


@pytest.fixture(autouse=True)
def _no_platform_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail fast on any real dtlpy HTTP call.

    dtlpy binds its state directory at import, so a developer's own ~/.dataloop login and
    gate can otherwise leak into an offline test that reaches `dl.*` directly.
    """
    import dtlpy as dl

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("offline test suite: real DOE platform requests are blocked")

    for method in ("gen_request", "gen_async_request", "send_session"):
        monkeypatch.setattr(type(dl.client_api), method, refuse)


@pytest.fixture(autouse=True)
def _undeclared_mock_owners_are_not_under_test(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let a bare ``MagicMock`` resource pass the project-ownership check.

    Most tool tests fake platform entities with ``MagicMock``, whose ``project_id`` is an
    auto-created child mock: the fake never declared an owner, so the ownership check is not
    what that test is about. Any fake that *does* declare one (a string ``project_id`` or a
    ``project.id``) is still checked strictly, which is how ``test_context`` and the R04 tests
    exercise rejection. Production code never sees this fixture.
    """
    from unittest.mock import Base

    from doe_mcp import context

    strict = context._verify_resource_project

    def verify(resource: Any, project_id: str, resource_name: str) -> Any:
        owner = getattr(resource, "project_id", None)
        if owner is None and getattr(resource, "project", None) is not None:
            owner = getattr(resource.project, "id", None)
        if isinstance(owner, Base) or isinstance(project_id, Base) or "Mock" in str(project_id):
            return resource
        return strict(resource, project_id, resource_name)

    monkeypatch.setattr(context, "_verify_resource_project", verify)


class OwnedRepo:
    """A repository whose ``get`` returns a resource owned by ``project_id``.

    For fakes that only need the project-ownership check (R04) to pass; tests about rejection
    build their own mismatching resources.
    """

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id

    def get(self, **lookup: Any) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(id=next(iter(lookup.values())), project_id=self.project_id)


class FakeRepo:
    """Records calls and returns canned entities."""

    def __init__(self, **returns: Any) -> None:
        self._returns = returns
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def method(**kwargs: Any) -> Any:
            self.calls.append((name, kwargs))
            value = self._returns.get(name)
            if callable(value):
                return value(**kwargs)
            return value

        return method
