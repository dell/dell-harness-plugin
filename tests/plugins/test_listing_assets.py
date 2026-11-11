# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import json
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MANIFESTS = sorted(ROOT.glob("dell-*/.codex-plugin/plugin.json"))


@pytest.mark.parametrize('manifest', MANIFESTS, ids=lambda p: p.parent.parent.name)
def test_listing_text_fits_submission_limits(manifest):
    interface = json.loads(manifest.read_text(encoding='utf-8'))['interface']
    assert 1 <= len(interface['displayName']) <= 30
    assert 1 <= len(interface['shortDescription']) <= 30


def png_size(path: Path):
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    return struct.unpack(">II", header[16:24])


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda p: p.parent.parent.name)
def test_listing_icons_exist_and_are_square(manifest):
    interface = json.loads(manifest.read_text(encoding="utf-8"))["interface"]
    for key in ("composerIcon", "logo"):
        path = manifest.parent.parent / interface[key]
        assert path.is_file(), f"{key} {interface[key]} is missing"
        width, height = png_size(path)
        assert width == height and width >= 256, f"{key} must be a square PNG of at least 256px"
