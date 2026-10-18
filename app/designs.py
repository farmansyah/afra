"""Designs: the slide style library, shared by the .pptx engine (pptx_build.py) and the Google Slides engine (gslides.py).

  data/designs.json        one record per design: name, kind, palette, fonts, layout names, source link, default
  data/designs/<id>.*      the design's picture (.png/.jpg) and, for an uploaded PowerPoint, the original .pptx

Kinds: builtin (A.F.R.A's own), web (a template page such as Slidesgo: its preview picture, colours and fonts),
pptx (an uploaded PowerPoint), slides (a Google Slides link), colors (made from colours and fonts). Every design has
a palette, so both engines can use every design; the Google engine also uses the real layouts of pptx and slides
designs. The Google copy made for each account is remembered in the record ("google": {email: {...}}).
Template pages are read like a browser would show them, once, without logging in or downloading the template files.
"""
import io
import json
import re
import secrets
import threading
import time
import zipfile
from collections import Counter
from pathlib import Path
from urllib.parse import unquote_plus, urljoin, urlparse
from xml.etree import ElementTree as ET

import httpx

from .config import DATA_DIR

FILE = DATA_DIR / "designs.json"
DIR = DATA_DIR / "designs"
TRANSPORT = None  # tests put an httpx.MockTransport here
THEME = {"name": "A.F.R.A Navy", "dark": "#14213D", "light": "#FFFFFF", "accent": "#FFC857", "accent2": "#3C7DC4",
         "text": "#1F2A44", "muted": "#6B7280", "heading_font": "Georgia", "body_font": "Arial"}
BUILTIN = [
    {"id": "afra-navy", "name": "A.F.R.A Navy", "theme": THEME},
    {"id": "afra-paper", "name": "Clean paper", "theme": {**THEME, "name": "Clean paper", "dark": "#1E3A3A", "accent": "#2A9D8F", "accent2": "#E9C46A",
                                                          "text": "#22303C", "heading_font": "Georgia", "body_font": "Calibri"}},
    {"id": "afra-forest", "name": "Forest", "theme": {**THEME, "name": "Forest", "dark": "#1B3A2F", "accent": "#A7C957", "accent2": "#6A994E",
                                                      "text": "#1F2D24", "heading_font": "Trebuchet MS", "body_font": "Trebuchet MS"}},
    {"id": "afra-plum", "name": "Plum", "theme": {**THEME, "name": "Plum", "dark": "#3D1F47", "accent": "#F2A65A", "accent2": "#9B5DE5",
                                                  "text": "#2B1B30", "heading_font": "Palatino Linotype", "body_font": "Arial"}},
    {"id": "afra-coral", "name": "Coral", "theme": {**THEME, "name": "Coral", "dark": "#264653", "accent": "#F4A261", "accent2": "#E76F51",
                                                    "text": "#263238", "heading_font": "Verdana", "body_font": "Verdana"}},
    {"id": "afra-mono", "name": "Charcoal", "theme": {**THEME, "name": "Charcoal", "dark": "#222222", "accent": "#FF6B35", "accent2": "#4ECDC4",
                                                      "text": "#222222", "heading_font": "Arial", "body_font": "Arial"}},
]
_LOCK = threading.RLock()


def _read() -> dict:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(d: dict):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2), encoding="utf-8")
    tmp.replace(FILE)


def _rows() -> list[dict]:
    d = _read()
    if not d.get("seeded"):  # first use: the built-in designs, the first one as default
        have = {r["id"] for r in d.get("designs", [])}
        rows = [{**b, "kind": "builtin", "theme": dict(b["theme"]), "layouts": [], "source": "built in", "source_url": "",
                 "default": False, "added": time.time() - 100 + i} for i, b in enumerate(BUILTIN) if b["id"] not in have] + d.get("designs", [])
        if not any(r.get("default") for r in rows):
            rows[0]["default"] = True
        _write({"seeded": True, "designs": rows})
        return rows
    return d.get("designs", [])


def _save(rows: list[dict]):
    _write({"seeded": True, "designs": rows})


def theme(rec: dict) -> dict:
    return {**THEME, **(rec.get("theme") or {})}


