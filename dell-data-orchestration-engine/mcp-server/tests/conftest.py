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
