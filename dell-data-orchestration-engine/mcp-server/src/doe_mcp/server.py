# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""doe-mcp entrypoint.

Tool bodies are written as plain synchronous functions on purpose: dtlpy is a blocking,
``requests``-based SDK, and the MCP SDK runs a non-async tool in a worker thread, so a slow
platform call never blocks the server's event loop.
"""

import argparse
import logging
import sys
from types import ModuleType
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from . import __version__, prompts
from .tools import (
    annotations,
    datasets,
    embeddings,
    executions,
    items,
    marketplace,
    models,
    pii,
    pipeline_nodes,
    pipelines,
    projects,
    rag,
    skills,
    triggers,
)

ALL_MODULES: tuple[ModuleType, ...] = (
    projects,
    datasets,
    items,
    annotations,
    pipelines,
    pipeline_nodes,
    marketplace,
    models,
    triggers,
    pii,
    embeddings,
    executions,
    rag,
)

# Plan §4 Phase 3's zero-protocol-risk fallback to dynamic tool gating: `tools/list_changed`
# support is client-contingent and untested (see the plan doc), so this cuts the advertised
# surface by scoping which tools a given `doe-mcp` process registers at all, rather than by
# mutating the served list at runtime. A profile is a *bias* toward one workflow, not a strict
# partition — `projects`/`datasets` are not force-included in every profile, since combining
# `--profile` values (or omitting `--profile` for the full surface) is the intended way to reach
# a tool from another domain.
PROFILES: dict[str, tuple[ModuleType, ...]] = {
    "discovery": (projects, datasets, items, annotations),
    "plugin-v1": ALL_MODULES,
    "pipelines": (pipelines, pipeline_nodes, triggers, executions),
    "models": (marketplace, models, embeddings, pii),
    "rag": (rag, pipelines, pipeline_nodes, marketplace, executions),
    # Prototype (plan §4 Phase 4, reopened on determinism grounds). Deliberately absent from
    # ALL_MODULES: a skill is an *alternative* to the fine-grained node tools, not an addition, so
    # registering it everywhere would grow the default surface while making it more confusing.
    # `rag` is intentionally not included here -- the point of the profile is to measure what a
    # session costs with the skill *instead of* the six rag_add_*_node tools.
    "skills": (skills, projects, datasets, pipelines, marketplace, executions),
}

#: Modules a profile may register that are deliberately absent from `ALL_MODULES`, and so from the
#: default surface. Declared explicitly rather than inferred, so "this profile adds a tool nobody
#: else has" stays a reviewed decision instead of an accident -- `tests/test_profiles.py` allows
#: exactly these to break the otherwise-strict "a profile is a subset of the full surface" rule.
#:
#: `skills` is here because a skill *replaces* the fine-grained node tools rather than adding to
#: them: a session with `rag_pipeline_build` has no use for six `rag_add_*_node` tools, and putting
#: both in the default surface would grow it past its ceiling while making it more confusing. The
#: open question this prototype exists to answer is whether the eventual shape is the inverse --
#: skills in the default surface, node tools moved out to an opt-in profile.
#:
#: `executions` was here for one release for a blunter reason -- no headroom -- and has since been
#: promoted to the default surface, paid for by stripping advisory schema titles (17.8% of the
#: surface, `strip_schema_titles`). Running and unsticking a pipeline is not a specialist activity
#: and does not belong behind a flag.
PROFILE_ONLY: tuple[ModuleType, ...] = (skills,)

# Focused contract shipped by the Dell plugin. It keeps the core discovery, governed workflow,
# rollback, and lifecycle tools together while excluding specialist and prototype surfaces.
PLUGIN_V1_TOOLS = frozenset(
    {
        "project_list",
        "project_get",
        "dataset_list",
        "dataset_get",
        "dataset_profile",
        "item_list",
        "item_get",
        "annotation_labels",
        "annotation_list",
        "annotation_get",
        "pipeline_list",
        "rag_pipeline_create",
        "rag_add_pdf_filter_node",
        "rag_add_pdf_to_text_node",
        "rag_add_text_to_chunks_node",
        "rag_add_embedding_node",
        "rag_add_output_node",
        "pipeline_connect_nodes",
        "pipeline_get",
        "pipeline_validate",
        "pipeline_install",
        "pipeline_pause",
        "pipeline_delete",
        "pipeline_execute",
        "execution_list",
        "execution_terminate",
        "rag_query",
        "pii_report",
        "pii_set_findings",
        "marketplace_search",
        "marketplace_get_dpk",
        "model_install",
        "app_list",
        "model_create",
        "model_set_subset",
        "model_delete_subset",
        "model_train",
        "model_deploy",
        "model_predict",
        "model_get",
        "model_delete",
        "app_uninstall",
    }
)

logger = logging.getLogger("doe_mcp")

INSTRUCTIONS = """\
Manage a DOE account: datasets, pipelines, and marketplace models.

