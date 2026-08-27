"""Context helpers normalize platform lookup failures."""

import pytest

from doe_mcp import context


def test_dataloop_call_normalizes_sdk_errors():
    def fail():
        raise RuntimeError("raw platform detail")

    with pytest.raises(RuntimeError, match="DOE project lookup failed: raw platform detail"):
        context.dataloop_call("project lookup", fail)


def test_dataloop_call_preserves_local_validation_errors():
    def fail():
        raise ValueError("bad input")

    with pytest.raises(ValueError, match="bad input"):
        context.dataloop_call("project lookup", fail)
