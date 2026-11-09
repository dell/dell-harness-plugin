# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Offline checks of the bundle builder; CI's bundle job builds and boots the real packages."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("build_plugin_bundle", ROOT / "scripts/build_plugin_bundle.py")
assert SPEC and SPEC.loader
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


def test_runtimes_are_pinned_by_release_version_and_digest():
    assert re.fullmatch(r"\d{8}", BUILDER.RUNTIME_RELEASE)
    for spec in BUILDER.RUNTIMES.values():
        assert BUILDER.RUNTIME_VERSION in spec["asset"] and BUILDER.RUNTIME_RELEASE in spec["asset"]
        assert re.fullmatch(r"[0-9a-f]{64}", spec["sha256"])
        assert spec["python"].startswith("./runtime/")


def test_every_local_mcp_plugin_is_bundleable_and_ships_portable_manifests():
    for plugin, spec in BUILDER.PLUGINS.items():
        root = ROOT / plugin
        assert spec["requirements"](root)
        server = json.loads((root / "mcp.json").read_text())["mcpServers"][spec["server"]]
        assert server["type"] == "stdio" and server["cwd"] == "${PLUGIN_ROOT}"
        assert all(arg.startswith("${PLUGIN_ROOT}/") for arg in server["args"])
        assert json.loads((root / "plugin.json").read_text())["name"] == plugin


def test_package_excludes_tests_evidence_and_bytecode():
    for plugin in BUILDER.PLUGINS:
        files = BUILDER.tracked_files(plugin)
        assert files and Path("mcp.json") in files and Path("plugin.json") in files
        assert not any(BUILDER.EXCLUDED_PARTS.intersection(path.parts) for path in files)
