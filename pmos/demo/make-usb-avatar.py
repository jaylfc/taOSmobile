#!/usr/bin/env python3
"""Draw the taOSusb Agent's lock-screen avatar: a USB stick.

Jay: "change their avatar to a flash drive/usb stick type image". GENERATED at
install time rather than committed, for the same reason as the demo icons: no
binary in the repo to drift. The lock screen serves avatars from
/var/lib/taos/lock-avatars/<slug>.jpg, the slug being the agent's name
lowercased with non-alphanumerics collapsed to "-", so "taOSusb Agent" ->
taosusb-agent.jpg. 128x128 like the others; drawn at 4x and scaled down so the
edges are smooth.

Usage: make-usb-avatar.py [OUT]   (default: /var/lib/taos/lock-avatars/taosusb-agent.jpg)
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else
           "/var/lib/taos/lock-avatars/taosusb-agent.jpg")
S = 512   # working size; saved at 128


def vgradient(w, h, top, bottom):
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        for x in range(w):
            px[x, y] = c
    return img


def hgradient(w, h, left, right):
    return vgradient(h, w, left, right).rotate(90, expand=True).transpose(Image.FLIP_LEFT_RIGHT)


# Background: the lock screen's near-black, with a soft accent glow behind the
# stick so it reads as a lit object rather than a clip-art sticker.
bg = vgradient(S, S, (22, 24, 34), (8, 9, 14))
glow = Image.new("L", (S, S), 0)
ImageDraw.Draw(glow).ellipse((90, 90, S - 90, S - 90), fill=150)
glow = glow.filter(ImageFilter.GaussianBlur(70))
bg = Image.composite(Image.new("RGB", (S, S), (70, 110, 255)), bg, glow.point(lambda v: v * 0.45))

# The stick, drawn upright on its own layer and then tilted.
layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
bw, bh = 170, 250                     # body
cw, ch = 110, 92                      # connector
bx, by = (S - bw) // 2, 205
cx, cy = (S - cw) // 2, by - ch + 6

# Connector: brushed metal, two contact windows.
metal = hgradient(cw, ch, (150, 156, 168), (232, 236, 242)).convert("RGBA")
mask = Image.new("L", (cw, ch), 0)
ImageDraw.Draw(mask).rounded_rectangle((0, 0, cw - 1, ch - 1), radius=10, fill=255)
layer.paste(metal, (cx, cy), mask)
d = ImageDraw.Draw(layer)
for wx in (cx + 24, cx + cw - 24 - 22):
    d.rounded_rectangle((wx, cy + 22, wx + 22, cy + 44), radius=3, fill=(40, 44, 54, 255))

# Body: the taOS accent, blue into violet, with a gloss band down one side.
body = vgradient(bw, bh, (86, 140, 255), (122, 84, 235)).convert("RGBA")
bmask = Image.new("L", (bw, bh), 0)
ImageDraw.Draw(bmask).rounded_rectangle((0, 0, bw - 1, bh - 1), radius=34, fill=255)
layer.paste(body, (bx, by), bmask)
gloss = Image.new("L", (bw, bh), 0)
ImageDraw.Draw(gloss).rounded_rectangle((14, 12, 58, bh - 16), radius=22, fill=90)
gloss = gloss.filter(ImageFilter.GaussianBlur(8))
layer.paste(Image.new("RGBA", (bw, bh), (255, 255, 255, 255)), (bx, by),
            Image.composite(gloss, Image.new("L", (bw, bh), 0), bmask))

# A lanyard hole and a live LED: it is an agent that is ON.
d.ellipse((S // 2 - 16, by + bh - 52, S // 2 + 16, by + bh - 20), fill=(20, 22, 30, 255))
d.ellipse((bx + bw - 44, by + 30, bx + bw - 26, by + 48), fill=(61, 220, 132, 255))
led = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(led).ellipse((bx + bw - 56, by + 18, bx + bw - 14, by + 60), fill=(61, 220, 132, 120))
layer = Image.alpha_composite(led.filter(ImageFilter.GaussianBlur(8)), layer)

# Shadow, then tilt the lot.
shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(shadow).rounded_rectangle((bx + 10, by + 24, bx + bw + 10, by + bh + 24), radius=34,
                                         fill=(0, 0, 0, 150))
shadow = shadow.filter(ImageFilter.GaussianBlur(18))
stick = Image.alpha_composite(shadow, layer).rotate(-28, resample=Image.BICUBIC, center=(S // 2, S // 2 + 20))

out = bg.convert("RGBA")
out.alpha_composite(stick, (0, -14))
OUT.parent.mkdir(parents=True, exist_ok=True)
out.convert("RGB").resize((128, 128), Image.LANCZOS).save(OUT, "JPEG", quality=92)
print(OUT)
