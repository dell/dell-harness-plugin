# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Project discovery. Read-only: projects are created outside this server."""

from typing import Annotated

import dtlpy as dl
from mcp.server import MCPServer
from pydantic import Field

from .. import outputs, paging, serialize
from ..auth import ensure_authenticated
from ..context import get_project
from ..safety import READ_ONLY


def register(mcp: MCPServer) -> None:
    @mcp.tool(title="List projects", annotations=READ_ONLY)
    def project_list(
        page: Annotated[int, Field(ge=0, description="Zero-based page number.")] = 0,
        page_size: Annotated[int, Field(ge=1, le=paging.MAX_LIMIT, description="Projects per page.")] = (
            paging.DEFAULT_LIMIT
        ),
        limit: Annotated[int, Field(ge=1, le=paging.MAX_LIMIT, description="Max projects to return.")] = (
            paging.DEFAULT_LIMIT
        ),
    ) -> paging.ListResult:
        """List the DOE projects this account can access.

        Start here: nearly every other tool needs a project_id.
        """
        ensure_authenticated()
        return paging.from_list(
            dl.projects.list(), serialize.project, page=page, page_size=page_size, limit=limit
        )

    @mcp.tool(title="Get project", annotations=READ_ONLY)
    def project_get(
        project_id: Annotated[str, Field(description="Id of the project to fetch.")],
    ) -> outputs.ProjectSummary:
        """Fetch a single project by id."""
        return serialize.project(get_project(project_id))
