# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Build each plugin's square listing icon from its existing wordmark.

The wordmark is centered, unmodified except for scaling, on an opaque white square.
This is a layout derivation, not a new brand asset: replace the output with the
brand-approved square icon when one is available.

Usage: python scripts/make_square_icon.py   (requires Pillow)
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SIZE = 512
MARGIN = 36


def build(plugin_dir: Path) -> Path:
    mark = Image.open(plugin_dir / "assets" / "dell-technologies-wordmark.png").convert("RGBA")
    width = SIZE - 2 * MARGIN
    mark = mark.resize((width, max(1, round(mark.height * width / mark.width))), Image.LANCZOS)
    canvas = Image.new("RGBA", (SIZE, SIZE), (255, 255, 255, 255))
    canvas.alpha_composite(mark, ((SIZE - mark.width) // 2, (SIZE - mark.height) // 2))
    out = plugin_dir / "assets" / "dell-technologies-icon-square.png"
    canvas.convert("RGB").save(out, optimize=True)
    return out


if __name__ == "__main__":
    for manifest in sorted(ROOT.glob("dell-*/.codex-plugin/plugin.json")):
        print(build(manifest.parent.parent).relative_to(ROOT))
