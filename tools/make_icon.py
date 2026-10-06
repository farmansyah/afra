"""Render the A.F.R.A logo to the Mac app icon (AFRA.app/Contents/Resources/AFRA.icns) and a PNG.

    python tools/make_icon.py

The drawing mirrors static/favicon.svg (64x64 design grid), drawn 16x larger with 4x supersampling.
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
S = 1024 * 4 / 64  # design units -> pixels (supersampled)
WHITE, GOLD = (255, 255, 255, 255), (255, 200, 87, 255)


def P(x, y):
    return (x * S, y * S)


def stroke(d, pts, width, color):
    w = width * S
    pts = [P(*p) for p in pts]
    d.line(pts, fill=color, width=round(w), joint="curve")
    for x, y in pts:  # round caps and joins
        d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=color)


def background(size):
    # rounded square with a diagonal blue -> navy gradient
    grad = Image.new("RGBA", (size, size))
    top, bottom = (47, 109, 179), (20, 33, 61)
    px = grad.load()
    for yy in range(0, size, 4):
        for xx in range(0, size, 4):
            t = (xx + yy) / (2 * size)
            c = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,)
            for dy in range(4):
                for dx in range(4):
                    if xx + dx < size and yy + dy < size:
                        px[xx + dx, yy + dy] = c
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=round(15 * S), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    return out


def draw_logo() -> Image.Image:
    size = round(64 * S)
    img = background(size)
    d = ImageDraw.Draw(img)
    stroke(d, [(9.5, 50), (21, 14.5), (32.5, 50)], 5.6, WHITE)   # A
    stroke(d, [(15, 38), (27, 38)], 5, WHITE)                     # A crossbar
    stroke(d, [(39.5, 50), (39.5, 14.5), (54, 14.5)], 5.6, WHITE)  # F
    stroke(d, [(39.5, 31.5), (49, 31.5)], 5, GOLD)                # gold bar
    star = [(54.5, 41.5), (56.2, 46.3), (61, 48), (56.2, 49.7), (54.5, 54.5), (52.8, 49.7), (48, 48), (52.8, 46.3)]
    d.polygon([P(*p) for p in star], fill=GOLD)                   # spark
    return img.resize((1024, 1024), Image.LANCZOS)


def main():
    icon = draw_logo()
    res = ROOT / "AFRA.app" / "Contents" / "Resources"
    res.mkdir(parents=True, exist_ok=True)
    # macOS icons sit on an ~824px tile inside a 1024 canvas, like other Dock icons
    canvas = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    canvas.paste(icon.resize((824, 824), Image.LANCZOS), (100, 100))
    canvas.save(res / "AFRA.icns", sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)])
    icon.resize((512, 512), Image.LANCZOS).save(ROOT / "static" / "icon-512.png")
    print("wrote", res / "AFRA.icns", "and static/icon-512.png")


if __name__ == "__main__":
    main()