def _public(r: dict) -> dict:
    out = {k: v for k, v in r.items() if k != "google"}
    out["theme"] = theme(r)
    out["accounts"] = sorted((r.get("google") or {}).keys())
    out["has_pptx"] = (DIR / f"{r['id']}.pptx").exists()
    return out


def all_designs() -> list[dict]:
    with _LOCK:
        rows = _rows()
    return [_public(r) for r in sorted(rows, key=lambda r: (not r.get("default"), r.get("kind") != "builtin", r.get("added", 0)))]


def get(did: str) -> dict:
    with _LOCK:
        r = next((r for r in _rows() if r["id"] == did), None)
    if not r:
        raise ValueError("That design no longer exists.")
    return r


def default() -> dict:
    with _LOCK:
        rows = _rows()
    return next((r for r in rows if r.get("default")), None) or (rows[0] if rows else {"id": "", "name": "A.F.R.A", "kind": "builtin", "theme": THEME})


def pick(did: str = "") -> dict:
    return get(did) if did else default()


def put(rec: dict) -> dict:
    with _LOCK:
        rows = [r for r in _rows() if r["id"] != rec["id"]]
        if rec.get("default"):
            for r in rows:
                r["default"] = False
        rows.append(rec)
        _save(rows)
    return rec


def update(did: str, name: str | None = None, make_default: bool = False) -> dict:
    r = get(did)
    if name is not None and name.strip():
        r["name"] = name.strip()[:120]
    if make_default:
        r["default"] = True
    return _public(put(r))


def set_google(did: str, email: str, info: dict | None):
    with _LOCK:
        r = get(did)
        r.setdefault("google", {})
        if info:
            r["google"][email] = info
        else:
            r["google"].pop(email, None)
        put(r)


def delete(did: str) -> dict:
    """Forget a design (its Google copies stay in "A.F.R.A Decks/Templates" until the caller trashes them)."""
    r = get(did)
    with _LOCK:
        rows = [x for x in _rows() if x["id"] != did]
        if r.get("default") and rows:
            rows[0]["default"] = True
        _save(rows)
    for f in DIR.glob(f"{did}.*") if DIR.exists() else []:
        f.unlink(missing_ok=True)
    return r


def restore_builtins() -> list[dict]:
    with _LOCK:
        rows = _rows()
        have = {r["id"] for r in rows}
        for i, b in enumerate(BUILTIN):
            if b["id"] not in have:
                rows.append({**b, "kind": "builtin", "theme": dict(b["theme"]), "layouts": [], "source": "built in", "source_url": "",
                             "default": False, "added": time.time() + i})
        _save(rows)
    return all_designs()


def new_id() -> str:
    return "d" + secrets.token_hex(5)


# ---------------------------------------------------------------- colours
def rgb(hexs: str) -> dict:
    h = (hexs or "#000000").lstrip("#")
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}


def lum(hexs: str) -> float:
    c = rgb(hexs)
    return 0.2126 * c["red"] + 0.7152 * c["green"] + 0.0722 * c["blue"]


def theme_from(colors: dict, heading: str, body: str, name: str) -> dict:
    """Our theme keys from Office/Google colour slots (dk1 lt1 dk2 lt2 accent1..6)."""
    dk = [c for c in (colors.get("dk2"), colors.get("dk1"), colors.get("accent1")) if c]
    dark = next((c for c in dk if lum(c) < .35), THEME["dark"])
    light = colors.get("lt1") if colors.get("lt1") and lum(colors["lt1"]) > .8 else "#FFFFFF"
    text = colors.get("dk1") if colors.get("dk1") and lum(colors["dk1"]) < .4 else dark
    accents = [colors[k] for k in ("accent1", "accent2", "accent3", "accent4") if colors.get(k)]
    accent = next((c for c in accents if abs(lum(c) - lum(dark)) > .3), accents[0] if accents else THEME["accent"])
    return {"name": name or "Style", "dark": dark, "light": light, "text": text, "accent": accent,
            "accent2": next((c for c in accents if c != accent), THEME["accent2"]), "muted": THEME["muted"],
            "heading_font": heading or THEME["heading_font"], "body_font": body or THEME["body_font"], "colors": colors}


