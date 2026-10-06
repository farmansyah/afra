"""A.F.R.A brand assets: logos (3 switchable styles), favicon, website icon and the Mac app icon.

    pip install fonttools skia-pathops pillow numpy   (developer tools only, not needed to run A.F.R.A)
    python tools/brand.py [--garnish dot] [--mac soft]

Letters are real glyph outlines from open-licence (SIL OFL) Google Fonts, which may be used in logos.
Fonts are downloaded once into tools/.fonts/ (not committed).
"""
import argparse
import urllib.request
from pathlib import Path

from fontTools.pens.basePen import BasePen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "tools" / ".fonts"
GOLD = "#e3b25c"
GF = "https://github.com/google/fonts/raw/main/ofl/"

STYLES = {
    # key: (label, font file in Google Fonts repo, variable axes, background, letters, border, tracking)
    "soft": ("Soft serif", "fraunces/Fraunces%5BSOFT,WONK,opsz,wght%5D.ttf", {"wght": 600, "opsz": 72, "SOFT": 100, "WONK": 0},
             "#14213d", "#f4e9d0", None, -0.03),
    "editorial": ("Editorial serif", "dmserifdisplay/DMSerifDisplay-Regular.ttf", {}, "#14213d", "#ffffff", None, -0.02),
    "classic": ("Classic book serif", "librebaskerville/LibreBaskerville%5Bwght%5D.ttf", {"wght": 700},
                "#ffffff", "#14213d", "#d9dde5", 0.035),
}


def font_for(key):
    _, path, axes, *_ = STYLES[key]
    CACHE.mkdir(parents=True, exist_ok=True)
    local = CACHE / path.split("/")[-1].replace("%5B", "[").replace("%5D", "]")
    if not local.exists():
        urllib.request.urlretrieve(GF + path, local)
    f = TTFont(local)
    if axes and "fvar" in f:
        # REMOVE merges overlapping contours into clean single outlines (no keyhole seams)
        return instancer.instantiateVariableFont(f, axes, overlap=instancer.OverlapMode.REMOVE)
    from fontTools.ttLib.removeOverlaps import removeOverlaps
    removeOverlaps(f)
    return f


def glyph_run(font, text, tracking, pen_factory):
    gs, cmap, upm = font.getGlyphSet(), font.getBestCmap(), font["head"].unitsPerEm
    x, out = 0, []
    bp = BoundsPen(gs)
    for ch in text:
        g = cmap[ord(ch)]
        pen = pen_factory(gs)
        gs[g].draw(TransformPen(pen, (1, 0, 0, -1, x, 0)))
        gs[g].draw(TransformPen(bp, (1, 0, 0, -1, x, 0)))
        out.append((ch, pen))
        x += font["hmtx"][g][0] + tracking * upm
    return out, bp.bounds


def layout(key, garnish, size=64):
    label, _, _, bg, fg, border, tracking = STYLES[key]
    font = font_for(key)
    run, (x0, y0, x1, y1) = glyph_run(font, "AF", tracking, SVGPathPen)
    w, h = x1 - x0, y1 - y0
    sc = size * (0.56 if garnish else 0.6) / max(w, h * 1.05)
    dx = -2.5 if garnish == "dot" else 0
    tx = (size - w * sc) / 2 - x0 * sc + dx
    ty = (size - h * sc) / 2 - y0 * sc
    right, bottom = tx + x1 * sc, ty + y1 * sc
    return dict(font=font, bg=bg, fg=fg, border=border, tracking=tracking, sc=sc, tx=tx, ty=ty,
                d="".join(p.getCommands() for _, p in run), dot=(right + 4.2, bottom - 2.6, 2.6) if garnish == "dot" else None)


def svg_icon(key, garnish="dot", size=64):
    L = layout(key, garnish, size)
    stroke = f' stroke="{L["border"]}" stroke-width="1.5"' if L["border"] else ""
    rect = f'<rect x="0.75" y="0.75" width="{size-1.5}" height="{size-1.5}" rx="{size*0.22:.1f}" fill="{L["bg"]}"{stroke}/>'
    dot = f'<circle cx="{L["dot"][0]:.2f}" cy="{L["dot"][1]:.2f}" r="{L["dot"][2]}" fill="{GOLD}"/>' if L["dot"] else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">{rect}'
            f'<path fill="{L["fg"]}" transform="translate({L["tx"]:.2f} {L["ty"]:.2f}) scale({L["sc"]:.5f})" d="{L["d"]}"/>{dot}</svg>\n')


