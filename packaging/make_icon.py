"""Draw packaging/icon.png (1024 px). Run once: python packaging/make_icon.py

PyInstaller turns the PNG into .icns (macOS) and .ico (Windows) when Pillow is installed.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 1024
OUT = Path(__file__).with_name("icon.png")


def gradient(size, a, b):
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            px[x, y] = tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
    return img


def main():
    icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    tile_box = (92, 92, S - 92, S - 92)
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((92, 110, S - 92, S - 74), 200, fill=(0, 0, 0, 110))
    icon.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(24)))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(tile_box, 200, fill=255)
    icon.paste(gradient(S, (24, 22, 34), (12, 12, 18)).convert("RGBA"), (0, 0), mask)

    accent = gradient(S, (167, 139, 250), (34, 211, 238)).convert("RGBA")
    back = Image.new("L", (S, S), 0)
    ImageDraw.Draw(back).rounded_rectangle((250, 300, 600, 760), 60, fill=110)
    icon.paste(accent, (0, 0), back)
    front = Image.new("L", (S, S), 0)
    ImageDraw.Draw(front).rounded_rectangle((350, 240, 720, 720), 64, fill=255)
    icon.paste(accent, (0, 0), front)
    d = ImageDraw.Draw(icon)
    for i, w in enumerate((250, 250, 160)):
        y = 360 + i * 95
        d.rounded_rectangle((430, y, 430 + w, y + 34), 17, fill=(14, 14, 20, 255))
    d.ellipse((560, 560, 800, 800), fill=(14, 14, 20, 255))
    ring = Image.new("L", (S, S), 0)
    rd = ImageDraw.Draw(ring)
    rd.ellipse((600, 600, 760, 760), fill=255)
    rd.ellipse((632, 632, 728, 728), fill=0)
    rd.line((740, 740, 832, 832), fill=255, width=40)
    rd.ellipse((812, 812, 852, 852), fill=255)
    icon.paste(accent, (0, 0), ring)
    icon.save(OUT)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
