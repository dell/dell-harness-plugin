# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""End-to-end check over a real stdio subprocess.

The other tests drive the server in-process. This one starts it the way a host does, so it
also covers the entrypoint, tool registration at startup, and the two error channels:
missing credentials must arrive as a JSON-RPC error, while a refused destructive call must
come back as tool output the model can read.
"""

import os
import sys
import tempfile

import pytest
from mcp import Client, MCPError
from mcp.client.stdio import StdioServerParameters, stdio_client

from .conftest import run


def server_params() -> StdioServerParameters:
    env = {key: value for key, value in os.environ.items() if key != "DATALOOP_API_KEY"}
    env.setdefault("DATALOOP_PATH", os.path.join(tempfile.gettempdir(), "doe-mcp-tests-dataloop"))
    # Reach the package without depending on an editable install being present.
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    return StdioServerParameters(command=sys.executable, args=["-m", "doe_mcp.server"], env=env)


@pytest.fixture(scope="module")
def stdio_results():
    """Start the server once and collect everything the assertions need."""

    async def _run():
        async with Client(stdio_client(server_params())) as client:
            results: dict = {
                "server_info": client.server_info,
                "protocol_version": client.protocol_version,
                "tools": [tool.name for tool in (await client.list_tools()).tools],
            }
            try:
                await client.call_tool("project_list", {})
                results["credentials_error"] = None
            except MCPError as exc:
                results["credentials_error"] = str(exc)
            results["delete_without_confirm"] = await client.call_tool(
                "dataset_delete", {"project_id": "p", "dataset_id": "d"}
            )
            return results

    return run(_run())


def test_server_identifies_itself(stdio_results):
    assert stdio_results["server_info"].name == "doe-mcp"


def test_negotiates_a_modern_protocol_version(stdio_results):
    assert stdio_results["protocol_version"] == "2026-07-28"


def test_advertises_every_tool_area(stdio_results):
    tools = stdio_results["tools"]

    for expected in (
        "project_list",
        "dataset_create",
        "pipeline_create",
        "pipeline_create_from_template",
        "pipeline_node_add",
        "marketplace_search",
        "model_install",
        "model_deploy",
    ):
        assert expected in tools


def test_missing_credentials_is_a_protocol_error(stdio_results):
    """The host, not the model, is the audience for a missing API key."""
    assert "DATALOOP_API_KEY is not set" in stdio_results["credentials_error"]


def test_refused_delete_is_a_tool_error(stdio_results):
    """Recoverable by the caller, so it comes back as readable tool output."""
    result = stdio_results["delete_without_confirm"]

    assert result.is_error is True
    assert "confirm=true" in result.content[0].text
