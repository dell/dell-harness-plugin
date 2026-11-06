# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Repository-wide guards for the public release.

Hostnames are checked against an allowlist of public or reserved example
domains, so this file never needs to name a private host in order to block it.
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGINS = (
    "dell-ai-factory-agentic-workspace",
    "dell-data-analytics-engine",
    "dell-data-search-engine",
    "dell-data-orchestration-engine",
)
LICENSE_ID = "Apache-2.0"
COPYRIGHT = "Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved."
# REUSE-IgnoreStart
SPDX = f"SPDX-License-Identifier: {LICENSE_ID}"
# REUSE-IgnoreEnd
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip", ".pyc"}
SKIPPED_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules",
                ".mypy_cache", ".ruff_cache", ".pytest_cache"}

# Public domains the documentation legitimately links to, plus RFC 2606/6761
# reserved names used for examples and fixtures.
ALLOWED_DOMAINS = (
    "dell.com",
    "github.com",
    "githubusercontent.com",
    "openai.com",
    "chatgpt.com",
    "dataloop.ai",
    "pydantic.dev",
    "apache.org",
    "developercertificate.org",
    "contributor-covenant.org",
    "keepachangelog.com",
    "semver.org",
    "conventionalcommits.org",
    # Agent Plugins manifest schema URIs used by the portable plugin.json and mcp.json.
    "agent-plugins.org",
    # Kubernetes / Eclipse Che API groups used by the legacy Dev Spaces backend.
    "k8s.io",
    "devfile.io",
    "eclipse.org",
    "example.com",
    "example.net",
    "example.org",
    "example",
    "test",
    "invalid",
    "localhost",
)
# Dotted identifiers that look like hostnames but are not (e.g. PowerShell [System.IO]).
NOT_HOSTS = {"system.io"}
URL_HOST = re.compile(r"https?://([A-Za-z0-9.-]+)")
BARE_HOST = re.compile(r"\b((?:[a-z0-9-]+\.)+(?:com|net|org|io|ai|dev|cloud|tech))\b")
FORBIDDEN_PHRASES = (
    "internal ca",
    "internal local codex plugin",
    "internal dell repository",
    "dell network access",
    "private dell network",
    # Pilot-lab network access and identifiers must not ship.
    "private tunnel",
    "private-network tunnel",
    "socks5://",
    "the validated pilot used",
    "ctolab",
    "cto lab",
)
# Absolute paths from the machine that generated a file (evidence, logs, transcripts).
LOCAL_PATH = re.compile(r"(/app/projects/|/home/[A-Za-z0-9_.-]+/|/Users/[A-Za-z0-9_.-]+/|[A-Za-z]:\\\\Users\\\\)")


def repository_files():
    try:
        listed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        paths = [ROOT / name for name in listed]
    except (OSError, subprocess.CalledProcessError):
        paths = list(ROOT.rglob("*"))
    return sorted(
        path for path in paths
        if path.is_file()
        and not any(part in SKIPPED_DIRS or part.startswith(".venv") for part in path.relative_to(ROOT).parts)
        and path.suffix.lower() not in BINARY_SUFFIXES
    )


def text_files():
    for path in repository_files():
        try:
            yield path, path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue


def allowed(host):
    host = host.lower().rstrip(".")
    if host in NOT_HOSTS or host in {"127.0.0.1", "0.0.0.0"}:
        return True
    return any(host == domain or host.endswith("." + domain) for domain in ALLOWED_DOMAINS)


def test_only_public_or_example_hostnames_are_referenced():
    violations = []
    for path, text in text_files():
        hosts = set(URL_HOST.findall(text)) | set(BARE_HOST.findall(text.lower()))
        for host in sorted(hosts):
            # Dotted Python/package identifiers such as "doe_mcp.tools.items" are not hosts.
            if "." in host.strip(".") and not allowed(host):
                violations.append(f"{path.relative_to(ROOT)}: {host}")
    assert not violations, "Non-public hostnames found:\n" + "\n".join(violations)


def test_documentation_has_no_internal_distribution_language():
    violations = [
        f"{path.relative_to(ROOT)}: {phrase}"
        for path, text in text_files()
        if path != Path(__file__).resolve()
        for phrase in FORBIDDEN_PHRASES
        if re.search(rf"\b{re.escape(phrase)}\b", text.lower())
    ]
    assert not violations, "\n".join(violations)


def test_no_local_machine_paths_are_published():
    violations = [
        f"{path.relative_to(ROOT)}: {match.group(0)}"
        for path, text in text_files()
        if path != Path(__file__).resolve()
        for match in LOCAL_PATH.finditer(text)
    ]
    assert not violations, "Local filesystem paths found:\n" + "\n".join(violations)


def test_repository_ships_only_the_four_supported_plugins():
    marketplace = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
    assert tuple(entry["name"] for entry in marketplace["plugins"]) == PLUGINS
    manifests = sorted(path.parents[1].name for path in ROOT.glob("*/.codex-plugin/plugin.json"))
    assert manifests == sorted(PLUGINS)


@pytest.mark.parametrize("plugin", PLUGINS)
def test_manifest_declares_the_repository_license(plugin):
    manifest = json.loads((ROOT / plugin / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    assert manifest["license"] == LICENSE_ID


def test_license_and_community_files_are_present():
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in license_text and "Version 2.0, January 2004" in license_text
    assert "Dell Inc. or its subsidiaries" in (ROOT / "NOTICE").read_text(encoding="utf-8")
    assert (ROOT / "LICENSES/Apache-2.0.txt").read_text(encoding="utf-8") == license_text
    for name in ("REUSE.toml", "CONTRIBUTING.md", "CONTRIBUTORS.md", "CODE_OF_CONDUCT.md", "SECURITY.md",
                 "MAINTAINERS.md", "docs/RELEASE_CHECKLIST.md", ".github/pull_request_template.md",
                 ".github/ISSUE_TEMPLATE/config.yml"):
        assert (ROOT / name).is_file(), name
    assert "Signed-off-by" in (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")


def test_local_configuration_patterns_are_ignored():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for pattern in (".env", ".env.*", ".devin/mcp_config.local.json", "mcp_config.local.json", "*.local.json"):
        assert pattern in ignore, f".gitignore must exclude {pattern}"


def test_every_source_file_carries_the_copyright_header():
    missing = []
    for path in repository_files():
        # REUSE.toml is licensing metadata itself, not a source file.
        if path.suffix not in {".py", ".ps1", ".psm1", ".cmd", ".toml"} or path.name == "REUSE.toml":
            continue
        head = "\n".join(path.read_text(encoding="utf-8-sig").splitlines()[:5])
        if COPYRIGHT not in head or SPDX not in head:
            missing.append(str(path.relative_to(ROOT)))
    assert not missing, "Missing copyright/SPDX header:\n" + "\n".join(missing)
