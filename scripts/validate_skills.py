# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Lint every plugin skill: frontmatter, routing exclusions, size, adapters.

Usage: python scripts/validate_skills.py [repo-root]
Exits non-zero and prints one line per problem.
"""
import re
import sys
from pathlib import Path

import yaml

MAX_SKILL_LINES = 200
MAX_DESCRIPTION_CHARS = 1024
MIN_DESCRIPTION_CHARS = 60
EXCLUSION = re.compile(r"\b(do not use|does not|never use|not for)\b", re.IGNORECASE)
NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
PLACEHOLDER = re.compile(r"<!--|TODO|TBD|lorem ipsum", re.IGNORECASE)


def split_frontmatter(text: str):
    match = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        return None, text
    return yaml.safe_load(match.group(1)), text[match.end():]


def skill_dirs(root: Path):
    return sorted(p.parent for p in root.glob("dell-*/skills/*/SKILL.md"))


def check_skill(skill_dir: Path):
    problems = []
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    meta, body = split_frontmatter(text)
    if not isinstance(meta, dict):
        return ["missing or invalid YAML frontmatter"]
    name = meta.get("name")
    description = " ".join(str(meta.get("description", "")).split())
    if name != skill_dir.name:
        problems.append(f"frontmatter name {name!r} must equal directory {skill_dir.name!r}")
    if not isinstance(name, str) or not NAME.match(name):
        problems.append("name must be lowercase kebab-case")
    if not MIN_DESCRIPTION_CHARS <= len(description) <= MAX_DESCRIPTION_CHARS:
        problems.append(
            f"description must be {MIN_DESCRIPTION_CHARS}-{MAX_DESCRIPTION_CHARS} chars, got {len(description)}"
        )
    if not re.search(r"\buse\b", description, re.IGNORECASE):
        problems.append("description must say when to use the skill")
    if not EXCLUSION.search(description):
        problems.append("description must say when NOT to use the skill ('Do not use for ...')")
    lines = text.count("\n") + 1
    if lines > MAX_SKILL_LINES:
        problems.append(f"{lines} lines exceeds {MAX_SKILL_LINES}; move detail into references/")
    if PLACEHOLDER.search(body):
        problems.append("contains a template comment or placeholder")
    if not re.search(r"^# \S", body, re.MULTILINE):
        problems.append("missing top-level heading")
    adapter = skill_dir / "agents" / "openai.yaml"
    if not adapter.is_file():
        problems.append("missing agents/openai.yaml")
    else:
        interface = (yaml.safe_load(adapter.read_text(encoding="utf-8")) or {}).get("interface", {})
        for key in ("display_name", "short_description", "default_prompt"):
            if not interface.get(key):
                problems.append(f"agents/openai.yaml missing interface.{key}")
        if interface.get("default_prompt") and f"${skill_dir.name}" not in interface["default_prompt"]:
            problems.append(f"agents/openai.yaml default_prompt must mention ${skill_dir.name}")
    for ref in re.findall(r"\]\((references/[^)#]+)", body):
        if not (skill_dir / ref).is_file():
            problems.append(f"broken reference link {ref}")
    return problems


def main(argv):
    root = Path(argv[1] if len(argv) > 1 else Path(__file__).resolve().parents[1])
    failed = 0
    dirs = skill_dirs(root)
    if not dirs:
        print("no skills found", file=sys.stderr)
        return 1
    for skill_dir in dirs:
        for problem in check_skill(skill_dir):
            failed += 1
            print(f"{skill_dir.relative_to(root)}: {problem}")
    print(f"checked {len(dirs)} skills, {failed} problem(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