Almost every tool is project-scoped; call `project_list` first for a `project_id` unless given one.

Install a marketplace model as a sequence: `marketplace_search` -> `marketplace_get_dpk` (read its
model components) -> `model_install` (as an app) -> `model_create` (from a component) ->
`model_deploy` (bring it up).

Pipelines are graphs: `pipeline_create`, `pipeline_node_add`, `pipeline_connect_nodes`,
`pipeline_install`.

RAG pipelines process PDFs into searchable chunks, in order: `rag_pipeline_create` ->
`rag_add_pdf_filter_node` (DQL) -> `rag_add_pdf_to_text_node` -> `rag_add_text_to_chunks_node` ->
`rag_add_embedding_node` -> `rag_add_output_node` -> `pipeline_connect_nodes` ->
`pipeline_install` -> `rag_query` to search the result.

Dataset contents: `item_list`, `item_get`, `item_upload`, `item_update`; `annotation_list` shows
what a model or annotator tagged.

`confirm=true` is required for anything that spends money or keeps running:
`pipeline_install`, `model_train`, `model_deploy`, and `trigger_create`.
`model_create` also requires it when configuration overrides are supplied. A
trigger outlives the conversation — say what it will do before creating one,
and check `trigger_list` for automation an earlier session left behind.
"""

PLUGIN_V1_INSTRUCTIONS = """\
Manage authorized DOE datasets, reviewed RAG pipelines, executions, PII findings, and marketplace
models through the focused Dell plugin profile.

Start with `project_list`. Inspect data through `dataset_list`, `dataset_get`,
`dataset_profile`, `item_list`, and `item_get`; inspect labels and exact annotations through
`annotation_labels`, `annotation_list`, and `annotation_get`.

Use `pipeline_list` before creating another workflow. Build RAG in order:
`rag_pipeline_create` -> `rag_add_pdf_filter_node` -> `rag_add_pdf_to_text_node` ->
`rag_add_text_to_chunks_node` -> `rag_add_embedding_node` -> `rag_add_output_node` ->
`pipeline_connect_nodes` -> `pipeline_validate` -> `pipeline_install`. Use
`pipeline_execute` for an approved bounded run, `execution_list` to inspect it, and
`execution_terminate` only for an exact approved execution. `pipeline_pause` stops an installed
pipeline; `pipeline_delete` is permanent and requires confirmation.

