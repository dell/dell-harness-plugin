"""doe-mcp entrypoint.

Tool bodies are written as plain synchronous functions on purpose: dtlpy is a blocking,
``requests``-based SDK, and the MCP SDK runs a non-async tool in a worker thread, so a slow
platform call never blocks the server's event loop.
"""

import argparse
import logging
import sys
from types import ModuleType

from mcp.server import MCPServer

from . import __version__, prompts
from .tools import (
    annotations,
    datasets,
    embeddings,
    items,
    marketplace,
    models,
    pii,
    pipeline_nodes,
    pipelines,
    projects,
    rag,
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
    "pipelines": (pipelines, pipeline_nodes, triggers),
    "models": (marketplace, models, embeddings, pii),
    "rag": (rag, pipelines, pipeline_nodes, marketplace),
}

# Focused surface used by the Dell plugin. The full server remains available when
# no profile is selected, preserving compatibility for existing SDK users.
PLUGIN_V1_TOOLS = frozenset(
    {
        "project_list",
        "project_get",
        "dataset_list",
        "dataset_get",
        "dataset_profile",
        "item_list",
        "annotation_labels",
        "annotation_list",
        "rag_pipeline_create",
        "rag_add_pdf_filter_node",
        "rag_add_pdf_to_text_node",
        "rag_add_text_to_chunks_node",
        "rag_add_embedding_node",
        "rag_add_output_node",
        "pipeline_connect_nodes",
        "pipeline_get",
        "pipeline_install",
        "rag_query",
        "pii_report",
        "pii_set_findings",
        "marketplace_search",
        "marketplace_get_dpk",
        "model_install",
        "app_list",
        "model_create",
        "model_set_subset",
        "model_train",
        "model_deploy",
        "model_predict",
        "model_get",
        "model_delete",
        "app_uninstall",
    }
)

FULL_TOOL_NAMES = frozenset(
    {
        "project_list",
        "project_get",
        "dataset_list",
        "dataset_get",
        "dataset_create",
        "dataset_update",
        "dataset_delete",
        "dataset_profile",
        "item_list",
        "item_get",
        "item_upload",
        "item_update",
        "annotation_list",
        "annotation_get",
        "annotation_labels",
        "annotation_create",
        "annotation_delete",
        "annotation_delete_by_model",
        "pipeline_list",
        "pipeline_get",
        "pipeline_create",
        "pipeline_create_from_template",
        "pipeline_delete",
        "pipeline_install",
        "pipeline_pause",
        "pipeline_reset",
        "pipeline_stats",
        "pipeline_update_settings",
        "pipeline_node_add",
        "pipeline_node_update",
        "pipeline_node_remove",
        "pipeline_connect_nodes",
        "pipeline_disconnect_nodes",
        "marketplace_search",
        "marketplace_get_dpk",
        "model_install",
        "app_list",
        "model_create",
        "model_deploy",
        "model_train",
        "model_predict",
        "model_set_subset",
        "model_delete_subset",
        "model_list",
        "model_get",
        "model_delete",
        "app_uninstall",
        "trigger_create",
        "trigger_list",
        "trigger_get",
        "trigger_delete",
        "pii_report",
        "pii_set_findings",
        "pii_writeback_code",
        "pii_filter",
        "dataset_embeddings_status",
        "dataset_embed",
        "dataset_cluster",
        "dataset_visual_search",
        "rag_pipeline_create",
        "rag_add_pdf_filter_node",
        "rag_add_pdf_to_text_node",
        "rag_add_text_to_chunks_node",
        "rag_add_embedding_node",
        "rag_add_output_node",
        "rag_query",
    }
)

logger = logging.getLogger("doe_mcp")

INSTRUCTIONS = """\
Manage a Dataloop account: datasets, pipelines, and marketplace models.

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

`confirm=true` is required for anything that spends money or keeps running: `model_train`,
`model_deploy`, `trigger_create`. A trigger outlives the conversation — say what it will do before
creating one, and check `trigger_list` for automation an earlier session left behind.
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
    return tuple(module for module in ALL_MODULES if module in wanted)


def build_server(profiles: list[str] | None = None) -> MCPServer:
    """Create the server for the selected module profiles or focused plugin surface."""
    selected = set(profiles or [])
    if "plugin-v1" in selected and len(selected) != 1:
        raise ValueError("The plugin-v1 profile cannot be combined with another profile.")

    mcp = MCPServer(
        name="doe-mcp",
        title="Dataloop",
        instructions=INSTRUCTIONS,
        version=__version__,
    )
    for module in resolve_modules(profiles):
        module.register(mcp)
    if selected == {"plugin-v1"}:
        for tool_name in FULL_TOOL_NAMES - PLUGIN_V1_TOOLS:
            mcp.remove_tool(tool_name)
    prompts.register(mcp)
    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(prog="doe-mcp", description="Dataloop MCP server")
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
