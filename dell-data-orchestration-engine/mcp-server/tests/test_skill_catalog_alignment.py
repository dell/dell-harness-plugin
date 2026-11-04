# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""R07: everything the installed plugin tells the agent names only tools it actually has.

The plugin launches the ``plugin-v1`` profile. Its skills, their references, the server
instructions and every advertised tool description or schema must not send the agent to a
tool outside that profile; an instruction to call a missing tool is a dead end at best and an
invitation to improvise at worst. Administrator documentation of the other profiles is allowed
only under the catalog's "Administrator-only profiles" heading.

Skipped when this server is checked out without the plugin around it (upstream doe-mcp).
"""

import asyncio
import json
import pathlib
import re

import pytest
from mcp import Client

from doe_mcp.server import PLUGIN_V1_INSTRUCTIONS, PROFILES, build_server

PLUGIN = pathlib.Path(__file__).resolve().parents[2]
SKILLS = PLUGIN / "skills"
ADMIN_CATALOG = SKILLS / "configure-dell-data-orchestration-engine" / "references" / "capability-catalog.md"
ADMIN_HEADING = "## Administrator-only profiles"

pytestmark = pytest.mark.skipif(not SKILLS.is_dir(), reason="plugin skills are not bundled here")


def _tools(profiles):
    async def run():
        async with Client(build_server(profiles=profiles)) as client:
            return (await client.list_tools()).tools

    return asyncio.run(run())


PLUGIN_TOOLS = _tools(["plugin-v1"])
DEFAULT = {tool.name for tool in PLUGIN_TOOLS}
EVERY_TOOL = {tool.name for profile in PROFILES for tool in _tools([profile])}


def _mentioned(text: str) -> set[str]:
    return {word for word in re.findall(r"\b[a-z]+(?:_[a-z0-9]+)+\b", text) if word in EVERY_TOOL}


def _documents():
    for path in sorted(SKILLS.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        if path == ADMIN_CATALOG:
            admin_start = text.index(ADMIN_HEADING)
            admin_end = text.index("\n## ", admin_start + len(ADMIN_HEADING))
            text = text[:admin_start] + text[admin_end:]
        yield str(path.relative_to(PLUGIN)), text


@pytest.mark.parametrize(("name", "text"), list(_documents()), ids=lambda value: str(value)[:60])
def test_skill_documents_name_only_default_tools(name, text):
    assert _mentioned(text) <= DEFAULT, f"{name} names tools outside plugin-v1: {_mentioned(text) - DEFAULT}"


def test_server_instructions_name_only_default_tools():
    assert _mentioned(PLUGIN_V1_INSTRUCTIONS) <= DEFAULT


@pytest.mark.parametrize("tool", PLUGIN_TOOLS, ids=lambda tool: tool.name)
def test_advertised_tool_text_names_only_default_tools(tool):
    text = " ".join([tool.description or "", json.dumps(tool.input_schema), json.dumps(tool.output_schema or {})])

    assert _mentioned(text) <= DEFAULT, f"{tool.name} points at {_mentioned(text) - DEFAULT}"


def test_skills_never_ask_the_agent_to_switch_profiles():
    offenders = [
        name
        for name, text in _documents()
        if "configure-dell-data-orchestration-engine" not in name
        and re.search(r"\buse (the )?`?(discovery|models|pipelines|rag)`?( profile|,)", text, re.IGNORECASE)
    ]

    assert offenders == []
