#!/usr/bin/env python3
"""Render the kiosk background so it is the boot splash's final frame, exactly.

The splash does NOT draw wordmark.png at its native 1479x994. taos.script scales
it to `lockup_width_pct` of the panel width and centres it:

    scale = (Window.GetWidth() * lockup_width_pct) / text_image.GetWidth()

swaybg's `center` mode does not scale, so pointing it at the raw wordmark put a
1479px-wide image on a 1080px panel: enlarged and cropped. Observed on the glass
as "an enlarged distorted splash".

So we pre-render a full-panel image with the wordmark at exactly the size and
position plymouth gives it. Then `center` is a no-op and the two frames are
pixel-identical.

The scale factor is READ OUT OF taos.script rather than copied, so the splash
and the background cannot drift apart silently.
"""
import re
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / ".." / ".." / ".." / "plymouth" / "taos" / "taos.script"
WORDMARK = HERE / ".." / ".." / ".." / "plymouth" / "taos" / "wordmark.png"


def read_lockup_width_pct(script: Path) -> float:
    """Pull lockup_width_pct out of taos.script. Fail loudly if it moved."""
    text = script.read_text()
    m = re.search(r"^\s*global\.lockup_width_pct\s*=\s*([0-9.]+)\s*;", text, re.M)
    if not m:
        raise SystemExit(
            f"FAIL: could not find lockup_width_pct in {script}.\n"
            "The splash script changed shape; fix this parser rather than "
            "hardcoding a number, or the background will drift from the splash."
        )
    return float(m.group(1))


def render(width: int, height: int, out: Path) -> None:
    pct = read_lockup_width_pct(SCRIPT.resolve())
    src = Image.open(WORDMARK.resolve()).convert("RGBA")

    target_w = round(width * pct)
    scale = target_w / src.width
    target_h = round(src.height * scale)
    mark = src.resize((target_w, target_h), Image.LANCZOS)

    # True black, matching the splash background and the `#000000` fallback
    # colour in sway-kiosk.conf.
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    canvas.paste(mark, ((width - target_w) // 2, (height - target_h) // 2), mark)
    canvas.convert("RGB").save(out, "PNG", optimize=True)

    print(f"panel            {width}x{height}")
    print(f"lockup_width_pct {pct}  (read from taos.script)")
    print(f"wordmark         {src.width}x{src.height} -> {target_w}x{target_h}")
    print(f"wrote            {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    # spacewar's panel. Passed explicitly so this is reproducible off-device.
    w = int(sys.argv[1]) if len(sys.argv) > 1 else 1080
    h = int(sys.argv[2]) if len(sys.argv) > 2 else 2400
    dest = Path(sys.argv[3]) if len(sys.argv) > 3 else HERE / ".." / "boot-wordmark.png"
    render(w, h, dest.resolve())