def theme_from_list(name: str, colors: list[str], fonts: list[str]) -> dict:
    """A theme from a plain list of colours and fonts (a web page, or typed in)."""
    cs = [c.upper() for c in colors if re.fullmatch(r"#[0-9A-Fa-f]{6}", c or "")]
    darks = sorted([c for c in cs if lum(c) < .4], key=lum)
    dark = darks[0] if darks else THEME["dark"]
    acc = [c for c in cs if c != dark and .15 < lum(c) < .9] or [THEME["accent"]]
    t = {**THEME, "name": name or "Style", "dark": dark, "text": dark if lum(dark) < .25 else THEME["text"], "accent": acc[0],
         "accent2": acc[1] if len(acc) > 1 else THEME["accent2"], "palette": cs[:8]}
    if fonts:
        t["heading_font"] = fonts[0]
        t["body_font"] = fonts[1] if len(fonts) > 1 else fonts[0]
    return t


def from_colors(name: str, colors: list[str], fonts: list[str], source: str = "", source_url: str = "", image: str = "") -> dict:
    rec = {"id": new_id(), "name": (name or "My style")[:120], "kind": "colors" if not source_url else "web",
           "theme": theme_from_list(name, colors, fonts), "layouts": [], "source": source or "colours and fonts", "source_url": source_url,
           "image": image, "default": False, "added": time.time()}
    put(rec)
    if image:
        cache_image(rec["id"], image)
    return _public(rec)


# ---------------------------------------------------------------- PowerPoint files
NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main", "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "r": "http://schemas.openxmlformats.org/package/2006/relationships"}