# ---------------------------------------------------------------- raster (Mac .icns / PNG) without an SVG renderer
class FlatPen(BasePen):
    """Flattens glyph curves into polygons."""
    def __init__(self, gs, steps=24):
        super().__init__(gs)
        self.contours, self.cur, self.steps = [], [], steps

    def _moveTo(self, p):
        self.cur = [p]

    def _lineTo(self, p):
        self.cur.append(p)

    def _curveToOne(self, p1, p2, p3):
        p0 = self.cur[-1]
        for i in range(1, self.steps + 1):
            t = i / self.steps
            mt = 1 - t
            self.cur.append((mt**3 * p0[0] + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t**3 * p3[0],
                             mt**3 * p0[1] + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t**3 * p3[1]))

    def _qCurveToOne(self, p1, p2):
        p0 = self.cur[-1]
        for i in range(1, self.steps + 1):
            t = i / self.steps
            mt = 1 - t
            self.cur.append((mt * mt * p0[0] + 2 * mt * t * p1[0] + t * t * p2[0], mt * mt * p0[1] + 2 * mt * t * p1[1] + t * t * p2[1]))

    def _closePath(self):
        if len(self.cur) > 2:
            self.contours.append(self.cur)
        self.cur = []

    _endPath = _closePath


def raster_icon(key, garnish="dot", px=1024):
    from PIL import Image, ImageDraw
    ss = 4
    size = px * ss
    k = size / 64
    L = layout(key, garnish)
    run, _ = glyph_run(L["font"], "AF", L["tracking"], FlatPen)
    hexc = lambda c: tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) + (255,)  # noqa: E731
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=round(64 * 0.22 * k), fill=hexc(L["bg"]),
                        outline=hexc(L["border"]) if L["border"] else None, width=round(1.5 * k) if L["border"] else 0)
    # nonzero winding fill (what browsers do): variable fonts build letters from overlapping contours
    import numpy as np
    winding = np.zeros((size, size), dtype=np.int16)
    area = lambda p: sum(p[i][0] * p[i - 1][1] - p[i - 1][0] * p[i][1] for i in range(len(p)))  # noqa: E731
    for _, pen in run:
        for c in pen.contours:
            poly = [((L["tx"] + x * L["sc"]) * k, (L["ty"] + y * L["sc"]) * k) for x, y in c]
            m = Image.new("L", (size, size), 0)
            ImageDraw.Draw(m).polygon(poly, fill=1)
            winding += np.asarray(m, dtype=np.int16) * (1 if area(poly) > 0 else -1)
    mask = Image.fromarray(((winding != 0) * 255).astype(np.uint8), "L")
    img.paste(Image.new("RGBA", (size, size), hexc(L["fg"])), (0, 0), mask)
    if L["dot"]:
        cx, cy, r = (v * k for v in L["dot"])
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=hexc(GOLD))
    return img.resize((px, px), Image.LANCZOS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--garnish", default="dot", choices=["dot", "none"])
    ap.add_argument("--mac", default="classic", choices=list(STYLES), help="style used for the Mac app icon and website")
    a = ap.parse_args()
    g = None if a.garnish == "none" else a.garnish
    out = ROOT / "static" / "logos"
    out.mkdir(parents=True, exist_ok=True)
    for key in STYLES:
        (out / f"{key}.svg").write_text(svg_icon(key, g), encoding="utf-8", newline="\n")
    main_svg = svg_icon(a.mac, g)
    for p in (ROOT / "static" / "favicon.svg", ROOT / "docs" / "favicon.svg"):
        p.write_text(main_svg, encoding="utf-8", newline="\n")
    from PIL import Image
    icon = raster_icon(a.mac, g)
    icon.resize((512, 512), Image.LANCZOS).save(ROOT / "static" / "icon-512.png")
    canvas = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))  # macOS icon grid: ~824px tile on a 1024 canvas
    canvas.paste(icon.resize((824, 824), Image.LANCZOS), (100, 100))
    res = ROOT / "AFRA.app" / "Contents" / "Resources"
    res.mkdir(parents=True, exist_ok=True)
    canvas.save(res / "AFRA.icns", sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)])
    print("logos:", ", ".join(STYLES), "| favicon + Mac icon:", a.mac, "| garnish:", a.garnish)


if __name__ == "__main__":
    main()
