#!/usr/bin/env python3
"""Render the taOS mobile boot wordmark for the Plymouth theme.

The lockup is generated rather than hand-drawn so the one thing Jay specified
exactly -- "mobile" set to the WIDTH of the larger "taOS" -- is measured, not
eyeballed. Width matching is done on the rendered pixels (the ink bounding box),
not on font metrics, because a font's advance width includes side bearings that
"taOS" and "mobile" do not share.

Two images come out of this, on one shared canvas so the theme cannot drift:

  wordmark.png       white lockup on transparency
  wordmark-glow.png  the same lockup, blurred, for the glow halo behind it

Both are the same size, so the theme scales and centres them identically and the
halo can never sit off-register from the text.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Rendered oversized; the theme scales down to the panel. 1080-wide phone, so
# ~2x headroom means the splash stays sharp and can survive a taller device.
TAOS_TARGET_W = 1200

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

TAOS_TEXT = "taOS"
SUB_TEXT = "mobile"

# Tracking on the subtitle only. It reads as deliberate rather than merely
# shrunken, and it is what makes the width match look like a lockup.
SUB_TRACKING = 0.22

# Gap between the baseline of taOS and the top of "mobile", as a fraction of the
# taOS ink height.
GAP_RATIO = 0.30

GLOW_BLUR = 26
PAD = 140  # room for the blur to fall off inside the canvas


def ink_box(text: str, font: ImageFont.FreeTypeFont, tracking: float = 0.0):
    """Render to a scratch canvas and return (image, ink bbox) of the glyphs."""
    probe = Image.new("L", (4000, 1200), 0)
    d = ImageDraw.Draw(probe)
    if tracking:
        # Draw glyph by glyph so tracking is real spacing, not a fake.
        x = 100.0
        space = font.size * tracking
        for ch in text:
            d.text((x, 100), ch, font=font, fill=255)
            x += d.textlength(ch, font=font) + space
    else:
        d.text((100, 100), text, font=font, fill=255)
    return probe, probe.getbbox()


def fit_font(text: str, target_w: int, tracking: float = 0.0) -> ImageFont.FreeTypeFont:
    """Binary-search the font size whose INK width matches target_w."""
    lo, hi = 8, 900
    best = ImageFont.truetype(FONT, lo)
    while lo <= hi:
        mid = (lo + hi) // 2
        f = ImageFont.truetype(FONT, mid)
        _, bbox = ink_box(text, f, tracking)
        w = bbox[2] - bbox[0]
        if w <= target_w:
            best, lo = f, mid + 1
        else:
            hi = mid - 1
    return best


def layer(text: str, font: ImageFont.FreeTypeFont, tracking: float = 0.0) -> Image.Image:
    """A tightly cropped white-on-transparent image of the text."""
    probe, bbox = ink_box(text, font, tracking)
    return probe.crop(bbox)


def main() -> int:
    out_dir = Path(__file__).resolve().parent.parent / "taos"
    out_dir.mkdir(parents=True, exist_ok=True)

    taos_font = fit_font(TAOS_TEXT, TAOS_TARGET_W)
    taos = layer(TAOS_TEXT, taos_font)

    # The subtitle is fitted to the MEASURED ink width of taOS, so the two are
    # the same width by construction rather than by a guessed font size.
    sub_font = fit_font(SUB_TEXT, taos.width, SUB_TRACKING)
    sub = layer(SUB_TEXT, sub_font, SUB_TRACKING)

    # Font sizes are integers, so the search lands a pixel or two under. Resample
    # the last couple of pixels away: "same width" should mean equal, not close.
    if sub.width != taos.width:
        sub = sub.resize((taos.width, round(sub.height * taos.width / sub.width)), Image.LANCZOS)

    gap = int(taos.height * GAP_RATIO)
    inner_w = max(taos.width, sub.width)
    inner_h = taos.height + gap + sub.height

    canvas = Image.new("L", (inner_w + PAD * 2, inner_h + PAD * 2), 0)
    canvas.paste(taos, (PAD + (inner_w - taos.width) // 2, PAD))
    canvas.paste(sub, (PAD + (inner_w - sub.width) // 2, PAD + taos.height + gap))

    # White, with the drawn coverage as alpha.
    white = Image.new("RGBA", canvas.size, (255, 255, 255, 255))
    white.putalpha(canvas)
    white.save(out_dir / "wordmark.png")

    # The halo: same canvas, blurred, so it stays in register with the text.
    glow = Image.new("RGBA", canvas.size, (255, 255, 255, 255))
    glow.putalpha(canvas.filter(ImageFilter.GaussianBlur(GLOW_BLUR)))
    glow.save(out_dir / "wordmark-glow.png")

    print(f"taOS   ink: {taos.width}x{taos.height}px  (font {taos_font.size})")
    print(f"mobile ink: {sub.width}x{sub.height}px  (font {sub_font.size}, tracking {SUB_TRACKING})")
    print(f"width delta: {abs(taos.width - sub.width)}px")
    print(f"canvas: {canvas.size[0]}x{canvas.size[1]}")
    print(f"wrote {out_dir}/wordmark.png and wordmark-glow.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
