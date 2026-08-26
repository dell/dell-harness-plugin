"""Profile-scoped entrypoints (plan §4 Phase 3's zero-protocol-risk fallback).

`tools/list_changed` support is client-contingent and untested here (that's the point of the
plan's Phase 3 gate: pick this over dynamic tool gating without needing that test), so this only
covers what a profile controls: which tools `build_server(profiles=...)` actually registers.
"""

import asyncio

import pytest

from doe_mcp.server import FULL_TOOL_NAMES, PLUGIN_V1_TOOLS, PROFILES, build_server


def tool_names(profiles=None):
    async def list_tools():
        return await build_server(profiles=profiles).list_tools()

    return {tool.name for tool in asyncio.run(list_tools())}


def test_no_profile_registers_every_tool():
    assert tool_names(None) == tool_names()
    full = tool_names(None)
    for names in (tool_names(["discovery"]), tool_names(["models"])):
        assert names < full  # a strict subset, not the same set


def test_discovery_profile_has_no_pipeline_or_model_tools():
    names = tool_names(["discovery"])

    assert "dataset_list" in names
    assert "item_list" in names
    assert "pipeline_create" not in names
    assert "model_deploy" not in names
    assert "rag_pipeline_create" not in names


def test_pipelines_profile_has_pipeline_and_node_tools_but_not_models():
    names = tool_names(["pipelines"])

    assert "pipeline_create" in names
    assert "pipeline_node_add" in names
    assert "trigger_create" in names
    assert "model_deploy" not in names
    assert "rag_pipeline_create" not in names


def test_models_profile_has_marketplace_model_and_pii_tools():
    names = tool_names(["models"])

    assert "marketplace_search" in names
    assert "model_deploy" in names
    assert "pii_report" in names
    assert "dataset_embed" in names
    assert "pipeline_create" not in names


def test_rag_profile_has_rag_and_supporting_pipeline_tools():
    names = tool_names(["rag"])

    assert "rag_pipeline_create" in names
    assert "pipeline_connect_nodes" in names
    assert "marketplace_search" in names
    assert "model_deploy" not in names


def test_multiple_profiles_union_their_tools():
    names = tool_names(["discovery", "models"])

    assert "dataset_list" in names
    assert "model_deploy" in names


def test_unknown_profile_is_rejected():
    with pytest.raises(ValueError, match="Unknown profile"):
        build_server(profiles=["not-a-real-profile"])


def test_every_declared_profile_is_a_strict_subset_of_the_full_surface():
    full = tool_names(None)
    for name in PROFILES:
        assert tool_names([name]) < full, f"profile {name!r} isn't smaller than the full surface"


def test_plugin_v1_profile_is_exact_and_cannot_be_combined():
    assert tool_names(None) == FULL_TOOL_NAMES
    assert tool_names(["plugin-v1"]) == PLUGIN_V1_TOOLS
    with pytest.raises(ValueError, match="cannot be combined"):
        build_server(profiles=["plugin-v1", "discovery"])
