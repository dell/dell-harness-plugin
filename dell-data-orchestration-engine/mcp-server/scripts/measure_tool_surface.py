#!/usr/bin/env python
"""Measure the advertised tool surface: chars and approximate tokens, broken down by component.

Reproduces the numbers in `docs/plans/AGENT_OPTIMIZATION_HARNESS_SLM_PLAN.md` §1 so they stay
verifiable rather than becoming stale prose. Tokens are approximated as chars/4 — the same method
the plan document uses, since `tiktoken` is not a project dependency (see plan §1: "Token figures
are chars/4 approximations").

Usage:
    python scripts/measure_tool_surface.py [--json] [--profile NAME ...]

No credentials are needed: authentication is lazy, so a placeholder DATALOOP_API_KEY is enough to
build the server and list its tools.
"""

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from typing import Any

os.environ.setdefault("DATALOOP_API_KEY", "placeholder-not-a-real-key")

from doe_mcp.server import INSTRUCTIONS, PROFILES, build_server  # noqa: E402

CHARS_PER_TOKEN = 4


def _chars(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    return len(json.dumps(value, separators=(",", ":")))


def tool_prefix(name: str) -> str:
    """Group a tool by its leading `<prefix>_` component, e.g. `pipeline_node_add` -> `pipeline`."""
    return name.split("_", 1)[0]


def measure(profiles: list[str] | None = None) -> dict[str, Any]:
    tools = asyncio.run(build_server(profiles=profiles).list_tools())

    per_component = Counter[str]()
    per_prefix_chars = Counter[str]()
    per_prefix_count = Counter[str]()
    per_tool_input_chars: dict[str, int] = {}

    for tool in tools:
        name_chars = _chars(tool.name) + _chars(getattr(tool, "title", None))
        description_chars = _chars(tool.description)
        input_chars = _chars(tool.input_schema)
        output_chars = _chars(tool.output_schema)

        per_component["name_and_title"] += name_chars
        per_component["description"] += description_chars
        per_component["input_schema"] += input_chars
        per_component["output_schema"] += output_chars

        prefix = tool_prefix(tool.name)
        tool_total = name_chars + description_chars + input_chars + output_chars
        per_prefix_chars[prefix] += tool_total
        per_prefix_count[prefix] += 1
        per_tool_input_chars[tool.name] = input_chars

    total_chars = sum(per_component.values())
    instructions_chars = _chars(INSTRUCTIONS)

    degenerate_output = sum(
        1
        for tool in tools
        if tool.output_schema is not None
        and tool.output_schema.get("additionalProperties") is True
        and tool.output_schema.get("title", "").endswith("DictOutput")
    )
    degenerate_chars = sum(
        _chars(tool.output_schema)
        for tool in tools
        if tool.output_schema is not None
        and tool.output_schema.get("additionalProperties") is True
        and tool.output_schema.get("title", "").endswith("DictOutput")
    )

    return {
        "tool_count": len(tools),
        "total_chars": total_chars,
        "total_tokens_approx": total_chars // CHARS_PER_TOKEN,
        "instructions_chars": instructions_chars,
        "instructions_tokens_approx": instructions_chars // CHARS_PER_TOKEN,
        "by_component_chars": dict(per_component),
        "by_component_tokens_approx": {k: v // CHARS_PER_TOKEN for k, v in per_component.items()},
        "by_prefix_chars": dict(per_prefix_chars),
        "by_prefix_tool_count": dict(per_prefix_count),
        "degenerate_output_schema_count": degenerate_output,
        "degenerate_output_schema_chars": degenerate_chars,
        "degenerate_output_schema_tokens_approx": degenerate_chars // CHARS_PER_TOKEN,
        "largest_input_schemas": sorted(per_tool_input_chars.items(), key=lambda kv: -kv[1])[:5],
        "by_tool_input_chars": per_tool_input_chars,
    }


def print_report(report: dict[str, Any]) -> None:
    print(
        f"{report['tool_count']} tools · {report['total_chars']} chars · "
        f"~{report['total_tokens_approx']} tokens advertised on every turn"
    )
    print(
        f"Plus INSTRUCTIONS = {report['instructions_chars']} chars "
        f"(~{report['instructions_tokens_approx']} tok)"
    )
    print()
    print("By component:")
    for component, chars in report["by_component_chars"].items():
        tokens = report["by_component_tokens_approx"][component]
        print(f"  {component:20s} {chars:8d} chars  ~{tokens:6d} tok")
    print()
    print("By prefix:")
    for prefix, chars in sorted(report["by_prefix_chars"].items(), key=lambda kv: -kv[1]):
        count = report["by_prefix_tool_count"][prefix]
        print(f"  {prefix:15s} {count:3d} tools  {chars:8d} chars  ~{chars // CHARS_PER_TOKEN:6d} tok")
    print()
    print(
        f"Degenerate output schemas: {report['degenerate_output_schema_count']} tools, "
        f"{report['degenerate_output_schema_chars']} chars "
        f"(~{report['degenerate_output_schema_tokens_approx']} tok)"
    )
    print()
    print("Largest input schemas:")
    for name, chars in report["largest_input_schemas"]:
        print(f"  {name:30s} {chars:6d} chars")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON instead")
    parser.add_argument(
        "--profile",
        action="append",
        choices=sorted(PROFILES),
        default=None,
        help="Measure only this profile's tools (see server.py's PROFILES); repeat to union several.",
    )
    args = parser.parse_args()

    report = measure(profiles=args.profile)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
