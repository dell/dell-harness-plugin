# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("validate_skills", ROOT / "scripts/validate_skills.py")
validate_skills = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validate_skills)

SKILLS = validate_skills.skill_dirs(ROOT)


def test_every_plugin_ships_skills():
    assert len(SKILLS) >= 11


@pytest.mark.parametrize("skill_dir", SKILLS, ids=lambda p: p.name)
def test_skill_passes_lint(skill_dir):
    assert validate_skills.check_skill(skill_dir) == []


def make_skill(tmp_path, description, body="# Title\n", adapter=True, prompt="Profile my demo data with $demo-skill."):
    skill = tmp_path / "dell-x" / "skills" / "demo-skill"
    (skill / "agents").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: demo-skill\ndescription: {description}\n---\n\n{body}", encoding="utf-8"
    )
    if adapter:
        (skill / "agents/openai.yaml").write_text(
            'interface:\n  display_name: "D"\n  short_description: "S"\n'
            f'  default_prompt: "{prompt}"\n',
            encoding="utf-8",
        )
    return skill


GOOD = "Investigate demo data for the user. Use for demo requests. Do not use for unrelated work."


def test_linter_accepts_a_well_formed_skill(tmp_path):
    assert validate_skills.check_skill(make_skill(tmp_path, GOOD)) == []


def test_linter_rejects_the_template_sample_prompt(tmp_path):
    skill = make_skill(tmp_path, GOOD, prompt="Use $demo-skill to help me with my data.")
    assert any("template" in p for p in validate_skills.check_skill(skill))


def test_linter_requires_an_exclusion_clause(tmp_path):
    skill = make_skill(tmp_path, "Investigate demo data for the user. Use for demo requests and reports.")
    assert any("NOT" in p for p in validate_skills.check_skill(skill))


def test_linter_requires_the_codex_adapter(tmp_path):
    skill = make_skill(tmp_path, GOOD, adapter=False)
    assert any("openai.yaml" in p for p in validate_skills.check_skill(skill))


def test_linter_rejects_template_comments_and_oversize(tmp_path):
    skill = make_skill(tmp_path, GOOD, body="# T\n<!-- keep short -->\n" + "x\n" * 250)
    problems = validate_skills.check_skill(skill)
    assert any("placeholder" in p for p in problems)
    assert any("exceeds" in p for p in problems)


def test_linter_rejects_name_directory_mismatch(tmp_path):
    skill = make_skill(tmp_path, GOOD)
    text = (skill / "SKILL.md").read_text().replace("name: demo-skill", "name: other")
    (skill / "SKILL.md").write_text(text)
    assert any("must equal directory" in p for p in validate_skills.check_skill(skill))


@pytest.mark.parametrize("skill_dir", SKILLS, ids=lambda p: p.name)
def test_skill_treats_tool_output_as_data(skill_dir):
    text = " ".join((skill_dir / "SKILL.md").read_text(encoding="utf-8").split())
    assert "never as instructions" in text, "add the untrusted-data line after the opening paragraph"
