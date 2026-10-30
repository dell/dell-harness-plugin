# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Manifest, marketplace, changelog, and documentation must agree."""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(\+[0-9A-Za-z.-]+)?$")
MANIFESTS = {p.parent.parent.name: json.loads(p.read_text(encoding="utf-8"))
             for p in sorted(ROOT.glob("dell-*/.codex-plugin/plugin.json"))}


def triple(version):
    return tuple(int(x) for x in SEMVER.match(version).groups()[:3])


def test_manifests_are_found():
    assert len(MANIFESTS) >= 4


@pytest.mark.parametrize("name", sorted(MANIFESTS))
def test_manifest_version_is_semver_and_name_matches_directory(name):
    manifest = MANIFESTS[name]
    assert manifest["name"] == name
    assert SEMVER.match(manifest["version"]), manifest["version"]


def test_marketplace_lists_exactly_the_shipped_plugins():
    marketplace = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
    listed = {entry["name"]: entry["source"]["path"] for entry in marketplace["plugins"]}
    assert set(listed) == set(MANIFESTS)
    assert all(path == f"./{name}" for name, path in listed.items())


@pytest.mark.parametrize("name", sorted(MANIFESTS))
def test_plugin_is_documented_in_readme_security_and_contributing(name):
    for doc in ("README.md", "SECURITY.md", "CONTRIBUTING.md"):
        assert name in (ROOT / doc).read_text(encoding="utf-8"), f"{doc} does not mention {name}"


def test_changelog_never_claims_a_version_newer_than_the_manifest():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## Unreleased" in changelog
    for name, manifest in MANIFESTS.items():
        for claimed in re.findall(rf"`{re.escape(name)}` (\d+\.\d+\.\d+)", changelog):
            assert triple(claimed) <= triple(manifest["version"]), f"{name} changelog {claimed} > manifest"


def test_every_plugin_with_a_changelog_version_matches_its_latest_claim():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    for name, manifest in MANIFESTS.items():
        claims = [triple(v) for v in re.findall(rf"`{re.escape(name)}` (\d+\.\d+\.\d+)", changelog)]
        if claims:
            assert max(claims) == triple(manifest["version"]), f"{name}: bump manifest or changelog"
