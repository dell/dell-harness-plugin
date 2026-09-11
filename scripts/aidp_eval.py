#!/usr/bin/env python3
"""Validate AIDP golden cases and grade captured agent results."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = ROOT / "evals" / "cases"
FIXTURES_PATH = ROOT / "evals" / "fixtures" / "catalog.json"
REVIEW_FIXTURES = {
    "dell-data-analytics-engine": ROOT / "evals" / "fixtures" / "ddae-review-fixture.json",
    "dell-data-orchestration-engine": ROOT / "evals" / "fixtures" / "dataloop-review-fixture.json",
    "dell-data-search-engine": ROOT / "evals" / "fixtures" / "ddse-review-fixture.json",
}
CASE_FILES = (
    CASES_DIR / "ddae.json",
    CASES_DIR / "ddse.json",
    CASES_DIR / "dataloop.json",
)
KINDS = {"success", "edge", "safety", "non_invocation"}
SUBMISSION_CLASSES = {"positive", "negative", "not_selected"}
INVOCATIONS = {"explicit", "implicit", "ambiguous", "misspelled", "negative"}


class EvalError(ValueError):
    """Raised when an evaluation definition or result is invalid."""


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvalError(f"{path}: {exc}") from exc


def require(value: Any, message: str) -> None:
    if not value:
        raise EvalError(message)


def load_definitions() -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    fixtures_doc = load_json(FIXTURES_PATH)
    fixtures = {item["id"]: item for item in fixtures_doc.get("fixtures", [])}
    require(fixtures, "fixture catalog must contain fixtures")
    documents = [load_json(path) for path in CASE_FILES]
    return fixtures, documents


def iter_variants(document: dict[str, Any]):
    for workflow in document["workflows"]:
        for variant in workflow["variants"]:
            yield workflow, variant


def validate_definitions() -> dict[str, Any]:
    fixtures, documents = load_definitions()
    workflow_ids: set[str] = set()
    variant_ids: set[str] = set()
    summary: dict[str, Any] = {"plugins": {}}

    for document in documents:
        plugin = document.get("plugin")
        fixture_id = document.get("fixture")
        declared = document.get("declared_tools")
        workflows = document.get("workflows")
        require(isinstance(plugin, str), "each case document needs plugin")
        require(fixture_id in fixtures, f"{plugin}: unknown fixture {fixture_id}")
        require(
            fixtures[fixture_id].get("plugin") == plugin,
            f"{plugin}: fixture belongs to another plugin",
        )
        require(
            isinstance(declared, list)
            and declared
            and len(declared) == len(set(declared)),
            f"{plugin}: declared_tools must be a non-empty unique list",
        )
        require(
            isinstance(workflows, list) and 5 <= len(workflows) <= 10,
            f"{plugin}: define 5-10 hero workflows",
        )

        covered: set[str] = set()
        positive_path_tools: set[str] = set()
        invocation_types: set[str] = set()
        submission_counts = {"positive": 0, "negative": 0, "not_selected": 0}

        for workflow in workflows:
            workflow_id = workflow.get("id")
            require(
                isinstance(workflow_id, str) and workflow_id not in workflow_ids,
                f"{plugin}: duplicate or missing workflow id {workflow_id}",
            )
            workflow_ids.add(workflow_id)
            require(workflow.get("title"), f"{workflow_id}: title is required")
            workflow_tools = workflow.get("covered_tools")
            require(
                isinstance(workflow_tools, list) and workflow_tools,
                f"{workflow_id}: covered_tools is required",
            )
            unknown = set(workflow_tools) - set(declared)
            require(
                not unknown, f"{workflow_id}: unknown covered tools {sorted(unknown)}"
            )
            covered.update(workflow_tools)

            variants = workflow.get("variants")
            require(
                isinstance(variants, list) and 2 <= len(variants) <= 3,
                f"{workflow_id}: define 2-3 prompt variants",
            )
            for variant in variants:
                case_id = variant.get("id")
                require(
                    isinstance(case_id, str) and case_id not in variant_ids,
                    f"{workflow_id}: duplicate or missing case id {case_id}",
                )
                variant_ids.add(case_id)
                require(variant.get("prompt"), f"{case_id}: prompt is required")
                require(
                    variant.get("kind") in KINDS,
                    f"{case_id}: invalid kind {variant.get('kind')}",
                )
                submission_class = variant.get("submission_class")
                require(
                    submission_class in SUBMISSION_CLASSES,
                    f"{case_id}: invalid submission_class {submission_class}",
                )
                submission_counts[submission_class] += 1
                invocation = variant.get("invocation")
                require(
                    invocation in INVOCATIONS,
                    f"{case_id}: invalid invocation {invocation}",
                )
                invocation_types.add(invocation)

                for key in (
                    "expected_tool_path",
                    "required_facts",
                    "forbidden_tools",
                    "manual_checks",
                ):
                    require(
                        isinstance(variant.get(key), list),
                        f"{case_id}: {key} must be a list",
                    )
                tool_path = variant["expected_tool_path"]
                unknown_path = set(tool_path) - set(declared)
                unknown_forbidden = set(variant["forbidden_tools"]) - set(declared)
                require(
                    not unknown_path,
                    f"{case_id}: unknown expected tools {sorted(unknown_path)}",
                )
                require(
                    not unknown_forbidden,
                    f"{case_id}: unknown forbidden tools {sorted(unknown_forbidden)}",
                )
                require(
                    not (set(tool_path) & set(variant["forbidden_tools"])),
                    f"{case_id}: a tool cannot be expected and forbidden",
                )
                if variant["kind"] in {"success", "edge"} and tool_path:
                    positive_path_tools.update(tool_path)

        require(
            covered == set(declared),
            f"{plugin}: workflow coverage mismatch; missing "
            f"{sorted(set(declared) - covered)}, extra {sorted(covered - set(declared))}",
        )
        require(
            positive_path_tools == set(declared),
            f"{plugin}: each declared tool needs a success/edge expected path; missing "
            f"{sorted(set(declared) - positive_path_tools)}",
        )
        require(
            submission_counts["positive"] >= 5,
            f"{plugin}: at least five positive submission cases are required",
        )
        require(
            submission_counts["negative"] >= 3,
            f"{plugin}: at least three negative submission cases are required",
        )
        require(
            {"explicit", "implicit", "ambiguous", "misspelled"} <= invocation_types,
            f"{plugin}: explicit, implicit, ambiguous, and misspelled prompts are required",
        )
        policy = document.get("submission_policy")
        if policy is not None:
            require(
                isinstance(policy, dict),
                f"{plugin}: submission_policy must be an object",
            )
            selected_tools = policy.get("selected_tools")
            conditional_groups = policy.get("conditional_tools")
            require(
                isinstance(selected_tools, list)
                and selected_tools
                and len(selected_tools) == len(set(selected_tools)),
                f"{plugin}: submission selected_tools must be non-empty and unique",
            )
            require(
                isinstance(conditional_groups, dict),
                f"{plugin}: conditional_tools must be an object",
            )
            conditional_tools = [
                tool for tools in conditional_groups.values() for tool in tools
            ]
            require(
                all(
                    isinstance(tools, list) and tools
                    for tools in conditional_groups.values()
                ),
                f"{plugin}: every conditional dependency group must contain tools",
            )
            require(
                len(conditional_tools) == len(set(conditional_tools)),
                f"{plugin}: conditional tools must appear in exactly one dependency group",
            )
            require(
                not (set(selected_tools) & set(conditional_tools)),
                f"{plugin}: selected and conditional tools must be disjoint",
            )
            require(
                set(selected_tools) | set(conditional_tools) == set(declared),
                f"{plugin}: selected and conditional tools must partition declared_tools",
            )
            selected_variants = [
                variant
                for _, variant in iter_variants(document)
                if variant["submission_class"] in {"positive", "negative"}
            ]
            selected_path_tools = {
                tool
                for variant in selected_variants
                if variant["submission_class"] == "positive"
                for tool in variant["expected_tool_path"]
            }
            require(
                selected_path_tools == set(selected_tools),
                f"{plugin}: selected positive paths must cover exactly selected_tools",
            )
            require(
                all(
                    not (set(variant["expected_tool_path"]) & set(conditional_tools))
                    for variant in selected_variants
                ),
                f"{plugin}: selected cases cannot invoke conditional tools",
            )
            selected_invocations = {
                variant["invocation"] for variant in selected_variants
            }
            require(
                {"explicit", "implicit", "ambiguous", "misspelled", "negative"}
                <= selected_invocations,
                f"{plugin}: selected cases need explicit, implicit, ambiguous, misspelled, and negative prompts",
            )
        summary["plugins"][plugin] = {
            "workflows": len(workflows),
            "variants": sum(len(item["variants"]) for item in workflows),
            "declared_tools": len(declared),
            "submission_cases": submission_counts,
        }

    return summary


def validate_review_fixture(plugin: str) -> dict[str, Any]:
    validate_definitions()
    path = REVIEW_FIXTURES.get(plugin)
    if path is None:
        raise EvalError(f"{plugin}: no executable review fixture is registered")
    fixture = load_json(path)
    _, documents = load_definitions()
    document = next(item for item in documents if item["plugin"] == plugin)
    require(
        fixture.get("fixture_id") == document["fixture"],
        f"{plugin}: executable fixture ID must match the case definition",
    )
    require(
        fixture.get("data_classification") == "synthetic_non_customer",
        f"{plugin}: review fixture must be synthetic non-customer data",
    )
    expected_cases = {
        variant["id"]
        for _, variant in iter_variants(document)
        if variant["submission_class"] in {"positive", "negative"}
    }
    actual_cases = fixture.get("selected_cases")
    require(
        isinstance(actual_cases, list) and len(actual_cases) == len(set(actual_cases)),
        f"{plugin}: fixture selected_cases must be a unique list",
    )
    require(
        set(actual_cases) == expected_cases,
        f"{plugin}: fixture must cover exactly the selected submission cases",
    )
    policy = document["submission_policy"]
    return {
        "plugin": plugin,
        "fixture": str(path),
        "fixture_id": fixture["fixture_id"],
        "data_classification": fixture["data_classification"],
        "selected_cases": len(actual_cases),
        "positive_cases": sum(
            1
            for _, variant in iter_variants(document)
            if variant["submission_class"] == "positive"
        ),
        "negative_cases": sum(
            1
            for _, variant in iter_variants(document)
            if variant["submission_class"] == "negative"
        ),
        "selected_tools": len(policy["selected_tools"]),
        "conditional_tools": sum(
            len(tools) for tools in policy["conditional_tools"].values()
        ),
        "chatgpt_transcripts": policy["chatgpt_transcripts"],
        "passed": True,
    }


def find_case(case_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    _, documents = load_definitions()
    for document in documents:
        for _, variant in iter_variants(document):
            if variant["id"] == case_id:
                return document, variant
    raise EvalError(f"unknown case_id {case_id}")


def grade_result(path: Path) -> dict[str, Any]:
    validate_definitions()
    result = load_json(path)
    case_id = result.get("case_id")
    document, case = find_case(case_id)
    failures: list[str] = []

    if result.get("fixture") != document["fixture"]:
        failures.append(
            f"fixture must be {document['fixture']}, got {result.get('fixture')}"
        )
    if result.get("surface") not in {"chatgpt", "codex"}:
        failures.append("surface must be chatgpt or codex")

    tool_calls = result.get("tool_calls")
    if not isinstance(tool_calls, list):
        failures.append("tool_calls must be a list")
        actual_path: list[str] = []
    else:
        actual_path = [
            call.get("name") for call in tool_calls if isinstance(call, dict)
        ]
        if len(actual_path) != len(tool_calls) or any(not name for name in actual_path):
            failures.append("every tool call needs a non-empty name")

    if case.get("tool_order") == "any":
        path_matches = sorted(actual_path) == sorted(case["expected_tool_path"])
    else:
        path_matches = actual_path == case["expected_tool_path"]
    if not path_matches:
        failures.append(
            f"tool path must be {case['expected_tool_path']}, got {actual_path}"
        )
    forbidden_used = sorted(set(actual_path) & set(case["forbidden_tools"]))
    if forbidden_used:
        failures.append(f"forbidden tools used: {forbidden_used}")

    final_answer = result.get("final_answer")
    if not isinstance(final_answer, str) or not final_answer.strip():
        failures.append("final_answer must be a non-empty string")
        final_lower = ""
    else:
        final_lower = final_answer.lower()
    missing_facts = [
        fact for fact in case["required_facts"] if fact.lower() not in final_lower
    ]
    if missing_facts:
        failures.append(f"final answer missing required facts: {missing_facts}")

    latency = result.get("latency_ms")
    if not isinstance(latency, (int, float)) or latency < 0:
        failures.append("latency_ms must be a non-negative number")
    for key in ("transcript_url", "audit_event_id"):
        if not isinstance(result.get(key), str) or not result[key].strip():
            failures.append(f"{key} must be a non-empty approved reference")
    manual_checks = result.get("manual_checks")
    if case["manual_checks"]:
        if not isinstance(manual_checks, dict) or not manual_checks:
            failures.append("manual_checks must record the human review")
        elif not all(value is True for value in manual_checks.values()):
            failures.append("all recorded manual checks must pass")

    return {
        "case_id": case_id,
        "passed": not failures,
        "automated_failures": failures,
        "manual_requirements": case["manual_checks"],
    }


def check_contract(
    plugin: str, snapshot_path: Path, review_ready: bool
) -> dict[str, Any]:
    validate_definitions()
    _, documents = load_definitions()
    document = next(item for item in documents if item["plugin"] == plugin)
    snapshot = load_json(snapshot_path)
    tools = snapshot.get("tools")
    if not isinstance(tools, list):
        raise EvalError("snapshot tools must be a list")
    by_name = {
        tool.get("name"): tool
        for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    }
    expected = set(document.get("contract_tools", document["declared_tools"]))
    actual = set(by_name)
    failures: list[str] = []
    if expected - actual:
        failures.append(f"missing tools: {sorted(expected - actual)}")
    if actual - expected:
        failures.append(f"unexpected tools: {sorted(actual - expected)}")

    missing_input = sorted(
        name for name, tool in by_name.items() if not tool.get("inputSchema")
    )
    missing_output = sorted(
        name for name, tool in by_name.items() if not tool.get("outputSchema")
    )
    generic_output = sorted(
        name
        for name, tool in by_name.items()
        if tool.get("outputSchema", {}).get("type") == "object"
        and not tool.get("outputSchema", {}).get("properties")
    )
    required_annotations = {
        "readOnlyHint",
        "destructiveHint",
        "openWorldHint",
    }
    missing_annotations = {
        name: sorted(required_annotations - set(tool.get("annotations", {})))
        for name, tool in by_name.items()
        if required_annotations - set(tool.get("annotations", {}))
    }
    if missing_input:
        failures.append(f"tools without input schemas: {missing_input}")
    if review_ready and missing_output:
        failures.append(f"tools without output schemas: {missing_output}")
    if review_ready and missing_annotations:
        failures.append(f"tools without required annotations: {missing_annotations}")
    if review_ready and plugin == "dell-data-orchestration-engine" and generic_output:
        failures.append(f"tools with generic object outputs: {generic_output}")
    missing_read_idempotency = sorted(
        name
        for name, tool in by_name.items()
        if tool.get("annotations", {}).get("readOnlyHint") is True
        and tool.get("annotations", {}).get("idempotentHint") is not True
    )
    if (
        review_ready
        and plugin == "dell-data-orchestration-engine"
        and missing_read_idempotency
    ):
        failures.append(
            f"read-only tools without idempotentHint=true: {missing_read_idempotency}"
        )

    if plugin == "dell-data-analytics-engine" and "queryReadOnly" in by_name:
        query_schema = by_name["queryReadOnly"].get("inputSchema", {})
        if review_ready and "query" not in query_schema.get("required", []):
            failures.append("queryReadOnly must require query")

    return {
        "plugin": plugin,
        "snapshot": str(snapshot_path),
        "review_ready_mode": review_ready,
        "passed": not failures,
        "failures": failures,
        "observations": {
            "tool_count": len(actual),
            "missing_output_schemas": missing_output,
            "generic_output_schemas": generic_output,
            "read_only_without_idempotency": missing_read_idempotency,
            "missing_required_annotations": missing_annotations,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="validate fixtures and golden cases")
    fixture_parser = subparsers.add_parser(
        "fixture", help="validate one executable synthetic review fixture"
    )
    fixture_parser.add_argument("plugin", choices=tuple(REVIEW_FIXTURES))
    grade_parser = subparsers.add_parser("grade", help="grade one captured result")
    grade_parser.add_argument("result", type=Path)
    contract_parser = subparsers.add_parser(
        "contract",
        help="compare a sanitized tools/list snapshot with the plugin contract",
    )
    contract_parser.add_argument(
        "plugin",
        choices=(
            "dell-data-analytics-engine",
            "dell-data-search-engine",
            "dell-data-orchestration-engine",
        ),
    )
    contract_parser.add_argument("snapshot", type=Path)
    contract_parser.add_argument(
        "--review-ready",
        action="store_true",
        help="also fail on missing output schemas, annotations, and known schema gates",
    )
    args = parser.parse_args()

    try:
        if args.command == "validate":
            output = validate_definitions()
        elif args.command == "fixture":
            output = validate_review_fixture(args.plugin)
        elif args.command == "grade":
            output = grade_result(args.result)
        else:
            output = check_contract(args.plugin, args.snapshot, args.review_ready)
    except EvalError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if output.get("passed", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
