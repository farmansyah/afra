"""A design from slides exported as PDF (the Design step of the Create into PPT wizard).

Two things can be read from the PDF without any new dependency (pypdf is already used for papers):
  vector pages  PowerPoint, Google Slides and Keynote export text and shapes as drawing commands: the colours that fill
                the page and the text, the fonts and the page size are read straight from them. No picture is needed.
  page pictures if the pages are scanned or flattened images, pypdf can pull those out; the same GLM vision step that
                rebuilds preview-only template links (template_import.py) then reads colours, fonts and layouts from them.
                That step needs an OpenRouter key; without it A.F.R.A says so and keeps only what the vector read found.
A PDF cannot be drawn into pictures here (no renderer is installed), so a slide that mixes both is read as vector only.
"""
import io
import logging
import re
from collections import Counter
from pathlib import Path

from . import designs, pptx_build, template_import

OPS = re.compile(rb"(?P<rgb>(?:-?[\d.]+\s+){3}rg)|(?P<gray>-?[\d.]+\s+g)\b|(?P<re>(?:-?[\d.]+\s+){4}re)|(?P<fill>\b(?:f\*?|F|B\*?|b\*?)\b)"
                 rb"|(?P<tf>/[\w.+-]+\s+[\d.]+\s+Tf)|(?P<show>\b(?:Tj|TJ)\b|['\"]\s)")
logging.getLogger("pypdf").setLevel(logging.ERROR)  # a broken PDF is reported in plain words, not in the log
SUBSET = re.compile(r"^[A-Z]{6}\+")


def hexc(r, g, b) -> str:
    return "#%02X%02X%02X" % tuple(max(0, min(255, round(float(v) * 255))) for v in (r, g, b))


def family(base: str) -> str:
    """A readable font family from a PDF BaseFont name: ABCDEF+Calibri-Bold -> Calibri, TimesNewRomanPSMT -> Times New Roman."""
    f = SUBSET.sub("", str(base or "").lstrip("/"))
    f = re.split(r"[-,]", f)[0]
    f = re.sub(r"(PSMT|MT|PS)$", "", f)
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", f).strip()


def read(data: bytes) -> dict:
    """{"pages", "size": (w, h) in points, "colors": [#RRGGBB by weight], "fonts": [heading, body], "images": number of pages with pictures}."""
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = list(reader.pages)
    except Exception:
        raise ValueError("That isn't a PDF A.F.R.A can read.")
    if not pages:
        raise ValueError("That PDF has no pages.")
    box = pages[0].mediabox
    pw, ph = float(box.width), float(box.height)
    colors, sizes, shown = Counter(), {}, Counter()
    for page in pages[:60]:
        try:
            fonts = {}
            res = page.get("/Resources")
            table = res.get_object().get("/Font") if res is not None else None
            for name, obj in (table.get_object() if table is not None else {}).items():
                fonts[str(name).lstrip("/")] = family(obj.get_object().get("/BaseFont", ""))
            c = page.get_contents()
            raw = c.get_data() if c is not None else b""
        except Exception:
            continue
        fill, area, face = "#000000", 0.0, ""
        for m in OPS.finditer(raw):
            if m.group("rgb"):
                fill = hexc(*m.group("rgb").split()[:3])
            elif m.group("gray"):
                fill = hexc(*[m.group("gray").split()[0]] * 3)
            elif m.group("re"):
                v = [float(x) for x in m.group("re").split()[:4]]
                area = min(1.0, abs(v[2] * v[3]) / (pw * ph))
            elif m.group("fill"):
                if area > 0.002:
                    colors[fill] += area
                area = 0.0
            elif m.group("tf"):
                nm, size = m.group("tf").split()[:2]
                face = fonts.get(nm.decode().lstrip("/"), "")
                if face:
                    sizes[face] = max(sizes.get(face, 0), float(size))
            elif m.group("show"):
                colors[fill] += 0.004
                if face:
                    shown[face] += 1
    heading = max(sizes, key=sizes.get) if sizes else ""
    body = shown.most_common(1)[0][0] if shown else heading
    return {"pages": len(pages), "size": (pw, ph), "colors": [c for c, _ in colors.most_common(8)], "fonts": [f for f in (heading, body) if f] or [],
            "images": len(page_images(data, 60))}


def page_images(data: bytes, limit: int = 12) -> list[tuple[bytes, str]]:
    """The first picture of each page that has one (JPEG or PNG), if pypdf can hand it over."""
    from pypdf import PdfReader
    out = []
    try:
        for page in list(PdfReader(io.BytesIO(data)).pages)[:limit]:
            for im in page.images:
                ext = str(getattr(im, "name", "")).rsplit(".", 1)[-1].lower()
                if ext in ("jpg", "jpeg", "png") and len(im.data) > 5000:
                    out.append((im.data, "image/png" if ext == "png" else "image/jpeg"))
                    break
    except Exception:
        pass
    return out


def import_pdf(data: bytes, filename: str, step=lambda s: None) -> dict:
    """PDF of slides -> a saved design. Returns {"design", "method": "rebuilt"|"colours", "message"}."""
    name = Path(filename or "Slides").stem[:100]
    step("Reading the PDF…")
    info = read(data)
    shots = page_images(data) if info["images"] else []
    note = ""
    if shots:
        try:
            conn = template_import.vision_conn()
        except template_import.Stop as e:
            conn, note = None, str(e)
        if conn:
            specs = []
            for i, (img, mime) in enumerate(shots, 1):
                step(f"Reading page {i} of {len(shots)} with {conn[2]}…")
                try:
                    specs.append(template_import.read_slide(img, mime, i, len(shots), conn))
                except ValueError:
                    continue
                except template_import.Stop as e:
                    note = str(e)
                    break
            if specs:
                step("Writing the design…")
                deck = template_import.build_deck_spec(specs, name, pptx_build.image_size(shots[0][0]))
                thumb = (shots[0][0], "png" if shots[0][1] == "image/png" else "jpeg")
                pptx = pptx_build.build_from_spec(deck, name, thumb)
                d = designs.add_pptx(pptx, name + " (from PDF).pptx")
                return {"design": d, "method": "rebuilt", "message": "Rebuilt from the page pictures: similar layouts, colours and fonts, not an exact copy."}
    if not (info["colors"] or info["fonts"]):
        raise ValueError("This PDF's pages are pictures, so there are no colours or fonts to read, and A.F.R.A has no renderer to look at them. "
                         + (note or "Add an OpenRouter key (Settings › AI model) so a GLM vision model can read the pictures, or upload the original .pptx."))
    d = designs.from_colors(name + " (PDF)", info["colors"], info["fonts"], source="PDF " + (filename or ""))
    msg = f"Took {len(info['colors'])} colours and {len(info['fonts'])} font{'s' if len(info['fonts']) != 1 else ''} from the PDF. Layouts are A.F.R.A's own."
    if note:
        msg += " The pages also hold pictures, but they weren't read: " + note
    return {"design": d, "method": "colours", "message": msg}
