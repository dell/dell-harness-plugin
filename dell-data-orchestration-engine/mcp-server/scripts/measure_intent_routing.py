#!/usr/bin/env python
"""How hard is it to route a plain-English request to the right doe-mcp tool?

The question behind a possible fine-tuned router: **is intent→tool matching actually a problem?**
Nothing measured so far says it is — Phase 5's one clear failure was an infrastructure race, not a
mis-routed request — and the plan's Phase 6 gate exists to stop building a training pipeline for an
unmeasured problem.

This gives the floor. It routes each intent with a deliberately stupid lexical matcher: bag-of-words
cosine over the tool's advertised name, title and description, with inverse-document weighting so
words common to every tool count for little. No model, no credentials, no network.

Read the number this way:

* **High baseline accuracy** — the tool surface is self-describing enough that a bag of words finds
  the right tool. A fine-tuned router is then hard to justify: a frontier model in-context will do
  at least this well, and the gap it could close is small.
* **Low baseline accuracy** — routing is genuinely hard, either because the surface is ambiguous or
  because intents do not share vocabulary with descriptions. That is a measured problem, and it
  also says *where*: the per-intent misses name the confusable tools.

Either way the misses are the useful output. A tool that keeps stealing other intents is a
description defect this repo can fix for free, which is cheaper than training anything.

Usage:
    python scripts/measure_intent_routing.py
    python scripts/measure_intent_routing.py --json
    python scripts/measure_intent_routing.py --misses   # only what it got wrong
"""

import argparse
import asyncio
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from doe_mcp.routing import LexicalRouter, catalogue_from  # noqa: E402
from doe_mcp.server import PROFILE_ONLY, build_server  # noqa: E402

#: (request, tool that should handle it). Provenance matters, so it is marked per entry:
#: `trace` — the intent comes from a golden trace in `doe_mcp.eval.traces`, expected tool is that
#:   trace's first required tool, i.e. what the workflow actually opens with.
#: `guide` — phrasing drawn from a `docs/guides/*.md` walkthrough.
#: `written` — written for this file. These are the weakest evidence: they are one person's guess
#:   at how a user phrases things, and a corpus authored by the same person who wrote the tool
#:   descriptions will flatter the baseline. Treat the `written` subtotal as an upper bound.
CORPUS: list[tuple[str, str, str]] = [
    ("what projects do I have", "project_list", "trace"),
    ("show me the datasets in this project", "dataset_list", "trace"),
    ("what kinds of files are in this dataset", "dataset_profile", "trace"),
    ("find a PII detection solution in the marketplace", "marketplace_search", "trace"),
    ("does this dataset contain personal information", "pii_report", "trace"),
    ("build me a RAG pipeline from my PDFs", "rag_pipeline_build", "trace"),
    ("list the models installed in this project", "model_list", "guide"),
    ("what items are in this dataset", "item_list", "guide"),
    ("group the images by what they look like", "dataset_cluster", "guide"),
    ("search my images for pictures of a red car", "dataset_visual_search", "guide"),
    ("ask a question over my document chunks", "rag_query", "guide"),
    ("deploy this model so it can serve predictions", "model_deploy", "guide"),
    ("train this model on my labelled data", "model_train", "guide"),
    ("run the model over these items", "model_predict", "guide"),
    ("start the pipeline", "pipeline_install", "guide"),
    ("upload these files", "item_upload", "guide"),
    ("make a new dataset", "dataset_create", "written"),
    ("delete this dataset", "dataset_delete", "written"),
    ("rename a node in my pipeline", "pipeline_node_update", "written"),
    ("wire the output of one node into another", "pipeline_connect_nodes", "written"),
    ("show me the pipeline's graph", "pipeline_get", "written"),
    ("how is my pipeline doing", "pipeline_stats", "written"),
    ("stop the pipeline from running", "pipeline_pause", "written"),
    ("add a label to this image", "annotation_create", "written"),
    ("what has this model annotated", "annotation_list", "written"),
    ("remove everything this model labelled", "annotation_delete_by_model", "written"),
    ("automatically run this when new data arrives", "trigger_create", "written"),
    ("what automations are set up", "trigger_list", "written"),
    ("install this app from the marketplace", "model_install", "written"),
    ("does this dataset have embeddings yet", "dataset_embeddings_status", "written"),
    ("extract feature vectors for this dataset", "dataset_embed", "written"),
    ("which items did the scan flag", "pii_filter", "written"),
    # Execution control, added with the tools that serve it (Phase 6a). Without these the four new
    # tools were pure distractors in the measurement -- present in the catalogue, wanted by no
    # intent -- which is a measurement artefact, not a routing regression.
    ("run my pipeline now", "pipeline_execute", "written"),
    ("stop the stuck job", "execution_terminate", "written"),
    ("what is still running", "execution_list", "written"),
    ("check my pipeline is valid before I start it", "pipeline_validate", "written"),
]


