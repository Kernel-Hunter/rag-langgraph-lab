"""Draw packaging/icon.png (1024 px), the Vesper app icon.

Same mark as the UI logo (apps/web/src/packages/ui-primitives/Logo.tsx): the evening
star over a curved horizon, with a small second star, on a dark rounded tile.
Run once after changing it: python packaging/make_icon.py
"""

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

S = 1024
SS = 4  # supersampling factor for smooth edges
OUT = Path(__file__).with_name("icon.png")
VIOLET = (139, 124, 255)
TEAL = (45, 212, 191)


def gradient(size, a, b):
    """Diagonal gradient, top-left a to bottom-right b."""
    ramp = np.linspace(0, 1, size, dtype=np.float32)
    t = ((ramp[None, :] + ramp[:, None]) / 2)[..., None]
    rgb = np.array(a, np.float32) * (1 - t) + np.array(b, np.float32) * t
    return Image.fromarray(rgb.round().astype(np.uint8), "RGB")


def quad(p0, p1, p2, n=24):
    """Points along a quadratic Bezier curve."""
    return [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
             (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1])
            for t in (i / n for i in range(n + 1))]


def star(cx, cy, r, k):
    """Four-point star with concave sides, same path as Logo.tsx."""
    tips = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy), (cx, cy - r)]
    ctrl = [(cx + k, cy - k), (cx + k, cy + k), (cx - k, cy + k), (cx - k, cy - k)]
    pts = []
    for i in range(4):
        pts += quad(tips[i], ctrl[i], tips[i + 1])[:-1]
    return pts


def main():
    big = S * SS
    # Logo units are the 64x64 SVG grid; map them onto the tile.
    scale = 11.5 * SS
    ox, oy = (512 - 32 * 11.5) * SS, (512 - 33 * 11.5) * SS

    def p(x, y):
        return (ox + x * scale, oy + y * scale)

    icon = Image.new("RGBA", (big, big), (0, 0, 0, 0))

    # tile + soft shadow
    shadow = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((92 * SS, 112 * SS, (S - 92) * SS, (S - 72) * SS), 200 * SS, fill=(0, 0, 0, 120))
    icon.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(24 * SS)))
    tile = Image.new("L", (big, big), 0)
    ImageDraw.Draw(tile).rounded_rectangle((92 * SS, 92 * SS, (S - 92) * SS, (S - 92) * SS), 200 * SS, fill=255)
    icon.paste(gradient(big, (30, 28, 44), (12, 12, 18)), (0, 0), tile)

    # The accent gradient spans the mark so both colors show.
    x0, y0 = p(3, 3)
    x1, y1 = p(58, 58)
    accent = Image.new("RGB", (big, big), TEAL)
    accent.paste(gradient(int(max(x1 - x0, y1 - y0)), VIOLET, TEAL), (int(x0), int(y0)))

    mark = Image.new("L", (big, big), 0)
    d = ImageDraw.Draw(mark)
    d.polygon([p(*q) for q in star(31, 25, 20, 3.6)], fill=255)
    d.polygon([p(*q) for q in star(51, 11, 6.5, 1.3)], fill=217)
    # Thick horizon: a dense run of discs gives round caps and no seams.
    r = 2.25 * scale
    for x, y in (p(*q) for q in quad((6, 55), (32, 44), (58, 55), 400)):
        d.ellipse((x - r, y - r, x + r, y + r), fill=255)

    # soft glow behind the big star
    glow = Image.new("L", (big, big), 0)
    ImageDraw.Draw(glow).polygon([p(*q) for q in star(31, 25, 20, 3.6)], fill=255)
    glow = glow.filter(ImageFilter.GaussianBlur(40 * SS)).point(lambda v: v * 2 // 5)
    icon.paste(Image.new("RGB", (big, big), VIOLET), (0, 0), Image.composite(glow, Image.new("L", (big, big), 0), tile))
    icon.paste(accent, (0, 0), mark)

    icon.resize((S, S), Image.LANCZOS).save(OUT)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
