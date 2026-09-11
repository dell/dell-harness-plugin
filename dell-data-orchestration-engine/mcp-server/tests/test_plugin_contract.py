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


def test_default_contract_exposes_new_safety_and_pagination_inputs():
    by_name = {tool.name: tool for tool in listed_plugin_tools()}

    install = by_name["pipeline_install"].input_schema["properties"]
    create_model = by_name["model_create"].input_schema["properties"]
    annotations = by_name["annotation_list"].input_schema["properties"]

    assert install["confirm"]["default"] is False
    assert create_model["confirm"]["default"] is False
    assert annotations["page"]["default"] == 0
    assert annotations["page_size"]["default"] > 0
