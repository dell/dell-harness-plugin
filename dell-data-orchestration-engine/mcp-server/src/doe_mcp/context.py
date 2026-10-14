# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Project scoping for tool bodies.

dtlpy's module-level repositories are not safe for writes: ``dl.pipelines`` substitutes an
empty ``Project`` when it has none, and ``dl.datasets``/``dl.models`` carry ``project=None``,
so create calls either fail or land in the wrong place. Every tool therefore resolves the
project explicitly and works through ``project.<repository>``.
"""

from collections.abc import Callable
from typing import TypeVar

import dtlpy as dl
from mcp import MCPError

from .auth import ensure_authenticated

T = TypeVar("T")


class DoeError(RuntimeError):
    """A platform failure already normalized by :func:`dataloop_call`."""


def dataloop_call(action: str, func: Callable[[], T]) -> T:
    """Normalize common dtlpy failures without hiding local validation errors."""
    try:
        return func()
    except (MCPError, ValueError, DoeError):
        # Nested lookups are already normalized. Re-wrapping them would obscure the
        # original operation with a second ``DOE <action> failed`` prefix.
        raise
    except Exception as exc:
        message = str(exc).strip() or type(exc).__name__
        raise DoeError(f"DOE {action} failed: {message}") from exc


def get_project(project_id: str) -> dl.entities.Project:
    """Authenticate and fetch the project a tool call is scoped to."""
    ensure_authenticated()
    return dataloop_call("project lookup", lambda: dl.projects.get(project_id=project_id))


def get_dataset(project_id: str, dataset_id: str) -> dl.entities.Dataset:
    """Fetch a dataset within its project."""
    return dataloop_call(
        "dataset lookup", lambda: get_project(project_id).datasets.get(dataset_id=dataset_id)
    )


def get_pipeline(project_id: str, pipeline_id: str) -> dl.entities.Pipeline:
    """Fetch a pipeline within its project."""
    return dataloop_call(
        "pipeline lookup", lambda: get_project(project_id).pipelines.get(pipeline_id=pipeline_id)
    )


def marketplace_dpks() -> dl.repositories.Dpks:
    """A project-less DPK repository.

    ``Dpks.list`` adds a ``context.project`` filter whenever its repository carries a
    project, which restricts results to DPKs published by that project. Browsing the
    shared catalogue therefore requires a repository with no project attached.
    """
    ensure_authenticated()
    return dl.repositories.Dpks(client_api=dl.client_api)
