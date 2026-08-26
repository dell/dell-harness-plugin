"""MCP Prompts for the three documented workflows in docs/guides/.

Each prompt's text is the guide's own "Appendix B: opening prompt" verbatim, so a host that
surfaces MCP prompts can start one of these sessions with a single pick instead of the user
having to paste text from the docs. The guides are the source of truth — if a guide's Appendix B
changes, update the matching prompt here too; this module does not read the markdown files at
runtime, since docs/ is not part of the installed package (see pyproject.toml's package config).
"""

from mcp.server import MCPServer


def register(mcp: MCPServer) -> None:
    @mcp.prompt(
        title="Explore a dataset",
        description="Read-only walkthrough: list datasets, profile one, and get an executive "
        "summary of its contents. See docs/guides/dataset-exploration-workflow.md.",
    )
    def explore_dataset() -> str:
        return """\
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

    @mcp.prompt(
        title="Find and handle PII",
        description="Human-gated walkthrough: plan first, then scan a dataset for PII, record "
        "findings, and route or exclude flagged items. See docs/guides/pii-detection-workflow.md.",
    )
    def find_and_handle_pii() -> str:
        return """\
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

    @mcp.prompt(
        title="Build a RAG pipeline",
        description="Human-gated walkthrough: plan first, then build a PDF/RAG pipeline with the "
        "direct rag_* tools and query it. See docs/guides/agentic-rag-workflow.md.",
    )
    def build_rag_pipeline() -> str:
        return """\
Using the doe-mcp MCP server, help me build a RAG setup on Dataloop from my PDFs.

Before you change anything: stay in planning mode. Read-only tools are fine — use them to ground
the plan — but do not create, update, delete, install or deploy anything until I say "approved".

Start by telling me which parts of this the server supports directly and which it doesn't. Use
project_list, then for the project I pick, show me: the datasets, any pipelines that already
exist, and the models already installed. Also search the marketplace for RAG pipeline templates
with templates_only=true. Then give me a plan.

Prefer the direct rag_* tools (rag_pipeline_create, rag_add_pdf_filter_node,
rag_add_pdf_to_text_node, rag_add_text_to_chunks_node, rag_add_embedding_node,
rag_add_output_node) over marketplace templates unless I ask for the template path specifically.

Show me the exact PDF-filter code or node configuration before anything runs on Dataloop, and
name every object you will create. Reply "approved" only proceeds the plan I actually agreed to —
honour a narrower approval exactly. Validate on a small dataset before a large one, and tell me
plainly if something I asked for is not supported instead of substituting something else quietly.
"""
