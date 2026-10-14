# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Context helpers normalize platform lookup failures."""

from types import SimpleNamespace
from unittest.mock import MagicMock

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


def test_dataloop_call_does_not_double_wrap_a_nested_dataloop_call_failure():
    """get_dataset/get_pipeline wrap a nested get_project lookup; the inner "DOE project lookup
    failed: ..." prefix must not be wrapped again as "DOE dataset lookup failed: DOE project
    lookup failed: ...".
    """

    def inner_fail():
        raise RuntimeError("raw platform detail")

    def outer():
        return context.dataloop_call("project lookup", inner_fail)

    with pytest.raises(RuntimeError) as excinfo:
        context.dataloop_call("dataset lookup", outer)

    message = str(excinfo.value)
    assert message == "DOE project lookup failed: raw platform detail"
    assert message.count("DOE ") == 1


def test_already_normalized_errors_are_recognised_by_type_not_by_message():
    """A plain RuntimeError that merely *starts* with "DOE " is not ours, and must still be
    normalized. Recognising by prefix coupled control flow to prose: any hand-raised message in
    that shape would silently escape wrapping.
    """

    def impostor():
        raise RuntimeError("DOE is down for maintenance")

    with pytest.raises(RuntimeError) as excinfo:
        context.dataloop_call("dataset lookup", impostor)

    assert str(excinfo.value) == "DOE dataset lookup failed: DOE is down for maintenance"


def test_a_normalized_error_is_a_runtime_error_for_existing_callers():
    """DoeError subclasses RuntimeError, so `except RuntimeError` call sites are unaffected."""
    assert issubclass(context.DoeError, RuntimeError)


@pytest.mark.parametrize(
    ("helper_name", "repo_name", "identifier_name"),
    [
        ("get_dataset", "datasets", "dataset_id"),
        ("get_pipeline", "pipelines", "pipeline_id"),
    ],
)
def test_scoped_resource_lookup_accepts_matching_project(
    monkeypatch, helper_name, repo_name, identifier_name
):
    resource = SimpleNamespace(project_id="proj-1")
    repository = MagicMock()
    repository.get.return_value = resource
    project = SimpleNamespace(**{repo_name: repository})
    monkeypatch.setattr(context, "get_project", lambda project_id: project)

    found = getattr(context, helper_name)("proj-1", "resource-1")

    assert found is resource
    repository.get.assert_called_once_with(**{identifier_name: "resource-1"})


@pytest.mark.parametrize(
    ("helper_name", "repo_name"),
    [("get_dataset", "datasets"), ("get_pipeline", "pipelines")],
)
def test_scoped_resource_lookup_rejects_cross_project_response(monkeypatch, helper_name, repo_name):
    repository = MagicMock()
    repository.get.return_value = SimpleNamespace(project_id="proj-other")
    project = SimpleNamespace(**{repo_name: repository})
    monkeypatch.setattr(context, "get_project", lambda project_id: project)

    with pytest.raises(context.DoeError, match="belongs to project 'proj-other'"):
        getattr(context, helper_name)("proj-1", "resource-1")


def test_scoped_resource_lookup_accepts_nested_project_identity(monkeypatch):
    resource = SimpleNamespace(project=SimpleNamespace(id="proj-1"))
    project = SimpleNamespace(datasets=SimpleNamespace(get=lambda **_: resource))
    monkeypatch.setattr(context, "get_project", lambda project_id: project)

    assert context.get_dataset("proj-1", "ds-1") is resource


def test_scoped_resource_lookup_fails_closed_when_ownership_is_missing(monkeypatch):
    project = SimpleNamespace(datasets=SimpleNamespace(get=lambda **_: SimpleNamespace()))
    monkeypatch.setattr(context, "get_project", lambda project_id: project)

    with pytest.raises(context.DoeError, match="ownership.*could not be verified"):
        context.get_dataset("proj-1", "ds-1")


def test_scoped_resource_lookup_stops_when_project_identity_is_denied(monkeypatch):
    def denied(project_id):
        raise context.DoeError("DOE project lookup failed: forbidden")

    monkeypatch.setattr(context, "get_project", denied)

    with pytest.raises(context.DoeError, match="project lookup failed: forbidden"):
        context.get_pipeline("proj-1", "pipe-1")
