# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""MCP Prompts for the three evaluated plugin workflows.

The constants are module-level so the live prompt and matching golden trace use
the same instruction text. Full operating guidance is provided by the plugin
skills; the installed Python package does not depend on repository-only docs.
"""

from mcp.server import MCPServer

EXPLORE_DATASET_PROMPT = """\
Using the doe-mcp MCP server, help me understand what is in one of my datasets.

Start read-only. Ask me which project rather than guessing, then list the datasets with their
item counts and let me pick one.

Once I pick one, profile it and tell me what kinds of files it holds, then give me an executive
summary. Be explicit about which parts you observed and which you inferred from filenames — and
if the profile only scanned a sample, say so rather than quoting the sample as a total.

If the filenames are uninformative, you may offer to group the items by similarity — check
whether embeddings already exist before proposing to extract any, tell me what extraction costs,
and when you report clusters, do not name them until you have looked at their example items.

Do not run any model over the data unless I ask. If I do ask, show me the models already
installed in this project and let me choose, tell me what it will cost and that predictions get
written back onto the items as annotations, and run over a small sample before anything larger.
"""

FIND_AND_HANDLE_PII_PROMPT = """\
Using the doe-mcp MCP server, help me find out whether a dataset contains PII and then deal with it.

Phase 0 — planning only. Read-only tools are fine; do not create, upload, update, delete, install,
deploy or run anything until I reply "approved". In this phase:
  1. Tell me which parts of this the server supports directly and which it does not — in
     particular, whether it detects PII itself and whether it can move items between datasets.
  2. Call project_list, then for the project I pick show datasets with item counts and any
     existing pipelines.
  3. Search the marketplace with templates_only=true for a PII / Presidio solution and show me
     what each package provides.
  4. Propose a numbered plan naming every object you would create and the exact writeback code
     you would deploy.

Then stop and wait. I may approve only part of the plan; honour that scope exactly.

Ask me rather than guessing which dataset, project or model I mean — including for read-only
calls. Before any dataset-level answer, verify the marker convention against one item I tell you
contains PII. Never report a zero result as "clean" without saying why it might not be, and never
describe copying items to a quarantine dataset as "moving" them.
"""

BUILD_RAG_PIPELINE_PROMPT = """\
Using the doe-mcp MCP server, help me build a RAG setup on DOE from my PDFs.

Phase 0 — planning only. Read-only tools are fine; do not create, update, delete, install or
deploy anything until I reply "approved". In this phase:
  1. Tell me which parts of this the server supports directly and which it does not.
  2. Call project_list, then for the project I pick show datasets, existing pipelines, and the
     models already installed (app_list, model_list).
  3. Propose a plan using the direct RAG tools (rag_pipeline_create, rag_add_pdf_filter_node,
     rag_add_pdf_to_text_node, rag_add_text_to_chunks_node, rag_add_embedding_node,
     rag_add_output_node). If those don't fit, propose marketplace templates or hand-built
     pipelines as alternatives.
  4. Name every object you would create, the output dataset the text items, chunk items and
     vectors would land in, the chunk configuration you would use, and which already-deployed
     model you would embed with (by id) — or say plainly that no deployed model is available.

Then stop and wait. I may approve only part of the plan; honour that scope exactly.
After approval, work one phase at a time and show me each pipeline graph before running it.
Tell me plainly whenever something I ask for is not supported instead of substituting something else.
"""


def register(mcp: MCPServer) -> None:
    @mcp.prompt(
        title="Explore a dataset",
        description="Read-only walkthrough: list datasets, profile one, and get an executive "
        "summary of its contents. Use the dataset exploration skill for full guidance.",
    )
    def explore_dataset() -> str:
        return EXPLORE_DATASET_PROMPT

    @mcp.prompt(
        title="Find and handle PII",
        description="Human-gated walkthrough: plan first, then scan a dataset for PII, record "
        "findings, and route or exclude flagged items. Use the PII skill for full guidance.",
    )
    def find_and_handle_pii() -> str:
        return FIND_AND_HANDLE_PII_PROMPT

    @mcp.prompt(
        title="Build a RAG pipeline",
        description="Human-gated walkthrough: plan first, then build a PDF/RAG pipeline with the "
        "direct rag_* tools and query it. Use the RAG skill for full guidance.",
    )
    def build_rag_pipeline() -> str:
        return BUILD_RAG_PIPELINE_PROMPT
