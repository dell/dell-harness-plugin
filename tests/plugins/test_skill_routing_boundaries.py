# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Static routing guards between sibling data-product skills.

Codex picks a skill from its description text, so a skill that does not name
what it excludes, or an evaluation prompt that leaks another product's
vocabulary into a positive case, is a routing regression. These checks are
deterministic; live routing is still measured by the evaluation runners.
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Vocabulary that belongs to exactly one product.
PRODUCT_TERMS = {
    "ddae": ("starburst", "trino", "data product"),
    "ddse": ("elastic", "kibana", "es|ql", "elasticsearch"),
    "dataloop": ("dataloop",),
}
PLUGIN_PRODUCT = {
    "dell-data-analytics-engine": "ddae",
    "dell-data-search-engine": "ddse",
    "dell-data-orchestration-engine": "dataloop",
}
CASE_FILES = {"ddae": "ddae.json", "ddse": "ddse.json", "dataloop": "dataloop.json"}


def description(skill_md: Path) -> str:
    text = skill_md.read_text(encoding="utf-8")
    block = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL).group(1)
    return " ".join(re.search(r"description:(.*?)(?=\n\w+:|\Z)", block, re.DOTALL).group(1).replace(">", "", 1).split())


def exclusion_clause(desc: str) -> str:
    match = re.search(r"(do not use|does not)\b.*", desc, re.IGNORECASE)
    return match.group(0).lower() if match else ""


@pytest.mark.parametrize("plugin", ["dell-data-analytics-engine", "dell-data-search-engine"])
def test_sibling_search_and_analytics_skills_exclude_each_other(plugin):
    other = "ddse" if PLUGIN_PRODUCT[plugin] == "ddae" else "ddae"
    skills = sorted((ROOT / plugin / "skills").glob("*/SKILL.md"))
    assert skills
    for skill in skills:
        clause = exclusion_clause(description(skill))
        assert any(term in clause for term in PRODUCT_TERMS[other]), (
            f"{skill.parent.name} must name {other} vocabulary in its 'Do not use' clause"
        )


def positive_prompts(product):
    data = json.loads((ROOT / "evals/cases" / CASE_FILES[product]).read_text(encoding="utf-8"))
    for workflow in data["workflows"]:
        for variant in workflow["variants"]:
            if variant["kind"] in ("success", "edge") and variant.get("invocation") != "negative":
                yield variant["id"], variant["prompt"]


@pytest.mark.parametrize("product", sorted(CASE_FILES))
def test_positive_eval_prompts_do_not_use_another_products_vocabulary(product):
    foreign = [t for p, terms in PRODUCT_TERMS.items() if p != product for t in terms]
    leaks = []
    for case_id, prompt in positive_prompts(product):
        lowered = prompt.lower()
        leaks += [(case_id, t) for t in foreign if re.search(rf"\b{re.escape(t)}", lowered)]
    assert leaks == []


@pytest.mark.parametrize("product", sorted(CASE_FILES))
def test_every_product_has_a_non_invocation_or_ambiguous_routing_case(product):
    data = json.loads((ROOT / "evals/cases" / CASE_FILES[product]).read_text(encoding="utf-8"))
    variants = [v for w in data["workflows"] for v in w["variants"]]
    assert any(v["kind"] == "non_invocation" or v.get("invocation") in ("negative", "ambiguous") for v in variants)


@pytest.mark.parametrize(
    ("setup_skill", "analysis_skill"),
    [
        ("dell-data-analytics-engine/skills/configure-dell-data-analytics-engine", "analyze-governed-data-products"),
        ("dell-data-search-engine/skills/configure-dell-data-search-engine", "investigate-dell-search-data"),
    ],
)
def test_setup_skills_hand_analysis_requests_to_the_analysis_skill(setup_skill, analysis_skill):
    desc = description(ROOT / setup_skill / "SKILL.md")
    use_clause = re.split(r"\bdo not use\b", desc, flags=re.IGNORECASE)[0].lower()
    for claim in ("data product search", "read-only trino sql", "index search", "es|ql", "security investigation"):
        assert claim not in use_clause, f"{setup_skill} claims analysis work: {claim!r}"
    assert analysis_skill in exclusion_clause(desc)
