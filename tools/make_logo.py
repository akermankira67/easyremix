"""Draws the EasyRemix logo and writes every icon size the builds need.

    python tools/make_logo.py

The mark: a rounded square in the app's violet-to-pink gradient, holding a white
"remix" arrow that circles a small three-bar waveform. Drawn at 1024 px and scaled
down with antialiasing, so it stays crisp from 16 px (taskbar) to 512 px (Android).
"""
from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
S = 1024  # master size

TOP_LEFT = (124, 77, 255)    # #7c4dff, the app accent
BOTTOM_RIGHT = (236, 72, 153)  # #ec4899


def gradient(size: int) -> Image.Image:
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            px[x, y] = tuple(int(a + (b - a) * t) for a, b in zip(TOP_LEFT, BOTTOM_RIGHT))
    return img


def draw_mark(size: int = S) -> Image.Image:
    k = size / S
    bg = gradient(size).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=int(232 * k), fill=255)
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    icon.paste(bg, (0, 0), mask)

    d = ImageDraw.Draw(icon)
    white = (255, 255, 255, 255)
    cx = cy = size / 2
    r, w = 318 * k, 78 * k
    # remix arrow: an open ring from 40 deg to 330 deg (clockwise in PIL's angles) ...
    start, end = -50, 230
    d.arc((cx - r, cy - r, cx + r, cy + r), start=start, end=end, fill=white, width=int(w))
    # ... with round caps and an arrowhead at the end
    def at(angle, radius):
        a = math.radians(angle)
        return cx + radius * math.cos(a), cy + radius * math.sin(a)
    cap = w / 2
    sx, sy = at(start, r - w / 2)
    d.ellipse((sx - cap, sy - cap, sx + cap, sy + cap), fill=white)
    mid = r - w / 2  # PIL grows the arc inwards, so this is the stroke's centre line
    base_out, base_in = at(end, mid + w * 1.05), at(end, mid - w * 1.05)
    tip = at(end + 30, mid)
    d.polygon([base_out, base_in, tip], fill=white)

    # waveform: three rounded bars
    bw = 74 * k
    for dx, h in ((-118, 150), (0, 290), (118, 205)):
        x = cx + dx * k
        d.rounded_rectangle((x - bw / 2, cy - h * k / 2, x + bw / 2, cy + h * k / 2),
                            radius=int(bw / 2), fill=white)
    return icon


def main() -> None:
    master = draw_mark()
    os.makedirs(ASSETS, exist_ok=True)
    master.resize((512, 512), Image.LANCZOS).save(os.path.join(ASSETS, "icon.png"))
    master.resize((128, 128), Image.LANCZOS).save(os.path.join(ASSETS, "logo-128.png"))
    master.resize((64, 64), Image.LANCZOS).save(os.path.join(ASSETS, "logo-64.png"))
    master.save(os.path.join(ASSETS, "icon.ico"),
                sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    # preview sheet (not shipped): the icon at real sizes on dark and light
    sheet = Image.new("RGBA", (760, 300), (24, 24, 30, 255))
    light = Image.new("RGBA", (760, 150), (245, 245, 248, 255))
    sheet.paste(light, (0, 150))
    x = 20
    for s in (16, 24, 32, 48, 64, 128):
        small = master.resize((s, s), Image.LANCZOS)
        sheet.alpha_composite(small, (x, 75 - s // 2))
        sheet.alpha_composite(small, (x, 225 - s // 2))
        x += s + 40
    sheet.save(os.path.join(ROOT, "tools", "logo-preview.png"))
    print("wrote assets/icon.png, icon.ico, logo-64.png, logo-128.png")


if __name__ == "__main__":
    main()
