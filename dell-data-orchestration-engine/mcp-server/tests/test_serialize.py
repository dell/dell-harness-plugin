# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Serialization returns MCP structured-content primitives."""

from datetime import datetime, timezone
from enum import Enum
from types import SimpleNamespace

import pytest

from doe_mcp import serialize


class Status(Enum):
    READY = "ready"


def test_dataset_serializer_normalizes_json_values():
    dataset = SimpleNamespace(
        id="ds-1",
        name="images",
        items_count=1,
        annotations_count=0,
        readonly=False,
        index_driver="v2",
        creator="me@example.com",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=Status.READY,
        url="https://example/ds-1",
    )

    result = serialize.dataset(dataset)

    assert result["created_at"] == "2026-01-01T00:00:00+00:00"
    assert result["updated_at"] == "ready"


def test_json_safe_reads_a_value_like_attribute():
    enum_like = SimpleNamespace(value="ready")

    assert serialize.json_safe(enum_like) == "ready"


def test_json_safe_does_not_raise_when_the_value_attribute_itself_raises():
    """Regression: the `hasattr(value, "value")` fallback called `.value` unguarded, unlike
    `optional()`, which exists in this same module specifically because dtlpy properties can
    raise on access (`Item.platform_url`). A single bad `.value` access must not blow up the
    serialization of an otherwise-successful entity.
    """

    class RaisesOnValue:
        @property
        def value(self):
            raise RuntimeError("boom")

    instance = RaisesOnValue()

    assert serialize.json_safe(instance) == str(instance)


def test_json_safe_reads_a_value_attribute_that_is_legitimately_none():
    """A sentinel, not `None`, marks "no .value attribute" -- otherwise a value type whose
    `.value` really is None would fall through to `str(value)` and be reported as its repr.
    """

    class NoneValued:
        value = None

    assert serialize.json_safe(NoneValued()) is None


def test_json_safe_does_not_swallow_a_failure_below_the_value_read():
    """Only the `.value` read is guarded. A failure deeper in the conversion is a real defect;
    hiding it would discard a partly-converted structure and return a plausible-looking string.
    """

    class Exploding(dict):
        def items(self):
            raise RuntimeError("boom")

    class Wrapper:
        value = Exploding()

    with pytest.raises(RuntimeError, match="boom"):
        serialize.json_safe(Wrapper())


def test_pipeline_serializer_tolerates_missing_graph_fields():
    pipeline = SimpleNamespace(
        id="pipe-1",
        name="flow",
        status=Status.READY,
        project_id="proj-1",
        description=None,
        created_at=None,
        updated_at=None,
        nodes=None,
        url="https://example/pipe-1",
    )

    result = serialize.pipeline(pipeline, include_graph=True)

    assert result["status"] == "ready"
    assert result["node_count"] == 0
    assert result["nodes"] == []
    assert result["connections"] == []


# --- computed properties that raise (found by live smoke, not by mocks) -----------------


class _RaisingUrlItem(SimpleNamespace):
    """An item whose platform_url behaves the way the real SDK's does without context."""

    @property
    def platform_url(self):
        raise RuntimeError("('400', 'Unknown environment: None')")


def test_item_summary_survives_a_platform_url_that_raises():
    """Pin the upstream behaviour that forces serialize.optional to exist.

    dtlpy computes Item.platform_url through the client environment and raises
    BadRequest when that context is missing. Reading it directly turned every
    successfully uploaded item into a reported failure — offline mocks never saw it
    because a SimpleNamespace attribute cannot raise.
    """
    item = _RaisingUrlItem(
        id="item-1",
        name="a.txt",
        dir="/",
        type="file",
        mimetype="text/plain",
        filename="/a.txt",
        dataset_id="ds-1",
        annotated=False,
        annotations_count=0,
        created_at="2026-01-01",
        metadata={"user": {"k": 1}},
    )

    summary = serialize.item(item)

    assert summary["id"] == "item-1"
    assert summary["url"] is None
    # The rest of the summary must be intact; a missing URL is not a failed item.
    assert summary["name"] == "a.txt"
    assert summary["metadata"] == {"user": {"k": 1}}