Install a marketplace model as `marketplace_search` -> `marketplace_get_dpk` ->
`model_install` -> `model_create` -> `model_deploy`. Apply reviewed training filters with
`model_set_subset`; `model_delete_subset` is its rollback. Anything billable, persistent,
long-running, or destructive needs a visible plan and current user approval. `rag_query` also
asks the user before uploading and embedding its temporary query item.
"""


def resolve_modules(profiles: list[str] | None) -> tuple[ModuleType, ...]:
    """The tool modules a server should register, given zero or more `--profile` values.

    No profiles (``None`` or empty) means every module — today's full surface, unchanged for any
    existing client config that doesn't pass `--profile`. Multiple profiles union their modules,
    de-duplicated, in `ALL_MODULES` order so registration order stays deterministic.
    """
    if not profiles:
        return ALL_MODULES
    unknown = sorted(set(profiles) - set(PROFILES))
    if unknown:
        raise ValueError(
            f"Unknown profile(s): {', '.join(unknown)}. Available profiles: {', '.join(sorted(PROFILES))}."
        )
    wanted = {module for name in profiles for module in PROFILES[name]}
    resolved = [module for module in ALL_MODULES if module in wanted]
    # A profile may name a module that is deliberately absent from ALL_MODULES -- `skills` is,
    # because a skill replaces the fine-grained node tools rather than adding to them, so it must
    # not join the default surface. Filtering by ALL_MODULES alone dropped those silently: the
    # profile resolved, the server started, and the tool simply was not there. Appended after the
    # ALL_MODULES ones, in profile order, so registration order stays deterministic.
    for name in profiles:
        for module in PROFILES[name]:
            if module not in resolved:
                resolved.append(module)
    return tuple(resolved)


def _without_titles(schema: Any) -> Any:
    """A JSON Schema with every advisory `title` removed, recursively.

    Pydantic derives a `title` for every field and model from its name, so the advertised surface
    carries `"title": "Project Id"` next to `"description": "Project that owns the dataset."` —
    the same information, spelled worse, on every parameter of every tool. Measured with a real
    tokenizer it is **4,067 tokens, 17.8% of the whole surface**, and it tells a model nothing.

    `title` is advisory in JSON Schema: nothing validates against it, and argument validation here
    runs through the pydantic model rather than the emitted schema, so removing it cannot change
    what a call accepts. The tool's *own* `title` (the human label in the picker) is a different
    field on the Tool object and is untouched.
    """
    if isinstance(schema, dict):
        return {k: _without_titles(v) for k, v in schema.items() if k != "title"}
    if isinstance(schema, list):
        return [_without_titles(item) for item in schema]
    return schema


def strip_schema_titles(mcp: MCPServer) -> None:
    """Drop advisory titles from every registered tool's schemas.

    Reaches into `_tool_manager._tools`, which is private SDK surface: the MCP server exposes no
    hook for rewriting a schema after registration. `tests/test_tool_surface_budget.py` asserts the
    stripping actually happened, so an SDK change that moves this internal fails loudly there
    rather than silently restoring 4,067 tokens.
    """
    for tool in mcp._tool_manager._tools.values():
        if getattr(tool, "parameters", None):
            tool.parameters = _without_titles(tool.parameters)
        if getattr(tool, "output_schema", None):
            tool.output_schema = _without_titles(tool.output_schema)


def make_tool_annotations_explicit(mcp: MCPServer) -> None:
    """Materialize every optional MCP behavior hint for a stable plugin contract."""
    for tool in mcp._tool_manager._tools.values():
        current = tool.annotations or ToolAnnotations()
        read_only = current.read_only_hint is True
        tool.annotations = ToolAnnotations(
            title=current.title,
            read_only_hint=read_only,
            destructive_hint=current.destructive_hint is True,
            idempotent_hint=(current.idempotent_hint if current.idempotent_hint is not None else read_only),
            open_world_hint=current.open_world_hint is True,
        )


def build_server(profiles: list[str] | None = None) -> MCPServer:
    """Create the server for the selected module profiles or focused plugin surface."""
    selected = set(profiles or [])
    if "plugin-v1" in selected and len(selected) != 1:
        raise ValueError("The plugin-v1 profile cannot be combined with another profile.")

    instructions = PLUGIN_V1_INSTRUCTIONS if selected == {"plugin-v1"} else INSTRUCTIONS
    mcp = MCPServer(
        name="doe-mcp",
        title="DOE",
        instructions=instructions,
        version=__version__,
    )
    for module in resolve_modules(profiles):
        module.register(mcp)
    if selected == {"plugin-v1"}:
        registered = frozenset(mcp._tool_manager._tools)
        missing = PLUGIN_V1_TOOLS - registered
        if missing:
            raise RuntimeError(f"Plugin profile references missing tools: {sorted(missing)}")
        for tool_name in registered - PLUGIN_V1_TOOLS:
            mcp.remove_tool(tool_name)
        make_tool_annotations_explicit(mcp)
    prompts.register(mcp)
    strip_schema_titles(mcp)
    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(prog="doe-mcp", description="DOE MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="MCP transport to serve on (default: stdio)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind host for HTTP transports")
    parser.add_argument("--port", type=int, default=8000, help="bind port for HTTP transports")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="logging verbosity",
    )
    parser.add_argument(
        "--profile",
        action="append",
        choices=sorted(PROFILES),
        default=None,
        help=(
            "Register only this profile's tools; repeat to union several. Omit for the full "
            "tool surface (default). Profiles: " + ", ".join(sorted(PROFILES)) + "."
        ),
    )
    args = parser.parse_args()

    # stdout carries the protocol on stdio, so logs must go to stderr.
    logging.basicConfig(
        level=args.log_level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    mcp = build_server(profiles=args.profile)
    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=args.transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
