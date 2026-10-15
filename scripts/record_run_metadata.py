# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Write the run record a validation run must carry, and validate per-case results.

A validation result is only reviewable if it says exactly what was tested. This
records the package hash, plugin version, git commit, runtime and library
versions, effective profile, tool-catalog hash, model, effort, client, fixture
and limits. Per-case results keep run status (did it finish) apart from quality
(was it right), as the reviewers asked.

    python scripts/record_run_metadata.py --plugin dell-data-orchestration-engine \
        --profile plugin-v1 --model MODEL --effort EFFORT --client NAME=VERSION \
        --fixture FIXTURE_ID --tool-catalog tools.json --out run-record.json

No credential, hostname, or local path is recorded.
"""
import argparse
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_STATUSES = ("completed", "timeout", "step_limit", "error")
QUALITY_STATUSES = ("pass", "fail", "not_graded")
LIBRARIES = ("mcp", "dtlpy", "pydantic")
SKIPPED = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", ".git"}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_hash(plugin_dir: Path) -> tuple[str, int]:
    """Hash of every shipped file's relative path and content, order-independent of the OS."""
    digest = hashlib.sha256()
    files = sorted(
        p for p in plugin_dir.rglob("*")
        if p.is_file() and not (SKIPPED & set(p.relative_to(plugin_dir).parts)) and p.suffix != ".pyc"
    )
    for path in files:
        digest.update(path.relative_to(plugin_dir).as_posix().encode("utf-8") + b"\0")
        digest.update(sha256_file(path).encode("ascii") + b"\n")
    return digest.hexdigest(), len(files)


def catalog_summary(path: Path) -> dict:
    """Hash a tools/list capture and keep its names; the raw capture stays with the evidence."""
    data = json.loads(path.read_text(encoding="utf-8"))
    tools = data.get("tools", data) if isinstance(data, dict) else data
    names = sorted(tool["name"] for tool in tools)
    canonical = json.dumps(tools, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"count": len(names), "sha256": hashlib.sha256(canonical).hexdigest(), "names": names}


def git_state(root: Path) -> dict:
    def run(*args):
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False).stdout.strip()

    return {"commit": run("rev-parse", "HEAD") or None, "dirty": bool(run("status", "--porcelain"))}


def library_versions() -> dict:
    found = {}
    for name in LIBRARIES:
        try:
            found[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            found[name] = None
    return found


def build_record(args, root: Path = ROOT) -> dict:
    plugin_dir = root / args.plugin
    manifest = json.loads((plugin_dir / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    digest, file_count = package_hash(plugin_dir)
    record = {
        "recorded_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "plugin": manifest["name"],
        "plugin_version": manifest["version"],
        "package_sha256": digest,
        "package_file_count": file_count,
        "git": git_state(root),
        "runtime": {"python": platform.python_version(), "os": platform.system(), "libraries": library_versions()},
        "effective_profile": args.profile,
        "client": args.client,
        "model": args.model,
        "effort": args.effort,
        "service_tier": args.service_tier,
        "fixture": args.fixture,
        "identity": args.identity,
        "limits": {"max_seconds_per_case": args.max_seconds, "max_steps_per_case": args.max_steps},
    }
    if args.tool_catalog:
        record["tool_catalog"] = catalog_summary(Path(args.tool_catalog))
    return record


def validate_case_result(result: dict) -> list[str]:
    """Problems with one per-case record; empty when it is well-formed."""
    problems = []
    for key in ("case_id", "run_status", "quality", "evidence"):
        if key not in result:
            problems.append(f"missing {key}")
    if result.get("run_status") not in RUN_STATUSES:
        problems.append(f"run_status must be one of {RUN_STATUSES}")
    if result.get("quality") not in QUALITY_STATUSES:
        problems.append(f"quality must be one of {QUALITY_STATUSES}")
    if result.get("run_status") != "completed" and result.get("quality") == "pass":
        problems.append("a run that did not complete cannot pass")
    if result.get("quality") in ("pass", "fail") and not result.get("evidence"):
        problems.append("a graded result needs evidence")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plugin", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--client", required=True, help="client name and version, e.g. codex=1.2.3")
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True)
    parser.add_argument("--service-tier", default="not observable")
    parser.add_argument("--fixture", required=True, help="fixture or disposable-environment identifier")
    parser.add_argument("--identity", required=True, help="role of the test identity, never a credential")
    parser.add_argument("--max-seconds", type=int, default=600)
    parser.add_argument("--max-steps", type=int, default=40)
    parser.add_argument("--tool-catalog", help="tools/list capture (JSON) from the effective server")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    record = build_record(args)
    Path(args.out).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
