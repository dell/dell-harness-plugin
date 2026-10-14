# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The routing measurement's own corpus must be trustworthy.

Regression: one `CORPUS` entry was tagged `"trace"` provenance -- "expected tool is that trace's
first required tool, i.e. what the workflow actually opens with" -- but named `rag_pipeline_build`,
a `--profile skills`-only tool no golden trace or guide ever calls for that intent; the trace and
its guide prompt both open with `rag_pipeline_create` instead. That silently corrupted the
"trace"-source subtotal and the headline top-1 number quoted in `docs/eval.md` and both specs.
Nothing caught it because `CORPUS` itself was untested.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from measure_intent_routing import CORPUS, analyse  # noqa: E402

from doe_mcp.eval.traces import GOLDEN_TRACES  # noqa: E402

ALL_REQUIRED_TOOLS = {name for trace in GOLDEN_TRACES for name in trace.required_tools}

#: Tools no golden trace calls, but which a `"trace"`-tagged intent could plausibly be mislabelled
#: with because their names read almost identically to one that *is* required. The original bug was
#: exactly this: `rag_pipeline_build` (a `--profile skills` tool) standing in for
#: `rag_pipeline_create`. Membership-in-the-union alone would not have caught a swap *between* two
#: required tools, so this names the confusable pairs explicitly.
CONFUSABLE_WITH_REQUIRED = {"rag_pipeline_build": "rag_pipeline_create"}


def test_every_trace_tagged_entry_names_a_tool_a_golden_trace_actually_requires():
    for intent, expected, source in CORPUS:
        if source != "trace":
            continue
        assert expected not in CONFUSABLE_WITH_REQUIRED, (
            f"{intent!r} expects {expected!r}, which no golden trace calls — it is the "
            f"easily-confused twin of {CONFUSABLE_WITH_REQUIRED.get(expected)!r}."
        )
        assert expected in ALL_REQUIRED_TOOLS, (
            f"{intent!r} is tagged 'trace' and expects {expected!r}, but no golden trace's "
            f"required_tools names that tool. Either the expected tool is wrong, or the "
            f"provenance tag should be 'written' instead."
        )


def test_trace_tagged_entries_are_not_all_pinned_to_the_same_opening_tool():
    """The corpus comment says a `"trace"` entry's expected tool is "that trace's first required
    tool". Taken literally that is `project_list` for all three traces, which is not what the
    corpus does — the entries are drawn from various phases of each guide. Pin the looser property
    the corpus actually holds to, so the comment and the data stop disagreeing silently.
    """
    trace_expected = {expected for _, expected, source in CORPUS if source == "trace"}

    assert len(trace_expected) > 1, "trace-tagged intents should cover more than one tool"
    assert trace_expected <= ALL_REQUIRED_TOOLS


def test_every_corpus_entry_names_a_tool_that_actually_exists():
    """`analyse()` already computes this; pin it here so a stale entry fails loudly in CI rather
    than only showing up as a quiet line in `measure_intent_routing.py`'s own printed output.
    """
    report = analyse()

    assert report["unknown_expected"] == []


def test_corpus_has_no_duplicate_intents():
    intents = [intent for intent, _, _ in CORPUS]

    assert len(intents) == len(set(intents))
