#!/usr/bin/env python
# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""How much of the `dtlpy` SDK surface is reachable through doe-mcp's tools, and what is missing.

Answers "what are we missing from dtlpy that could become a tool or a skill" mechanically, by
enumerating the SDK's repository methods and matching them against the tools this server
advertises. No model, no credentials, no network.

**Read the output as a map, not a scoreboard.** A low coverage percentage is not a defect: most of
`dtlpy` is deliberately out of scope (annotation-studio plumbing, org administration, driver
management), and this repo's whole thesis is a *bounded* surface — Phase 1 spent real effort
cutting it. What the report is for is spotting a capability a documented workflow needs and no tool
provides, which is a different question from raw breadth.

Matching is lexical and therefore approximate: a repository method counts as covered when a tool
name maps to it through `TOOL_TO_SDK`, or when the tool and method names share a normalised verb
and noun. Over-counting is possible where names coincide; under-counting is likelier, since a tool
often composes several SDK calls. Treat the gap list as a shortlist to read, not a verdict.

Usage:
    python scripts/measure_dtlpy_coverage.py            # ranked gap report
    python scripts/measure_dtlpy_coverage.py --json     # machine-readable
    python scripts/measure_dtlpy_coverage.py --repo Items   # one repository in detail
"""

import argparse
import asyncio
import inspect
import json
import os
import sys
from collections import defaultdict
from typing import Any

import dtlpy as dl

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from doe_mcp.server import build_server  # noqa: E402

#: Repositories whose absence is a deliberate scope decision rather than a gap. Each entry is a
#: reason, printed alongside the report so the exclusion stays arguable rather than invisible.
OUT_OF_SCOPE: dict[str, str] = {
    "Analytics": "usage reporting; not part of a data-science workflow this server serves",
    "Assignments": "annotation-studio workload distribution; task tooling stops at task creation",
    "Bots": "org-level service accounts; an administration concern",
    "Codebases": "package source storage, reached through Packages/Dpks when needed",
    "Collections": "UI-level item grouping with no workflow in docs/guides",
    "Commands": "async platform job handles; surfaced through the calls that create them",
    "Compositions": "internal service composition",
    "Computes": "cluster administration",
    "Downloader": "internal helper behind Items.download",
    "Drivers": "storage-driver administration",
    "Integrations": "credential administration; deliberately outside a tool surface",
    "Messages": "notification plumbing",
    "Metrics": "internal metrics",
    "Organizations": "org administration",
    "PackageCatalog": "internal catalogue",
    "ResourceExecutions": "internal execution bookkeeping",
    "Schema": "internal schema helpers",
    "ServiceDrivers": "service-runtime administration",
    "Settings": "org/project settings administration",
    "Uploader": "internal helper behind Items.upload",
    "Webhooks": "outbound integration plumbing; triggers cover the in-platform case",
    "FUNCTION_END_LINE": "not a repository; a module constant dtlpy exports by accident",
}

#: Explicit tool -> `Repository.method` mappings where names do not line up lexically. Keeps the
#: matcher honest: anything not listed here has to match on its own name.
TOOL_TO_SDK: dict[str, tuple[str, ...]] = {
    "dataset_profile": ("Items.list",),
    "dataset_cluster": ("Features.list", "FeatureSets.list"),
    "dataset_visual_search": ("Features.list",),
    "dataset_embed": ("Models.embed_datasets",),
    "dataset_embeddings_status": ("FeatureSets.list",),
    "marketplace_search": ("Dpks.list",),
    "marketplace_get_dpk": ("Dpks.get",),
    "model_install": ("Apps.install",),
    "app_list": ("Apps.list",),
    "app_uninstall": ("Apps.uninstall",),
    "pipeline_node_add": ("Nodes.add",),
    "pipeline_node_remove": ("Nodes.remove",),
    "pipeline_connect_nodes": ("Nodes.add",),
    "pipeline_create_from_template": ("Pipelines.create",),
    "rag_query": ("Features.list",),
    "rag_pipeline_build": ("Pipelines.create", "Nodes.add"),
    "pii_report": ("Items.list",),
    "pipeline_execute": ("PipelineExecutions.create",),
    "execution_list": ("Executions.list",),
    "execution_terminate": ("Executions.terminate", "Executions.get"),
    "pipeline_validate": ("Pipelines.validate",),
    "pii_set_findings": ("Items.update",),
}

#: Verbs that mean the same thing on either side of the boundary.
VERB_ALIASES = {
    "get": "get",
    "list": "list",
    "create": "create",
    "add": "create",
    "update": "update",
    "set": "update",
    "delete": "delete",
    "remove": "delete",
    "upload": "upload",
    "download": "download",
    "install": "install",
    "uninstall": "uninstall",
    "deploy": "deploy",
    "train": "train",
    "predict": "predict",
    "pause": "pause",
    "reset": "reset",
    "stats": "stats",
    "execute": "execute",
}


def public_methods(repo_class: type) -> list[str]:
    """Public, non-inherited callables on a repository class."""
    return sorted(
        name
        for name, member in vars(repo_class).items()
        if not name.startswith("_")
        and (inspect.isfunction(member) or isinstance(member, staticmethod | classmethod))
    )


def _normalise(name: str) -> tuple[str, str]:
    """Split a tool or method name into (verb, noun), both lowercased."""
    parts = name.lower().split("_")
    for index, part in enumerate(parts):
        if part in VERB_ALIASES:
            noun = "_".join(parts[:index] + parts[index + 1 :])
            return VERB_ALIASES[part], noun
    return "", name.lower()


def tool_names() -> list[str]:
    """Every tool the server can advertise, across all profiles."""
    from doe_mcp.server import PROFILE_ONLY

    async def collect() -> set[str]:
        names = {tool.name for tool in await build_server().list_tools()}
        for module in PROFILE_ONLY:
            from mcp.server import MCPServer

            probe = MCPServer(name="probe", version="0")
            module.register(probe)
            names |= {tool.name for tool in await probe.list_tools()}
        return names

    return sorted(asyncio.run(collect()))


def analyse() -> dict[str, Any]:
    tools = tool_names()
    covered: dict[str, set[str]] = defaultdict(set)

    for tool, targets in TOOL_TO_SDK.items():
        if tool in tools:
            for target in targets:
                repo, _, method = target.partition(".")
                covered[repo].add(method)

    normalised_tools = [(tool, *_normalise(tool)) for tool in tools]

    repositories: dict[str, Any] = {}
    for repo_name in sorted(n for n in dir(dl.repositories) if n[0].isupper()):
        repo_class = getattr(dl.repositories, repo_name)
        if not inspect.isclass(repo_class):
            continue
        methods = public_methods(repo_class)
        if not methods:
            continue

        hits: dict[str, list[str]] = {}
        for method in methods:
            if method in covered.get(repo_name, set()):
                hits[method] = ["explicit mapping"]
                continue
            m_verb, m_noun = _normalise(method)
            singular = repo_name.lower().rstrip("s")
            matching = [
                tool
                for tool, t_verb, t_noun in normalised_tools
                if t_verb and t_verb == m_verb and (singular in t_noun or (m_noun and m_noun in t_noun))
            ]
            if matching:
                hits[method] = matching
        repositories[repo_name] = {
            "methods": methods,
            "covered": hits,
            "uncovered": [m for m in methods if m not in hits],
            "out_of_scope": OUT_OF_SCOPE.get(repo_name),
        }

    in_scope = {k: v for k, v in repositories.items() if not v["out_of_scope"]}
    total = sum(len(v["methods"]) for v in in_scope.values())
    hit = sum(len(v["covered"]) for v in in_scope.values())
    return {
        "tool_count": len(tools),
        "repository_count": len(repositories),
        "in_scope_repositories": len(in_scope),
        "in_scope_methods": total,
        "in_scope_covered": hit,
        "coverage_pct": round(100 * hit / total, 1) if total else 0.0,
        "repositories": repositories,
    }


def print_report(report: dict[str, Any], only: str | None) -> None:
    print(
        f"{report['tool_count']} tools vs {report['repository_count']} dtlpy repositories "
        f"({report['in_scope_repositories']} in scope)"
    )
    print(
        f"In-scope SDK methods: {report['in_scope_covered']}/{report['in_scope_methods']} "
        f"reachable ({report['coverage_pct']}%)\n"
    )

    rows = sorted(
        (
            (name, data)
            for name, data in report["repositories"].items()
            if not data["out_of_scope"] and (only is None or name == only)
        ),
        key=lambda kv: -len(kv[1]["uncovered"]),
    )
    print("Gaps by repository, widest first:\n")
    for name, data in rows:
        n_cov, n_all = len(data["covered"]), len(data["methods"])
        print(f"  {name:20s} {n_cov}/{n_all} covered")
        if data["uncovered"]:
            print(f"    missing: {', '.join(data['uncovered'])}")
        if only:
            for method, by in sorted(data["covered"].items()):
                print(f"    {method} <- {', '.join(by)}")

    if only is None:
        print("\nDeliberately out of scope:")
        for name, data in sorted(report["repositories"].items()):
            if data["out_of_scope"]:
                print(f"  {name:20s} {data['out_of_scope']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--repo", help="show one repository in detail, including what covers it")
    args = parser.parse_args()

    report = analyse()
    if args.json:
        print(json.dumps(report, indent=2, default=list))
    else:
        print_report(report, args.repo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