def pptx_theme(data: bytes) -> dict:
    """Theme of a .pptx (zip + XML, no python-pptx): colour scheme, fonts, layout names, slide size, its picture."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("That isn't a .pptx file.")
    names = z.namelist()
    theme_file = ""
    rels = "ppt/slideMasters/_rels/slideMaster1.xml.rels"
    if rels in names:
        for r in ET.fromstring(z.read(rels)).findall("r:Relationship", NS):
            if r.get("Type", "").endswith("/theme"):
                theme_file = "ppt/" + r.get("Target", "").replace("../", "")
    theme_file = theme_file if theme_file in names else next((n for n in sorted(names) if re.fullmatch(r"ppt/theme/theme\d+\.xml", n)), "")
    if not theme_file:
        raise ValueError("No theme inside this .pptx.")
    root = ET.fromstring(z.read(theme_file))
    colors = {}
    scheme = root.find(".//a:clrScheme", NS)
    for c in list(scheme) if scheme is not None else []:
        slot = c.tag.split("}")[1]
        v, s = c.find("a:srgbClr", NS), c.find("a:sysClr", NS)
        val = v.get("val") if v is not None else s.get("lastClr") if s is not None else ""
        if val:
            colors[slot] = "#" + val.upper()

    def font(kind):
        f = root.find(f".//a:fontScheme/a:{kind}/a:latin", NS)
        return f.get("typeface", "") if f is not None else ""
    layouts = []
    for n in sorted((n for n in names if re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", n)), key=lambda n: int(re.findall(r"\d+", n)[-1])):
        c = ET.fromstring(z.read(n)).find("p:cSld", NS)
        layouts.append(c.get("name", "") if c is not None else "")
    size = {}
    if "ppt/presentation.xml" in names:
        sz = ET.fromstring(z.read("ppt/presentation.xml")).find("p:sldSz", NS)
        if sz is not None:
            size = {"cx": int(sz.get("cx", 0)), "cy": int(sz.get("cy", 0))}
    name = root.get("name", "") or (scheme.get("name", "") if scheme is not None else "")
    t = theme_from(colors, font("majorFont"), font("minorFont"), name)
    thumb = next((n for n in names if n.lower().startswith("docprops/thumbnail.")), "")
    return {**t, "layouts": layouts, "size": size, "slides": sum(1 for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
            "thumbnail": (thumb.rsplit(".", 1)[1].lower(), z.read(thumb)) if thumb else None}


def add_pptx(data: bytes, filename: str) -> dict:
    t = pptx_theme(data)
    thumb = t.pop("thumbnail")
    layouts, size, count = t.pop("layouts"), t.pop("size"), t.pop("slides")
    rec = {"id": new_id(), "name": Path(filename or "PowerPoint").stem[:120], "kind": "pptx", "theme": t, "layouts": [x for x in layouts if x],
           "slide_size": size, "slides": count, "source": "uploaded " + (filename or ".pptx"), "source_url": "", "default": False, "added": time.time()}
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / f"{rec['id']}.pptx").write_bytes(data)
    if thumb and thumb[0] in ("jpeg", "jpg", "png"):
        (DIR / f"{rec['id']}.{'png' if thumb[0] == 'png' else 'jpg'}").write_bytes(thumb[1])
    put(rec)
    return _public(rec)


def pptx_bytes(did: str) -> bytes | None:
    f = DIR / f"{did}.pptx"
    return f.read_bytes() if f.exists() else None


# ---------------------------------------------------------------- Google Slides decks (the dict the Slides API returns)
def _hex(c: dict) -> str:
    return "#" + "".join(f"{round(c.get(k, 0) * 255):02X}" for k in ("red", "green", "blue"))


def slides_theme(pres: dict) -> dict:
    slot = {"DARK1": "dk1", "LIGHT1": "lt1", "DARK2": "dk2", "LIGHT2": "lt2", "ACCENT1": "accent1", "ACCENT2": "accent2",
            "ACCENT3": "accent3", "ACCENT4": "accent4", "ACCENT5": "accent5", "ACCENT6": "accent6"}
    master = (pres.get("masters") or [{}])[0]
    colors = {slot[c["type"]]: _hex(c.get("color", {})) for c in ((master.get("pageProperties") or {}).get("colorScheme") or {}).get("colors", [])
              if c.get("type") in slot}
    fonts = {}
    for el in master.get("pageElements", []):
        ph = ((el.get("shape") or {}).get("placeholder") or {}).get("type")
        for te in ((el.get("shape") or {}).get("text") or {}).get("textElements", []):
            f = ((te.get("textRun") or {}).get("style") or {}).get("fontFamily")
            if f and ph in ("TITLE", "BODY"):
                fonts.setdefault(ph, f)
    return theme_from(colors, fonts.get("TITLE", ""), fonts.get("BODY", ""), pres.get("title", ""))


# ---------------------------------------------------------------- template web pages (Slidesgo and similar)
UA = "Mozilla/5.0 (A.F.R.A research assistant; template preview)"


def parse_page(html: str, base: str) -> dict:
    """What a template page shows: title, preview pictures, the colours and fonts it names. Pure (tests use saved HTML)."""
    html = html[:3_000_000]
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    og_title = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', html, re.I)
    imgs = re.findall(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', html, re.I)
    imgs += re.findall(r'<img[^>]+?(?:data-src|src)=["\']([^"\']+\.(?:jpe?g|png|webp)(?:\?[^"\']*)?)["\']', html, re.I)
    seen, pics = set(), []
    for u in imgs:
        u = urljoin(base, u.replace("&amp;", "&"))
        if u not in seen and not re.search(r"logo|icon|avatar|sprite|flag|badge|banner-ad", u, re.I):
            seen.add(u)
            pics.append(u)
    # the colours a template page lists ("#1D3557" swatches), most frequent first; the site's own stylesheets and
    # scripts, near-white and near-black left out
    shown = re.sub(r"<(style|script)\b.*?</\1\s*>", " ", html, flags=re.S | re.I)
    hexes = Counter("#" + c.upper() for c in re.findall(r"#([0-9a-fA-F]{6})\b", shown))
    colors = [c for c, _ in hexes.most_common(40) if .04 < lum(c) < .98][:8]
    fonts = []
    for fam in re.findall(r"fonts\.googleapis\.com/css2?\?family=([^\"'>\s]+)", html):
        for part in fam.replace("&amp;", "&").split("&family="):
            n = unquote_plus(part.split(":")[0].split("&")[0]).strip()
            if n and n not in fonts:
                fonts.append(n)
    for m in re.findall(r"(?:Fonts?|Typefaces?)(?:\s+used)?\s*:\s*(?:<[^>]+>\s*)*([A-Z][A-Za-z0-9 ]{2,30}?)\s*(?:and|,|&amp;|<|\n)", html):
        if m.strip() not in fonts:
            fonts.append(m.strip())
    name = (og_title.group(1) if og_title else title.group(1) if title else "") or urlparse(base).path.rstrip("/").rsplit("/", 1)[-1]
    name = re.split(r"\s+[|\-–]\s+(?:Free|Google Slides|Slidesgo|PowerPoint)", re.sub(r"\s+", " ", name).strip())[0][:100]
    return {"url": base, "title": name, "images": pics[:8], "colors": colors, "fonts": fonts[:4],
            "google_slides": bool(re.search(r"google\s*slides", html, re.I))}


HOW = ("The design's own files stay on the template site: open the page, sign in there if it asks, and use its "
       "\"Google Slides\" button (it makes a copy in your Drive). Paste that copy's link here to use its real layouts. "
       "Until then A.F.R.A uses the colours and fonts it could see.")


def fetch_page(url: str) -> dict:
    """One public GET of the page; never a login, never the template's download."""
    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError("Paste the web address of the template page (https://…).")
    with httpx.Client(timeout=25, follow_redirects=True, transport=TRANSPORT, headers={"User-Agent": UA}) as h:
        try:
            r = h.get(url)
        except httpx.HTTPError as e:
            raise ValueError(f"Couldn't open that page ({e.__class__.__name__}).")
    if r.status_code in (401, 403) or re.search(r"/(login|signin|sign-in|auth)\b", str(r.url)):
        raise ValueError("That page needs a login, so A.F.R.A doesn't read it. Open it in your browser instead.")
    if r.status_code >= 400:
        raise ValueError(f"The page answered {r.status_code}.")
    if "html" not in r.headers.get("content-type", "text/html"):
        raise ValueError("That link is a file, not a template page. A.F.R.A only reads the page.")
    return parse_page(r.text, str(r.url))


