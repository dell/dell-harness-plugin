# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

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


def test_dataloop_call_does_not_double_wrap_nested_platform_failures():
    def inner_fail():
        raise RuntimeError("raw platform detail")

    def outer():
        return context.dataloop_call("project lookup", inner_fail)

    with pytest.raises(RuntimeError) as excinfo:
        context.dataloop_call("dataset lookup", outer)

    assert str(excinfo.value) == "DOE project lookup failed: raw platform detail"


def test_normalized_errors_are_recognized_by_type_not_message_prefix():
    def impostor():
        raise RuntimeError("DOE is down for maintenance")

    with pytest.raises(RuntimeError) as excinfo:
        context.dataloop_call("dataset lookup", impostor)

    assert str(excinfo.value) == ("DOE dataset lookup failed: DOE is down for maintenance")
    assert issubclass(context.DoeError, RuntimeError)
