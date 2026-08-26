"""Focused plugin contract checks derived from the marketplace readiness guide."""

import asyncio

from doe_mcp.server import PLUGIN_V1_TOOLS, build_server


def listed_plugin_tools():
    return asyncio.run(build_server(profiles=["plugin-v1"]).list_tools())


def test_focused_tools_have_named_output_properties():
    tools = listed_plugin_tools()
    assert {tool.name for tool in tools} == PLUGIN_V1_TOOLS
    for tool in tools:
        schema = tool.output_schema
        assert schema is not None, f"{tool.name} has no output schema"
        assert schema.get("type") == "object", tool.name
        assert schema.get("properties"), f"{tool.name} exposes a generic object output"


def test_every_read_only_tool_is_marked_idempotent():
    for tool in listed_plugin_tools():
        annotations = tool.annotations
        if annotations and annotations.read_only_hint:
            assert annotations.idempotent_hint is True, tool.name
