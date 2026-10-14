#!/usr/bin/env python
# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Run DOE golden traces against an operator-provided model and a live platform.

The model adapter must implement `doe_mcp.eval.EvalModel`. Without one, the
harness fails closed instead of silently skipping evaluation.

Usage:
    export DATALOOP_API_KEY=... DATALOOP_PATH=~/.doe-mcp/dataloop
    python scripts/doe_eval.py --project-id <id> --model mypackage.mymodule:MyModel [--trace ID]...

Use only a disposable DOE project because traces can create pipelines and
datasets. Billable operations are declined by default. Pass `--allow-billable`
only for an approved test project and identity.

Exit codes: 0 all passed, 1 an eval failed, 2 usage or configuration error,
3 the run itself broke and says nothing about the agent.
"""

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import mcp_types as types
from mcp import Client

from doe_mcp.eval import (
    GOLDEN_TRACES,
    EvalModel,
    GoldenTrace,
    HarnessError,
    ModelNotConfiguredError,
    TraceResult,
    criteria_baseline,
    load_model,
    run_trace,
)
from doe_mcp.server import build_server

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_BROKEN = 0, 1, 2, 3


def _select_traces(trace_ids: list[str] | None) -> list[GoldenTrace]:
    if not trace_ids:
        return list(GOLDEN_TRACES)
    by_id = {trace.id: trace for trace in GOLDEN_TRACES}
    unknown = [trace_id for trace_id in trace_ids if trace_id not in by_id]
    if unknown:
        raise SystemExit(f"Unknown trace id(s): {', '.join(unknown)}. Known: {', '.join(by_id)}")
    return [by_id[trace_id] for trace_id in trace_ids]


# Parameter names matter: mcp's ElicitationFnT is a Protocol whose __call__ declares
# (context, params) as positional-or-keyword, so renaming them breaks structural conformance.
async def _decline_billable(context: Any, params: Any) -> types.ElicitResult:
    """Refuse every elicitation, so an unattended run cannot approve its own billable ops."""
    return types.ElicitResult(action="decline")


async def _accept_billable(context: Any, params: Any) -> types.ElicitResult:
    return types.ElicitResult(action="accept", content={"confirm": True})


async def _run_all(
    traces: list[GoldenTrace],
    model: EvalModel,
    project_id: str,
    *,
    repeats: int,
    allow_billable: bool,
    on_result: Any = None,
) -> list[TraceResult]:
    system_prompt = (
        f"You are driving the doe-mcp MCP server against DOE project {project_id!r}. Use "
        "that project_id for every tool call unless the user's intent names a different one."
    )
    results: list[TraceResult] = []
    callback = _accept_billable if allow_billable else _decline_billable
    async with Client(build_server(), elicitation_callback=callback) as client:
        for trace in traces:
            for _ in range(repeats):
                result = await run_trace(trace, model, client, system_prompt=system_prompt)
                results.append(result)
                # Handed over as each one finishes: a crash on the last trace of a run that
                # created real resources must not discard what the earlier ones measured.
                if on_result is not None:
                    on_result(result)
    return results


def _print_report(results: list[TraceResult], repeats: int) -> None:
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        error_rate = f"{result.sdk_error_rate:.0%}"
        print(
            f"{status}  {result.trace_id:20s} {result.turns_used:3d} turns  "
            f"{result.sdk_errors} sdk errors ({error_rate})  ~{result.usage.total} tok"
        )
        if result.error_kinds and result.sdk_errors:
            kinds = ", ".join(f"{kind}={count}" for kind, count in result.error_kinds.items() if count)
            print(f"      errors: {kinds}")
        if not result.passed:
            print(f"      {result.failure_reason}")

    if repeats > 1:
        print("\npass rate")
        by_trace: dict[str, list[bool]] = {}
        for result in results:
            by_trace.setdefault(result.trace_id, []).append(result.passed)
        for trace_id, passes in by_trace.items():
            pass_rate = statistics.mean(1.0 if ok else 0.0 for ok in passes)
            print(f"  {trace_id:20s} {sum(passes)}/{len(passes)}  ({pass_rate:.0%})")


def _git_revision() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _write_artifact(path: Path, results: list[TraceResult], args: argparse.Namespace) -> None:
    """Persist the run, including the criteria each result was scored against.

    Without this a run leaves nothing behind but stdout, so "it failed against the old trace and
    passes against the new one" is a claim with no artifact behind it — and a CI regression gate
    has no baseline to compare to.
    """
    payload = {
        "run": {
            "timestamp": datetime.now(UTC).isoformat(),
            "git_revision": _git_revision(),
            "model": args.model,
            "project_id": args.project_id,
            "repeats": args.repeat,
            "allow_billable": args.allow_billable,
        },
        "results": [result.to_dict() for result in results],
    }
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    print(f"\nwrote {path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--project-id", help="A disposable DOE project id.")
    parser.add_argument("--model", help="'package.module:ClassName' implementing EvalModel.")
    parser.add_argument(
        "--trace", action="append", dest="trace_ids", help="Run only this trace id; repeatable."
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Run each trace this many times and report a pass rate. Pass@1 over n=1 is not a rate.",
    )
    parser.add_argument("--out", type=Path, help="Write a JSON artifact of the run here.")
    parser.add_argument(
        "--allow-billable",
        action="store_true",
        help="Accept elicited confirmations. Without this, billable operations are declined.",
    )
    parser.add_argument(
        "--dump-criteria",
        type=Path,
        metavar="PATH",
        help=(
            "Write the golden traces' scoring criteria to PATH and exit. Regenerates the baseline "
            "CI diffs against; needs no model, project or credentials."
        ),
    )
    args = parser.parse_args()

    # Runs without a model or project on purpose: this is the one mode CI can execute.
    if args.dump_criteria:
        args.dump_criteria.write_text(json.dumps(criteria_baseline(), indent=2) + "\n")
        print(f"wrote {args.dump_criteria}")
        return EXIT_OK

    missing = [
        flag for flag, value in (("--project-id", args.project_id), ("--model", args.model)) if not value
    ]
    if missing:
        print(f"error: {' and '.join(missing)} required (or use --dump-criteria)", file=sys.stderr)
        return EXIT_USAGE

    # Cheap validation first: a typo'd --trace should not cost the user's adapter __init__, which
    # is where a provider client and API key get built.
    if args.repeat < 1:
        print("error: --repeat must be at least 1", file=sys.stderr)
        return EXIT_USAGE
    try:
        traces = _select_traces(args.trace_ids)
    except SystemExit as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    try:
        model = load_model(args.model)
    except ModelNotConfiguredError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    completed: list[TraceResult] = []
    try:
        results = asyncio.run(
            _run_all(
                traces,
                model,
                args.project_id,
                repeats=args.repeat,
                allow_billable=args.allow_billable,
                on_result=completed.append,
            )
        )
    except HarnessError as exc:
        print(f"error: the run broke before finishing: {exc}", file=sys.stderr)
        _print_report(completed, args.repeat)
        if args.out:
            _write_artifact(args.out, completed, args)
        return EXIT_BROKEN

    _print_report(results, args.repeat)
    if args.out:
        _write_artifact(args.out, results, args)
    return EXIT_OK if all(result.passed for result in results) else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