async def _catalogue() -> dict[str, str]:
    """Every advertised tool's routing text, across all profiles."""
    entries = dict(catalogue_from(await build_server().list_tools()))
    for module in PROFILE_ONLY:
        from mcp.server import MCPServer

        probe = MCPServer(name="probe", version="0")
        module.register(probe)
        entries.update(catalogue_from(await probe.list_tools()))
    return entries


def analyse() -> dict[str, Any]:
    catalogue = asyncio.run(_catalogue())
    # The same router the intent_route tool uses, so this measures the shipped implementation
    # rather than a second copy that could drift from it.
    router = LexicalRouter()

    results = []
    for intent, expected, source in CORPUS:
        top = [candidate.name for candidate in router.route(intent, catalogue, 5).candidates]
        results.append(
            {
                "intent": intent,
                "expected": expected,
                "source": source,
                "top1": top[0] if top else None,
                "top5": top,
                "hit1": bool(top) and top[0] == expected,
                "hit3": expected in top[:3],
                "expected_exists": expected in catalogue,
            }
        )

    def pct(key: str, rows: list[dict[str, Any]]) -> float:
        return round(100 * sum(r[key] for r in rows) / len(rows), 1) if rows else 0.0

    by_source = {
        source: {
            "n": len([r for r in results if r["source"] == source]),
            "top1": pct("hit1", [r for r in results if r["source"] == source]),
            "top3": pct("hit3", [r for r in results if r["source"] == source]),
        }
        for source in ("trace", "guide", "written")
    }
    return {
        "tool_count": len(catalogue),
        "corpus_size": len(results),
        "top1_accuracy": pct("hit1", results),
        "top3_accuracy": pct("hit3", results),
        "by_source": by_source,
        "unknown_expected": [r["expected"] for r in results if not r["expected_exists"]],
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--misses", action="store_true", help="only show what it routed wrongly")
    args = parser.parse_args()

    report = analyse()
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"Lexical baseline router over {report['tool_count']} tools, {report['corpus_size']} intents")
    print(f"  top-1: {report['top1_accuracy']}%    top-3: {report['top3_accuracy']}%\n")
    print("  by provenance (written entries flatter the baseline -- see CORPUS):")
    for source, stats in report["by_source"].items():
        print(f"    {source:8s} n={stats['n']:2d}  top-1 {stats['top1']:5.1f}%  top-3 {stats['top3']:5.1f}%")

    if report["unknown_expected"]:
        print(f"\n  corpus names tools that do not exist: {report['unknown_expected']}")

    print("\nMisses (what a router would have to fix):")
    for row in report["results"]:
        if row["hit1"]:
            continue
        marker = "~" if row["hit3"] else "x"
        print(f"  {marker} {row['intent']!r}")
        print(f"      wanted {row['expected']}, got {row['top1']}  |  top5: {', '.join(row['top5'][:5])}")
    if not args.misses:
        print("\n  '~' = right tool was in the top 3, so ranking not recall is the problem.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
