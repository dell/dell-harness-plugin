# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The advertised-surface budget: what a client is sent before any work happens.

Re-baselined 2026-08-27, in two ways that matter.

**Measured with a real tokenizer.** The figures up to now divided characters by four. Against
`tiktoken`'s `o200k_base` that under-counted by ~11%: the surface billed at "20,650" was really
22,910 tokens, because JSON schemas tokenize badly — every brace, quote and colon tends to cost a
token of its own. Every number here is now a real count.

**The ceiling is a decision, not a high-water mark.** The previous 20,650 was simply wherever
Phase 1's trimming happened to stop, against a target of 12,000 it never reached; it then hardened
into a rule that pushed real capability out of the default surface (see the plan's Phase 6a). The
number below is chosen, with headroom stated, and the standing target is recorded alongside it so
the gap stays visible rather than being forgotten.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from measure_tool_surface import (  # noqa: E402
    count_tokens,
    measure,
    tokenizer_available,
)

from doe_mcp import prompts as prompts_module  # noqa: E402
from doe_mcp.server import INSTRUCTIONS, build_server  # noqa: E402

#: Advertised tool surface, in real `o200k_base` tokens.
#:
#: Measured at 30,772 with typed outputs across the full surface, leaving roughly 225 tokens of
#: deliberate headroom — enough for a small parameter, not enough for a tool. Zero headroom was
#: actively harmful: it forced two capability sets behind profile flags and made a `confirm`
#: parameter unaffordable. Headroom is not permission to spend; the ratchet below still applies.
#:
#: **This must go down, not up.** Anything that grows the surface is paid for by trimming, exactly
#: as before. Raising it again needs the same thing this re-baseline had: a measurement, a stated
#: reason, and a note in the plan.
TOTAL_TOKEN_CEILING = 31_000

#: Where this should end up. Phase 1 set 12,000 in chars/4 terms, which is ~13,300 real tokens, and
#: never got near it. Recorded as an aspiration rather than an assertion so the remaining gap
#: stays visible instead of quietly becoming "20,000 is the goal".
STANDING_TARGET_TOKENS = 13_300

#: `INSTRUCTIONS` plus every MCP Prompt, in real tokens. Not part of the per-turn surface — a host
#: fetches a prompt on demand — but ratcheted so prose cannot be moved out of a tool description
#: into a prompt and counted as a saving. Measured at 1,096.
OFF_TURN_PROSE_TOKEN_CEILING = 1_150

DEGENERATE_OUTPUT_SCHEMA_CEILING = 57

PLUGIN_TOKEN_CEILING = 19_800
PLUGIN_DEGENERATE_OUTPUT_SCHEMA_CEILING = 0


def test_the_budget_is_measured_with_a_real_tokenizer():
    """A ceiling enforced against a chars/4 estimate is worse than no ceiling.

    The estimate was wrong by 11% in the direction that flatters, so the surface looked affordable
    while it was not. `tiktoken` is a dev dependency; if it is missing the numbers below silently
    become estimates again, so this fails rather than letting that pass.
    """
    assert tokenizer_available(), (
        "tiktoken is not installed, so the surface would be measured by dividing characters by "
        "four — an estimate that under-counted by 11%. Run `pip install -e '.[dev]'`."
    )


def test_advertised_surface_does_not_regress():
    report = measure()
    assert report["total_tokens"] <= TOTAL_TOKEN_CEILING, (
        f"Advertised tool surface grew to {report['total_tokens']} tok, over the "
        f"{TOTAL_TOKEN_CEILING} tok ceiling. Trim it back down. Raising the ceiling needs a "
        "measurement, a reason, and a note in the plan — see this module's docstring."
    )


def test_plugin_surface_does_not_regress():
    report = measure(["plugin-v1"])
    assert report["tool_count"] == 36
    assert report["total_tokens"] <= PLUGIN_TOKEN_CEILING, (
        f"Plugin surface grew to {report['total_tokens']} tok, over the {PLUGIN_TOKEN_CEILING} tok ceiling."
    )
    assert report["degenerate_output_schema_count"] <= PLUGIN_DEGENERATE_OUTPUT_SCHEMA_CEILING, (
        "Every plugin tool must advertise a named structured output."
    )


def test_the_standing_target_is_still_recorded_as_unmet():
    """Guards against the ceiling quietly becoming the goal.

    If the surface ever does reach the target this fails, which is the right time to revisit both
    numbers rather than leave a stale aspiration in the file.
    """
    report = measure()

    assert report["total_tokens"] > STANDING_TARGET_TOKENS, (
        "The surface now meets the standing target. Update both numbers deliberately."
    )


def test_schema_titles_are_stripped():
    """Pydantic's per-field `title` is 17.8% of the surface and says nothing a description doesn't.

    `strip_schema_titles` reaches into private SDK internals (`_tool_manager._tools`) because the
    server exposes no hook for rewriting a schema after registration. This is the test that fails
    if an SDK change moves that internal, rather than 4,067 tokens silently returning.
    """
    import asyncio

    tools = asyncio.run(build_server().list_tools())
    with_titles = [
        tool.name
        for tool in tools
        if '"title"' in json.dumps(tool.input_schema or {})
        or '"title"' in json.dumps(tool.output_schema or {})
    ]

    assert not with_titles, f"advisory schema titles came back on: {with_titles}"


def test_stripping_titles_leaves_the_tools_own_title_alone():
    """The tool's human label is a different field from the schema's advisory titles."""
    import asyncio

    tools = {tool.name: tool for tool in asyncio.run(build_server().list_tools())}

    assert tools["dataset_list"].title, "the picker label was stripped along with the schema titles"


def test_off_turn_prose_does_not_regress():
    prose = count_tokens(INSTRUCTIONS) + sum(
        count_tokens(getattr(prompts_module, name))
        for name in dir(prompts_module)
        if name.endswith("_PROMPT") and isinstance(getattr(prompts_module, name), str)
    )

    assert prose <= OFF_TURN_PROSE_TOKEN_CEILING, (
        f"INSTRUCTIONS plus MCP Prompts grew to {prose} tok, over the "
        f"{OFF_TURN_PROSE_TOKEN_CEILING} tok ceiling. This is the escape hatch the surface ceiling "
        "cannot see: trim it rather than relocating prose here."
    )


def test_degenerate_output_schemas_do_not_regress():
    report = measure()
    assert report["degenerate_output_schema_count"] <= DEGENERATE_OUTPUT_SCHEMA_CEILING, (
        "A new tool advertises a degenerate `dict[str, Any]` output schema. Use "
        "`structured_output=False` (see Phase 1 of the optimization plan) instead of a bare dict "
        "return annotation."
    )


@pytest.mark.parametrize("profile", ["discovery", "pipelines", "models", "rag", "skills"])
def test_every_profile_is_cheaper_than_the_full_surface(profile):
    """The point of a profile is a smaller surface; one that is not is misconfigured."""
    assert measure([profile])["total_tokens"] < measure()["total_tokens"]