def cache_image(did: str, url: str):
    """Keep the page's preview picture as the design's thumbnail (it is shown on the page itself)."""
    try:
        with httpx.Client(timeout=20, follow_redirects=True, transport=TRANSPORT, headers={"User-Agent": UA}) as h:
            r = h.get(url)
        ctype = r.headers.get("content-type", "")
        if r.status_code == 200 and len(r.content) < 4_000_000 and ("png" in ctype or "jpeg" in ctype or "jpg" in ctype):
            DIR.mkdir(parents=True, exist_ok=True)
            (DIR / f"{did}.{'png' if 'png' in ctype else 'jpg'}").write_bytes(r.content)
    except httpx.HTTPError:
        pass


URL_RE = re.compile(r"https?://[^\s<>\"',]+")


def split_links(text: str) -> list[str]:
    out = []
    for u in URL_RE.findall(text or ""):
        u = u.rstrip(").;")
        if u not in out:
            out.append(u)
    return out[:12]


def is_slides_link(u: str) -> bool:
    return bool(re.search(r"docs\.google\.com/presentation/d/", u))


def add_web(url: str) -> dict:
    p = fetch_page(url)
    rec = from_colors(p["title"] or "Web design", p["colors"], p["fonts"], "web page " + urlparse(p["url"]).netloc, p["url"],
                      p["images"][0] if p["images"] else "")
    return {**rec, "preview": p, "how": HOW}


# ---------------------------------------------------------------- pictures
def thumb(did: str) -> tuple[bytes, str]:
    for ext, mime in (("png", "image/png"), ("jpg", "image/jpeg")):
        f = DIR / f"{did}.{ext}"
        if f.exists():
            return f.read_bytes(), mime
    return swatch(theme(get(did)), get(did)["name"]).encode(), "image/svg+xml"


def swatch(t: dict, name: str = "") -> str:
    """A small title-slide picture of a palette, for designs without a picture."""
    from xml.sax.saxutils import escape
    t = {**THEME, **t}
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 180" width="320" height="180">'
            f'<rect width="320" height="180" fill="{t["dark"]}"/><rect x="22" y="62" width="34" height="4" fill="{t["accent"]}"/>'
            f'<text x="22" y="98" font-family="{escape(t["heading_font"])}, serif" font-size="20" fill="{t["light"]}">{escape(name[:26] or "Title")}</text>'
            f'<text x="22" y="122" font-family="{escape(t["body_font"])}, sans-serif" font-size="11" fill="{t["accent"]}">Subtitle · {escape(t["body_font"])}</text>'
            f'<rect x="236" y="140" width="18" height="18" fill="{t["accent"]}"/><rect x="258" y="140" width="18" height="18" fill="{t["accent2"]}"/>'
            f'<rect x="280" y="140" width="18" height="18" fill="{t["light"]}"/></svg>')
