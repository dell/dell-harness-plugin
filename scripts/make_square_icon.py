# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Build each plugin's square listing icon from the brand-approved Dell Technologies logo.

The logo is centered, unmodified except for scaling, on an opaque white square, so the
listing shows the approved mark at the size the directory expects.

Usage: python scripts/make_square_icon.py PATH_TO_APPROVED_LOGO   (requires Pillow)
"""
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SIZE = 512
MARGIN = 48


def build(logo: Image.Image, plugin_dir: Path) -> Path:
    width = SIZE - 2 * MARGIN
    mark = logo.resize((width, max(1, round(logo.height * width / logo.width))), Image.LANCZOS)
    canvas = Image.new("RGBA", (SIZE, SIZE), (255, 255, 255, 255))
    canvas.alpha_composite(mark, ((SIZE - mark.width) // 2, (SIZE - mark.height) // 2))
    out = plugin_dir / "assets" / "dell-technologies-icon-square.png"
    canvas.convert("RGB").save(out, optimize=True)
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    approved = Image.open(sys.argv[1]).convert("RGBA")
    for manifest in sorted(ROOT.glob("dell-*/.codex-plugin/plugin.json")):
        print(build(approved, manifest.parent.parent).relative_to(ROOT))
