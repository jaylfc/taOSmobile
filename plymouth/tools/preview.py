#!/usr/bin/env python3
"""Render a preview of the taOS boot splash.

This is a DESIGN preview, not a test of the theme: it re-implements the glow
curve in Python. It cannot prove the Plymouth script runs. What it can do is
avoid lying about the numbers, so it PARSES the globals out of taos.script
rather than repeating them -- change glow_period there and the preview follows.

Outputs an animated GIF of one full breath plus a still at peak glow.
"""

import re
import sys
from pathlib import Path

from PIL import Image

THEME = Path(__file__).resolve().parent.parent / "taos"
SCRIPT = THEME / "taos.script"

# Panel size of nothing-spacewar.
PANEL = (1080, 2400)
# Previews are downscaled so the GIF is a sane size to send.
PREVIEW_SCALE = 0.45
FRAMES = 48


def globals_from_script(text: str) -> dict:
    """Pull `global.name = <number>;` out of the theme script."""
    found = dict(re.findall(r"global\.(\w+)\s*=\s*(-?[\d.]+)\s*;", text))
    return {k: float(v) for k, v in found.items()}


def smooth_pulse(t: float, period: float) -> float:
    """Mirror of smooth_pulse() in taos.script."""
    u = (t / period) * 2
    if u > 1:
        u = 2 - u
    return u * u * (3 - 2 * u)


def main() -> int:
    if not SCRIPT.exists():
        print(f"missing {SCRIPT}", file=sys.stderr)
        return 1

    g = globals_from_script(SCRIPT.read_text())
    required = [
        "lockup_width_pct", "glow_period",
        "text_min_opacity", "text_max_opacity",
        "glow_min_opacity", "glow_max_opacity",
    ]
    missing = [k for k in required if k not in g]
    if missing:
        print(f"taos.script did not yield: {missing}", file=sys.stderr)
        return 1

    text_src = Image.open(THEME / "wordmark.png").convert("RGBA")
    glow_src = Image.open(THEME / "wordmark-glow.png").convert("RGBA")

    w = int(PANEL[0] * PREVIEW_SCALE)
    h = int(PANEL[1] * PREVIEW_SCALE)

    scale = (w * g["lockup_width_pct"]) / text_src.width
    size = (max(1, round(text_src.width * scale)), max(1, round(text_src.height * scale)))
    text_img = text_src.resize(size, Image.LANCZOS)
    glow_img = glow_src.resize(size, Image.LANCZOS)

    x = (w - size[0]) // 2
    y = (h - size[1]) // 2

    def at(opacity: float, img: Image.Image) -> Image.Image:
        faded = img.copy()
        a = faded.getchannel("A").point(lambda v: int(v * max(0.0, min(1.0, opacity))))
        faded.putalpha(a)
        return faded

    frames = []
    for i in range(FRAMES):
        t = (i / FRAMES) * g["glow_period"]
        p = smooth_pulse(t, g["glow_period"])
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        canvas.alpha_composite(
            at(g["glow_min_opacity"] + (g["glow_max_opacity"] - g["glow_min_opacity"]) * p, glow_img),
            (x, y),
        )
        canvas.alpha_composite(
            at(g["text_min_opacity"] + (g["text_max_opacity"] - g["text_min_opacity"]) * p, text_img),
            (x, y),
        )
        frames.append(canvas.convert("RGB"))

    gif = THEME / "preview.gif"
    still = THEME / "preview.png"
    ms = int((g["glow_period"] / FRAMES) * 1000)
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=ms, loop=0, optimize=True)
    frames[FRAMES // 2].save(still)

    print(f"glow_period={g['glow_period']}s  lockup={g['lockup_width_pct'] * 100:.0f}% of width")
    print(f"text {g['text_min_opacity']}->{g['text_max_opacity']}, glow {g['glow_min_opacity']}->{g['glow_max_opacity']}")
    print(f"wrote {gif} ({FRAMES} frames @ {ms}ms) and {still}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
