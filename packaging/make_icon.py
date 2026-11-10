"""Draw packaging/icon.png (1024 px), the Pagewise app icon.

Same mark as the UI logo (apps/web/src/packages/ui-primitives/Logo.tsx): a page with a
folded corner, three lines of text and a spark, on a dark rounded tile.
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
INK = (13, 12, 20)


def gradient(size, a, b):
    """Diagonal gradient, top-left a to bottom-right b."""
    ramp = np.linspace(0, 1, size, dtype=np.float32)
    t = ((ramp[None, :] + ramp[:, None]) / 2)[..., None]
    rgb = np.array(a, np.float32) * (1 - t) + np.array(b, np.float32) * t
    return Image.fromarray(rgb.round().astype(np.uint8), "RGB")


def main():
    big = S * SS
    # Logo units are the 64x64 SVG grid; map them onto the tile.
    scale, ox, oy = 12 * SS, (512 - 36 * 12) * SS, (512 - 31 * 12) * SS

    def p(x, y):
        return (ox + x * scale, oy + y * scale)

    def box(x0, y0, x1, y1):
        return (*p(x0, y0), *p(x1, y1))

    icon = Image.new("RGBA", (big, big), (0, 0, 0, 0))

    # tile + soft shadow
    shadow = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((92 * SS, 112 * SS, (S - 92) * SS, (S - 72) * SS), 200 * SS, fill=(0, 0, 0, 120))
    icon.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(24 * SS)))
    tile = Image.new("L", (big, big), 0)
    ImageDraw.Draw(tile).rounded_rectangle((92 * SS, 92 * SS, (S - 92) * SS, (S - 92) * SS), 200 * SS, fill=255)
    icon.paste(gradient(big, (30, 28, 44), (12, 12, 18)), (0, 0), tile)

    # Stretch the accent gradient over the mark itself so both colors show.
    x0, y0 = p(11, 2.5)
    x1, y1 = p(61, 57)
    accent = Image.new("RGB", (big, big), TEAL)
    accent.paste(gradient(int(max(x1 - x0, y1 - y0)), VIOLET, TEAL), (int(x0), int(y0)))

    # page with the top-right corner cut off
    page = Image.new("L", (big, big), 0)
    d = ImageDraw.Draw(page)
    d.rounded_rectangle(box(11, 10, 49, 57), 6 * scale, fill=255)
    d.polygon([p(36, 10), p(49.5, 9.5), p(49.5, 23)], fill=0)
    icon.paste(accent, (0, 0), page)

    # folded corner, darker
    fold = Image.new("L", (big, big), 0)
    ImageDraw.Draw(fold).polygon([p(36, 10), p(36, 19), p(40, 23), p(49, 23)], fill=255)
    icon.paste(Image.blend(accent, Image.new("RGB", (big, big), INK), 0.38), (0, 0), fold)

    # three lines of text
    d = ImageDraw.Draw(icon)
    for y, w in ((30, 22), (38, 22), (46, 13)):
        d.rounded_rectangle(box(19, y, 19 + w, y + 4), 2 * scale, fill=INK + (235,))

    # four-point spark
    cx, cy, r, k = 54, 9.4, 6.9, 1.9
    spark = Image.new("L", (big, big), 0)
    ImageDraw.Draw(spark).polygon(
        [p(cx, cy - r), p(cx + k, cy - k), p(cx + r, cy), p(cx + k, cy + k),
         p(cx, cy + r), p(cx - k, cy + k), p(cx - r, cy), p(cx - k, cy - k)], fill=255)
    glow = spark.filter(ImageFilter.GaussianBlur(18 * SS))
    icon.paste(Image.new("RGB", (big, big), TEAL), (0, 0), glow.point(lambda v: v // 2))
    icon.paste(accent, (0, 0), spark)

    icon.resize((S, S), Image.LANCZOS).save(OUT)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
