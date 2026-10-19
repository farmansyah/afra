"""Slides studio: research decks from readings and documents, as Google Slides and/or an editable PowerPoint file.

Flow: inputs (title, topic, library papers, an A.F.R.A document, uploaded PDF/DOCX/TXT/MD, pasted notes, a Sheet)
-> the AI writes an outline the user edits -> the AI writes each slide's content as JSON for A.F.R.A's layouts
-> fit_slides() splits what would not fit -> a background job builds it with one or both engines and reviews it:
  google  copies the design's template in "A.F.R.A Decks", fills it (text, tables, linked Sheets charts, images,
          speaker notes) with plain Slides API calls (google_api.py)
  pptx    pptx_build.py writes the file; with Google Drive for desktop it lands in "My Drive/A.F.R.A Decks"
The AI steps use llm.py (API key, or Claude mode through the page's hand-off). The MCP tools can also hand in
content Claude wrote itself and only use the build step.

Designs (designs.py) are shared by both engines. In Google each design becomes a template in
"A.F.R.A Decks/Templates", once per account:
  token   slides with {{title}}, {{body}}... placeholders and "afra:layout=<name>" in the speaker notes (A.F.R.A's
          own designs, and designs known only by their colours and fonts)
  layout  a real deck (an uploaded .pptx converted by Google, or a copied Google Slides deck): slides are added
          from its own layouts, so masters, backgrounds and fonts stay exactly as designed.
"""
import asyncio
import json
import math
import re
import secrets
import threading
import time
from pathlib import Path

import httpx

from . import db, designs, google_api as g
from .designs import rgb
from .citations import bibliography, process_citations
from .llm import complete_json

LAYOUTS = ["title", "section", "content", "two_column", "figure", "table", "chart", "references", "closing"]
# layouts only the PowerPoint wizard (wizard.py) asks for; the Google template path bends them onto LAYOUTS (FALLBACK)
EXTRA_LAYOUTS = ["quote", "image_left", "image_right"]
KNOWN = LAYOUTS + EXTRA_LAYOUTS
THEME = designs.THEME
EMU = 12700  # per point
PAGE = (720, 405)  # 16:9 in points
# the built-in template: layout -> boxes (token, x, y, w, h, role), all in points
BOXES = {
    "title": [("title", 48, 120, 624, 100, "hero"), ("subtitle", 48, 232, 624, 70, "sub")],
    "section": [("title", 48, 150, 624, 80, "h1"), ("subtitle", 48, 240, 624, 50, "sub")],
    "content": [("title", 40, 20, 640, 56, "h2"), ("body", 40, 96, 640, 284, "body")],
    "two_column": [("title", 40, 20, 640, 56, "h2"), ("left", 40, 96, 310, 284, "body"), ("right", 370, 96, 310, 284, "body")],
    "figure": [("title", 40, 20, 640, 56, "h2"), ("figure", 40, 96, 420, 276, "area"), ("caption", 476, 96, 204, 276, "small")],
    "table": [("title", 40, 20, 640, 56, "h2"), ("table", 40, 96, 640, 250, "area"), ("caption", 40, 352, 640, 30, "small")],
    "chart": [("title", 40, 20, 640, 56, "h2"), ("chart", 40, 96, 440, 276, "area"), ("caption", 496, 96, 184, 276, "small")],
    "references": [("title", 40, 20, 640, 56, "h2"), ("body", 40, 90, 640, 296, "refs")],
    "closing": [("title", 48, 140, 624, 90, "hero"), ("subtitle", 48, 240, 624, 60, "sub")],
}
ROLES = {"hero": (40, True, "heading"), "h1": (34, True, "heading"), "h2": (26, True, "heading"), "sub": (18, False, "body"),
         "body": (18, False, "body"), "small": (13, False, "body"), "refs": (11, False, "body"), "area": (12, False, "body")}
DARK = {"title", "section", "closing"}
FALLBACK = {"two_column": "content", "figure": "content", "table": "content", "chart": "content", "references": "content",
            "closing": "section", "section": "title", "title": "section", "content": "section",
            "quote": "content", "image_left": "figure", "image_right": "figure"}
MAX_BULLETS, MAX_CHARS, MAX_ROWS, MAX_COLS = 6, 160, 10, 6
TOKEN_RE = re.compile(r"\{\{(\w+)\}\}")
MATERIALS: dict[str, dict] = {}  # uploaded PDFs/notes for the next deck, in memory only
JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _file(name: str) -> Path:
    return g.DIR / name


def _load(name: str) -> list:
    try:
        return json.loads(_file(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(name: str, rows: list):
    g._write(_file(name), rows)


def _nonce() -> str:
    return secrets.token_hex(3)


# ---------------------------------------------------------------- small request builders
def _pt(v: float) -> dict:
    return {"magnitude": round(v, 2), "unit": "PT"}


def _box(page: str, x, y, w, h) -> dict:
    return {"pageObjectId": page, "size": {"width": _pt(w), "height": _pt(h)},
            "transform": {"scaleX": 1, "scaleY": 1, "translateX": x, "translateY": y, "unit": "PT"}}


def _solid(hexs: str) -> dict:
    return {"solidFill": {"color": {"rgbColor": rgb(hexs)}}}


def _text_style(oid: str, size: float, bold: bool, font: str, color: str, cell=None) -> dict:
    r = {"objectId": oid, "style": {"fontFamily": font, "fontSize": _pt(size), "bold": bold,
                                    "foregroundColor": {"opaqueColor": {"rgbColor": rgb(color)}}},
         "textRange": {"type": "ALL"}, "fields": "fontFamily,fontSize,bold,foregroundColor"}
    if cell:
        r["cellLocation"] = cell
    return {"updateTextStyle": r}


def _area(el: dict) -> dict:
    """An element's place on the page as size + transform in EMU, with the scale folded into the size."""
    s, t = el.get("size") or {}, el.get("transform") or {}
    k = EMU if t.get("unit") == "PT" else 1
    ks = EMU if (s.get("width") or {}).get("unit") == "PT" else 1
    w = (s.get("width") or {}).get("magnitude", 3000000) * ks * t.get("scaleX", 1)
    h = (s.get("height") or {}).get("magnitude", 2000000) * ks * t.get("scaleY", 1)
    return {"size": {"width": {"magnitude": w, "unit": "EMU"}, "height": {"magnitude": h, "unit": "EMU"}},
            "transform": {"scaleX": 1, "scaleY": 1, "translateX": t.get("translateX", 0) * k, "translateY": t.get("translateY", 0) * k, "unit": "EMU"}}


def _props(page: str, area: dict) -> dict:
    return {"pageObjectId": page, **area}


def _shrink(area: dict, pts: float) -> tuple[dict, dict]:
    """Split an area: (the area minus a strip of `pts` at the bottom, the strip) for a caption."""
    a = json.loads(json.dumps(area))
    a["size"]["height"]["magnitude"] -= pts * EMU
    strip = {"size": {"width": dict(area["size"]["width"]), "height": {"magnitude": pts * EMU, "unit": "EMU"}},
             "transform": {**area["transform"], "translateY": area["transform"]["translateY"] + a["size"]["height"]["magnitude"]}}
    return a, strip


def table_requests(tid: str, page: str, area: dict, header: list, rows: list, theme: dict) -> list[dict]:
    body = [list(header)] + [list(r) for r in rows] if header else [list(r) for r in rows]
    ncols = max(len(r) for r in body)
    body = [[str(c if c is not None else "") for c in r] + [""] * (ncols - len(r)) for r in body]
    size = 12 if len(body) <= 6 else 11 if len(body) <= 9 else 10
    reqs = [{"createTable": {"objectId": tid, "elementProperties": _props(page, area), "rows": len(body), "columns": ncols}}]
    for r, row in enumerate(body):
        for c, cell in enumerate(row):
            if not cell.strip():
                continue
            loc = {"rowIndex": r, "columnIndex": c}
            reqs.append({"insertText": {"objectId": tid, "cellLocation": loc, "text": cell}})
            reqs.append(_text_style(tid, size, bool(header) and r == 0, theme["body_font"], theme["light"] if header and r == 0 else theme["text"], loc))
    if header:
        reqs.append({"updateTableCellProperties": {"objectId": tid, "tableRange": {"location": {"rowIndex": 0, "columnIndex": 0}, "rowSpan": 1, "columnSpan": ncols},
                                                   "tableCellProperties": {"tableCellBackgroundFill": _solid(theme["dark"])},
                                                   "fields": "tableCellBackgroundFill.solidFill.color"}})
    return reqs


def chart_request(cid: str, page: str, area: dict, sheet_id: str, chart_id) -> dict:
    return {"createSheetsChart": {"objectId": cid, "spreadsheetId": sheet_id, "chartId": int(chart_id), "linkingMode": "LINKED",
                                  "elementProperties": _props(page, area)}}


# ---------------------------------------------------------------- the built-in token template
def template_requests(theme: dict) -> list[dict]:
    """Requests that turn an empty presentation into a token template, one slide per layout."""
    t = {**THEME, **(theme or {})}
    reqs = []
    for li, layout in enumerate(LAYOUTS):
        sid = f"afraT_{layout}"
        dark = layout in DARK
        reqs.append({"createSlide": {"objectId": sid, "insertionIndex": li, "slideLayoutReference": {"predefinedLayout": "BLANK"}}})
        reqs.append({"updatePageProperties": {"objectId": sid, "pageProperties": {"pageBackgroundFill": _solid(t["dark"] if dark else t["light"])},
                                              "fields": "pageBackgroundFill.solidFill.color"}})
        bar = f"{sid}_bar"
        bx, by, bw = (48, 112, 72) if dark else (40, 80, 56)
        reqs.append({"createShape": {"objectId": bar, "shapeType": "RECTANGLE", "elementProperties": _box(sid, bx, by, bw, 5)}})
        reqs.append({"updateShapeProperties": {"objectId": bar, "shapeProperties": {"shapeBackgroundFill": _solid(t["accent"]), "outline": {"propertyState": "NOT_RENDERED"}},
                                               "fields": "shapeBackgroundFill.solidFill.color,outline.propertyState"}})
        for n, (token, x, y, w, h, role) in enumerate(BOXES[layout]):
            oid = f"{sid}_{n}"
            size, bold, face = ROLES[role]
            color = t["light"] if dark else (t["muted"] if role == "small" else t["text"])
            if dark and role == "sub":
                color = t["accent"]
            reqs.append({"createShape": {"objectId": oid, "shapeType": "RECTANGLE" if role == "area" else "TEXT_BOX", "elementProperties": _box(sid, x, y, w, h)}})
            reqs.append({"insertText": {"objectId": oid, "text": "{{%s}}" % token}})
            reqs.append(_text_style(oid, size, bold, t["heading_font" if face == "heading" else "body_font"], t["muted"] if role == "area" else color))
            if role == "area":
                reqs.append({"updateShapeProperties": {"objectId": oid, "shapeProperties": {"shapeBackgroundFill": _solid("#F1F3F6"),
                             "outline": {"dashStyle": "DASH", "outlineFill": _solid("#B8BFCC")}, "contentAlignment": "MIDDLE"},
                             "fields": "shapeBackgroundFill.solidFill.color,outline.dashStyle,outline.outlineFill.solidFill.color,contentAlignment"}})
                reqs.append({"updateParagraphStyle": {"objectId": oid, "style": {"alignment": "CENTER"}, "textRange": {"type": "ALL"}, "fields": "alignment"}})
            elif role in ("hero", "h1", "h2"):
                reqs.append({"updateShapeProperties": {"objectId": oid, "shapeProperties": {"contentAlignment": "BOTTOM"}, "fields": "contentAlignment"}})
            else:
                reqs.append({"updateParagraphStyle": {"objectId": oid, "style": {"spaceBelow": _pt(8 if role == "body" else 4), "lineSpacing": 105},
                                                      "textRange": {"type": "ALL"}, "fields": "spaceBelow,lineSpacing"}})
            if role == "body":
                reqs.append({"createParagraphBullets": {"objectId": oid, "textRange": {"type": "ALL"}, "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"}})
    return reqs


def _text_of(el: dict) -> str:
    return "".join(te.get("textRun", {}).get("content", "") for te in ((el.get("shape") or {}).get("text") or {}).get("textElements", []))


def _notes_shape(slide: dict) -> tuple[str, str]:
    """(speaker notes shape id, its text)."""
    notes = (slide.get("slideProperties") or {}).get("notesPage") or {}
    nid = (notes.get("notesProperties") or {}).get("speakerNotesObjectId", "")
    for el in notes.get("pageElements", []):
        if el.get("objectId") == nid:
            return nid, _text_of(el)
    return nid, ""


def infer_layout(slide: dict) -> str:
    m = re.search(r"afra:layout=(\w+)", _notes_shape(slide)[1])
    if m and m.group(1) in LAYOUTS:
        return m.group(1)
    tokens = set(TOKEN_RE.findall(" ".join(_text_of(e) for e in slide.get("pageElements", []))))
    for tok, layout in (("table", "table"), ("chart", "chart"), ("figure", "figure"), ("image", "figure"), ("left", "two_column")):
        if tok in tokens:
            return layout
    if "body" in tokens:
        return "content"
    if "subtitle" in tokens:
        return "title"
    return "section" if "title" in tokens else ""


def token_slides(pres: dict) -> dict:
    """layout -> the template slide that has it (first one wins)."""
    out = {}
    for s in pres.get("slides", []):
        L = infer_layout(s)
        if L and L not in out:
            out[L] = s
    return out


# ---------------------------------------------------------------- real decks: pick a layout for each kind of slide
def _placeholders(layout: dict) -> list[dict]:
    out = []
    for el in layout.get("pageElements", []):
        ph = (el.get("shape") or {}).get("placeholder")
        if ph and ph.get("type") not in ("SLIDE_NUMBER", "FOOTER", "DATE_AND_TIME", "HEADER", "SLIDE_IMAGE"):
            out.append({"type": ph["type"], "index": ph.get("index", 0), "el": el, "parent": ph.get("parentObjectId", "")})
    return out


def map_layouts(pres: dict) -> dict:
    """Our layout name -> {"id", "ph": placeholders} from the deck's own layouts, by placeholder types and names."""
    masters = {e["objectId"]: e for m in pres.get("masters", []) for e in m.get("pageElements", [])}
    cands = []
    for lay in pres.get("layouts", []):
        phs = _placeholders(lay)
        for p in phs:  # placeholders often inherit their place from the master
            if "size" not in p["el"] and p["parent"] in masters:
                p["el"] = {**masters[p["parent"]], **p["el"]}
        props = lay.get("layoutProperties") or {}
        name = f"{props.get('name', '')} {props.get('displayName', '')}".lower().replace("_", " ")
        types = [p["type"] for p in phs]
        cands.append({"id": lay["objectId"], "ph": phs, "name": name, "title": "TITLE" in types, "center": "CENTERED_TITLE" in types,
                      "sub": "SUBTITLE" in types, "body": types.count("BODY"), "pic": "PICTURE" in types})

    def score(c, kind):
        s = 0
        if kind == "title":
            s = 3 * c["center"] + 3 * bool(re.search(r"title slide|^\s*title\s*$|cover", c["name"])) + c["sub"] - c["body"]
        elif kind == "section":
            s = 3 * bool(re.search(r"section", c["name"])) + (c["title"] or c["center"]) * (c["body"] <= 1)
        elif kind == "content":
            s = 3 * (c["title"] and c["body"] == 1) + 2 * bool(re.search(r"title and (body|content|text)|title, content", c["name"])) - 2 * c["pic"]
        elif kind == "two_column":
            s = 3 * (c["title"] and c["body"] == 2) + 2 * bool(re.search(r"two|comparison|2 col", c["name"]))
        return s

    out = {}
    for kind in ("title", "section", "content", "two_column"):
        best = max(cands, key=lambda c: score(c, kind), default=None)
        if best and score(best, kind) > 0:
            out[kind] = best
    if "content" not in out:
        withtitle = [c for c in cands if c["title"]]
        if withtitle:
            out["content"] = withtitle[0]
    for kind in ("figure", "table", "chart", "references"):
        if "content" in out:
            out[kind] = out["content"]
    out.setdefault("closing", out.get("section") or out.get("title"))
    out.setdefault("section", out.get("title"))
    out.setdefault("title", out.get("section"))
    return {k: v for k, v in out.items() if v}


# ---------------------------------------------------------------- what goes in each box
def _col(c) -> str:
    c = c or {}
    return "\n".join(([c["heading"]] if c.get("heading") else []) + list(c.get("bullets") or []))


def slide_texts(s: dict) -> dict:
    L = s.get("layout", "content")
    t = {"title": s.get("title", ""), "subtitle": s.get("subtitle", "")}
    if L in ("content", "references"):
        t["body"] = "\n".join(s.get("bullets") or [])
    if L == "two_column":
        t["left"], t["right"] = _col(s.get("left")), _col(s.get("right"))
    if L in ("figure", "table", "chart", "image_left", "image_right"):
        t["caption"] = s.get("caption") or (s.get("figure") or {}).get("caption", "")
    if L in ("image_left", "image_right"):
        t["body"] = chr(10).join(s.get("bullets") or [])
    if L == "quote":
        q = s.get("quote") or {}
        t["body"] = q.get("text", "")
        t["subtitle"] = t["subtitle"] or q.get("by", "")
    return t


def as_layout(s: dict, available) -> tuple[dict, str]:
    """Bend a slide onto a layout the template has; returns (slide, note or "")."""
    L = s.get("layout", "content")
    seen = set()
    while L not in available and L not in seen:
        seen.add(L)
        L = FALLBACK.get(L, "content")
    if L == s.get("layout"):
        return s, ""
    s2 = {**s, "layout": L}
    if L == "content":
        if s.get("layout") == "two_column":
            s2["bullets"] = [x for c in (s.get("left"), s.get("right")) for x in _col(c).split("\n") if x]
        elif s.get("layout") == "table":
            tb = s.get("table") or {}
            s2["bullets"] = [" | ".join(map(str, r)) for r in ([tb.get("header")] if tb.get("header") else []) + list(tb.get("rows") or [])]
        elif s.get("layout") in ("figure", "chart"):
            s2["bullets"] = [x for x in [s.get("caption", "")] if x]
        elif s.get("layout") == "quote":
            q = s.get("quote") or {}
            s2["bullets"] = [x for x in [q.get("text", ""), ("- " + q["by"]) if q.get("by") else ""] if x]
    return s2, f"the template has no {s.get('layout')} layout, used {L}"


# ---------------------------------------------------------------- overflow: split, trim and flag
def _trim(text: str, n: int) -> str:
    text = str(text or "").strip()
    if len(text) <= n:
        return text
    cut = text[:n].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut + "…"


def lines_needed(text: str, width_pt: float, size: float) -> int:
    cpl = max(8, int(width_pt / (size * 0.5)))
    return sum(max(1, math.ceil(len(p) / cpl)) for p in str(text).split("\n"))


def overflows(text: str, width_pt: float, height_pt: float, size: float) -> bool:
    return lines_needed(text, width_pt, size) * size * 1.25 > height_pt + 2


def fit_slides(slides: list[dict], max_bullets: int = MAX_BULLETS, max_chars: int = MAX_CHARS) -> tuple[list[dict], list[dict]]:
    """Long points are trimmed, crowded slides become "(cont.)" slides, big tables are cut into pages.
    Returns (slides, flags); a flag is {"slide": 1-based number in the result, "issue": text}."""
    out, flags = [], []

    def flag(issue):
        flags.append({"slide": len(out) + 1, "issue": issue})

    for s in slides:
        s = {**s, "layout": s.get("layout") if s.get("layout") in KNOWN else "content"}
        if len(s.get("title") or "") > 90:
            s["title"] = _trim(s["title"], 90)
            flag("title shortened")
        L = s["layout"]
        if L in ("content", "references"):
            limit = 16 if L == "references" else max_bullets
            width, size, height = (640, 11, 296) if L == "references" else (640, 18, 284)
            items = []
            for b in s.get("bullets") or []:
                if L == "content" and len(str(b)) > max_chars:
                    flag("a long point was shortened")
                    b = _trim(b, max_chars)
                items.append(str(b))
            parts, cur = [], []
            for b in items:
                if cur and (len(cur) >= limit or overflows("\n".join(cur + [b]), width, height, size)):
                    parts.append(cur)
                    cur = []
                cur.append(b)
            parts.append(cur)
            for i, p in enumerate(parts):
                if i:
                    flag("too much for one slide: continued here")
                out.append({**s, "bullets": p, "title": s.get("title", "") + (" (cont.)" if i else ""), "notes": s.get("notes", "") if not i else ""})
            continue
        if L == "two_column":
            left, right = dict(s.get("left") or {}), dict(s.get("right") or {})
            lb = [_trim(x, 110) for x in left.get("bullets") or []]
            rb = [_trim(x, 110) for x in right.get("bullets") or []]
            n = max_bullets - 1
            out.append({**s, "left": {**left, "bullets": lb[:n]}, "right": {**right, "bullets": rb[:n]}})
            if len(lb) > n or len(rb) > n:
                flag("columns too long: continued on the next slide")
                out.append({**s, "title": s.get("title", "") + " (cont.)", "notes": "", "left": {**left, "bullets": lb[n:]}, "right": {**right, "bullets": rb[n:]}})
            continue
        if L == "table":
            tb = s.get("table") or {}
            header, rows = list(tb.get("header") or []), [list(r) for r in tb.get("rows") or []]
            if not rows and not header:
                flag("table slide without data")
                out.append({**s, "layout": "content", "bullets": [s.get("caption", "")] if s.get("caption") else []})
                continue
            if max(len(header), *(len(r) for r in rows or [[]])) > MAX_COLS:
                flag(f"table had more than {MAX_COLS} columns: extra columns left out")
                header, rows = header[:MAX_COLS], [r[:MAX_COLS] for r in rows]
            rows = [[_trim(c, 60) for c in r] for r in rows]
            for i in range(0, max(1, len(rows)), MAX_ROWS):
                if i:
                    flag("long table: continued here")
                out.append({**s, "title": s.get("title", "") + (" (cont.)" if i else ""), "notes": s.get("notes", "") if not i else "",
                            "table": {"header": header, "rows": rows[i:i + MAX_ROWS]}})
            continue
        if L in ("figure", "chart", "title", "section", "closing"):
            if len(s.get("subtitle") or "") > 160:
                s["subtitle"] = _trim(s["subtitle"], 160)
                flag("subtitle shortened")
            if len(s.get("caption") or "") > 240:
                s["caption"] = _trim(s["caption"], 240)
                flag("caption shortened")
        out.append(s)
    return out, flags


# ---------------------------------------------------------------- one slide -> batchUpdate requests
def token_slide_requests(sid: str, tpl: dict, s: dict, theme: dict) -> tuple[list[dict], dict, list[str]]:
    """Duplicate a template slide under known ids and fill it. Returns (requests, record, notes for the review)."""
    ids = {tpl["objectId"]: sid}
    owner = {}  # token -> new element id
    area_el = None
    for n, el in enumerate(tpl.get("pageElements", [])):
        ids[el["objectId"]] = f"{sid}_{n}"
        for tok in TOKEN_RE.findall(_text_of(el)):
            owner.setdefault(tok, (f"{sid}_{n}", el))
            if tok in ("table", "chart", "figure", "image"):
                area_el = (tok, f"{sid}_{n}", el)
    reqs = [{"duplicateObject": {"objectId": tpl["objectId"], "objectIds": ids}}]
    texts, notes = slide_texts(s), []
    rec = {"id": sid, "layout": s["layout"], "fields": {}, "area": None, "element": None}
    for tok, (oid, el) in owner.items():
        if tok in ("table", "chart", "figure", "image"):
            continue
        rec["fields"][tok] = oid
    for tok in sorted(owner):
        if tok in ("table", "chart", "figure", "image"):
            continue
        reqs.append({"replaceAllText": {"containsText": {"text": "{{%s}}" % tok, "matchCase": True}, "replaceText": texts.get(tok, ""), "pageObjectIds": [sid]}})
    for tok, (oid, el) in owner.items():  # a box left with nothing in it is removed (no empty bullet)
        if tok in rec["fields"] and not TOKEN_RE.sub(lambda m: texts.get(m.group(1), ""), _text_of(el)).strip():
            reqs.append({"deleteObject": {"objectId": oid}})
            rec["fields"].pop(tok, None)
    if area_el:
        tok, oid, el = area_el
        area = _area(el)
        rec["area"] = area
        L = s["layout"]
        if L == "table" and (s.get("table") or {}).get("rows"):
            rec["element"] = f"{sid}_tbl"
            reqs.append({"deleteObject": {"objectId": oid}})
            reqs += table_requests(rec["element"], sid, area, s["table"].get("header") or [], s["table"]["rows"], theme)
        elif L == "chart" and (s.get("chart") or {}).get("sheet_id") and str((s.get("chart") or {}).get("chart_id", "")).strip():
            rec["element"] = f"{sid}_cht"
            reqs.append({"deleteObject": {"objectId": oid}})
            reqs.append(chart_request(rec["element"], sid, area, s["chart"]["sheet_id"], s["chart"]["chart_id"]))
        elif (s.get("figure") or {}).get("url"):
            reqs.append({"replaceAllShapesWithImage": {"imageUrl": s["figure"]["url"], "imageReplaceMethod": "CENTER_INSIDE",
                                                       "containsText": {"text": "{{%s}}" % tok, "matchCase": True}, "pageObjectIds": [sid]}})
        else:
            reqs.append({"replaceAllText": {"containsText": {"text": "{{%s}}" % tok, "matchCase": True},
                                            "replaceText": "Add the " + ("chart" if L == "chart" else "figure" if L == "figure" else "table") + " here", "pageObjectIds": [sid]}})
            notes.append(f"no {L if L in ('chart', 'figure', 'table') else 'figure'} to insert: add it by hand")
    return reqs, rec, notes


def layout_slide_requests(sid: str, lay: dict, s: dict, theme: dict, page_size: dict) -> tuple[list[dict], dict, list[str]]:
    """A new slide from one of the deck's own layouts; text goes into its placeholders."""
    texts, notes = slide_texts(s), []
    maps, fields = [], {}
    bodies = [p for p in lay["ph"] if p["type"] in ("BODY", "OBJECT")]
    want = {"title": next((p for p in lay["ph"] if p["type"] in ("TITLE", "CENTERED_TITLE")), None),
            "subtitle": next((p for p in lay["ph"] if p["type"] == "SUBTITLE"), None)}
    L = s["layout"]
    if L == "two_column":
        want["left"], want["right"] = (bodies + [None, None])[:2]
    elif L in ("content", "references"):
        want["body"] = bodies[0] if bodies else None
    elif L in ("figure", "table", "chart"):
        want["_area"] = bodies[0] if bodies else None
    if L in ("section", "closing", "title") and not want["subtitle"] and bodies:
        want["subtitle"] = bodies[0]
    for f, p in want.items():
        if p:
            oid = f"{sid}_{f.strip('_')}"
            maps.append({"layoutPlaceholder": {"type": p["type"], "index": p["index"]}, "objectId": oid})
            fields[f] = (oid, p)
    reqs = [{"createSlide": {"objectId": sid, "slideLayoutReference": {"layoutId": lay["id"]}, "placeholderIdMappings": maps}}]
    rec = {"id": sid, "layout": L, "fields": {}, "area": None, "element": None}
    for f, (oid, p) in fields.items():
        if f == "_area":
            continue
        if texts.get(f):
            reqs.append({"insertText": {"objectId": oid, "text": texts[f]}})
            rec["fields"][f] = oid
        else:
            reqs.append({"deleteObject": {"objectId": oid}})
    if L in ("figure", "table", "chart"):
        if "_area" in fields:
            area = _area(fields["_area"][1]["el"])
            reqs.append({"deleteObject": {"objectId": fields["_area"][0]}})
        else:
            W, H = page_size["width"]["magnitude"], page_size["height"]["magnitude"]
            area = {"size": {"width": {"magnitude": W * .88, "unit": "EMU"}, "height": {"magnitude": H * .66, "unit": "EMU"}},
                    "transform": {"scaleX": 1, "scaleY": 1, "translateX": W * .06, "translateY": H * .24, "unit": "EMU"}}
        if texts.get("caption"):
            area, strip = _shrink(area, 28)
            cap = f"{sid}_cap"
            reqs += [{"createShape": {"objectId": cap, "shapeType": "TEXT_BOX", "elementProperties": _props(sid, strip)}},
                     {"insertText": {"objectId": cap, "text": texts["caption"]}},
                     _text_style(cap, 12, False, theme["body_font"], theme["muted"])]
            rec["fields"]["caption"] = cap
        rec["area"] = area
        if L == "table" and (s.get("table") or {}).get("rows"):
            rec["element"] = f"{sid}_tbl"
            reqs += table_requests(rec["element"], sid, area, s["table"].get("header") or [], s["table"]["rows"], theme)
        elif L == "chart" and (s.get("chart") or {}).get("sheet_id"):
            rec["element"] = f"{sid}_cht"
            reqs.append(chart_request(rec["element"], sid, area, s["chart"]["sheet_id"], s["chart"].get("chart_id", 0)))
        elif (s.get("figure") or {}).get("url"):
            rec["element"] = f"{sid}_img"
            reqs.append({"createImage": {"objectId": rec["element"], "url": s["figure"]["url"], "elementProperties": _props(sid, area)}})
        else:
            notes.append(f"no {L} to insert: add it by hand")
    return reqs, rec, notes


def notes_requests(pres: dict, wanted: dict) -> list[dict]:
    """Speaker notes: clear the template's marker, write the new notes. wanted: slide id -> notes text."""
    reqs = []
    for slide in pres.get("slides", []):
        if slide["objectId"] not in wanted:
            continue
        nid, old = _notes_shape(slide)
        if not nid:
            continue
        if old.strip():
            reqs.append({"deleteText": {"objectId": nid, "textRange": {"type": "ALL"}}})
        if (wanted[slide["objectId"]] or "").strip():
            reqs.append({"insertText": {"objectId": nid, "text": wanted[slide["objectId"]].strip()}})
    return reqs


def build_requests(pres: dict, slides: list[dict], template: dict, nonce: str) -> tuple[list[dict], list[dict], list[dict]]:
    """The whole deck as one request list (structure, text, tables, charts, images, then the template's own slides
    removed). Returns (requests, slide records, flags)."""
    theme = {**THEME, **(template.get("theme") or {})}
    originals = [s["objectId"] for s in pres.get("slides", [])]
    reqs, recs, flags = [], [], []
    if template.get("kind") == "layout":
        lays = map_layouts(pres)
        if not lays:
            raise ValueError("This template has no usable layouts. Use it as a colour style instead.")
    else:
        lays = token_slides(pres)
        if not lays:
            raise ValueError("This template has no {{title}} / {{body}} placeholders.")
    for i, s in enumerate(slides):
        s, note = as_layout(s, lays)
        if note:
            flags.append({"slide": i + 1, "issue": note})
        sid = f"a{nonce}_{i:03d}"
        if template.get("kind") == "layout":
            r, rec, notes = layout_slide_requests(sid, lays[s["layout"]], s, theme, pres.get("pageSize") or {"width": {"magnitude": 9144000}, "height": {"magnitude": 5143500}})
        else:
            r, rec, notes = token_slide_requests(sid, lays[s["layout"]], s, theme)
        reqs += r
        recs.append(rec)
        flags += [{"slide": i + 1, "issue": n} for n in notes]
    reqs += [{"deleteObject": {"objectId": o}} for o in originals]
    if template.get("kind") != "layout":  # duplicates sit next to their template slide: put them in order
        reqs += [{"updateSlidesPosition": {"slideObjectIds": [r["id"]], "insertionIndex": i}} for i, r in enumerate(recs)]
    return reqs, recs, flags


# ---------------------------------------------------------------- review after building
CITE_RE = re.compile(r"\([^()]*\d{4}[a-z]?\)|\[\d+(?:\s*[,–-]\s*\d+)*\]|\[\?[^\]]+\]")
CLAIM_RE = re.compile(r"\d+(\.\d+)?\s?%|\b(19|20)\d{2}\b|\b(increase|decrease|reduc|improv|significant|shown|found|reported)", re.I)


def review(pres: dict, recs: list[dict], slides: list[dict], missing: list[str] = ()) -> list[dict]:
    flags = []
    ours = {r["id"]: i for i, r in enumerate(recs)}
    for slide in pres.get("slides", []):
        i = ours.get(slide["objectId"])
        if i is None:
            continue
        for el in slide.get("pageElements", []):
            text = _text_of(el)
            if "{{" in text:
                flags.append({"slide": i + 1, "issue": "a {{placeholder}} is left: " + ", ".join(sorted(set(TOKEN_RE.findall(text))))})
            if not text.strip() or "size" not in el:
                continue
            a = _area(el)
            w, h = a["size"]["width"]["magnitude"] / EMU, a["size"]["height"]["magnitude"] / EMU
            sizes = [te["textRun"]["style"]["fontSize"]["magnitude"] for te in el["shape"]["text"].get("textElements", [])
                     if "fontSize" in (te.get("textRun") or {}).get("style", {})]
            if overflows(text.strip(), w - 14, h - 8, max(sizes) if sizes else 18):
                flags.append({"slide": i + 1, "issue": "text may run out of its box: shorten it or split the slide"})
    cited = False
    for i, s in enumerate(slides):
        body = " ".join([s.get("title", "")] + list(s.get("bullets") or []) + [_col(s.get("left")), _col(s.get("right")), s.get("caption", "")])
        if CITE_RE.search(body):
            cited = True
        elif s.get("layout") in ("content", "two_column", "table", "chart") and CLAIM_RE.search(body):
            flags.append({"slide": i + 1, "issue": "a factual claim without a source"})
    if cited and not any(s.get("layout") == "references" for s in slides):
        flags.append({"slide": len(slides), "issue": "sources are cited but there is no References slide"})
    if missing:
        flags.append({"slide": 0, "issue": "cited but not in the library (left as ?key): " + ", ".join(missing)})
    return flags


# ---------------------------------------------------------------- designs in Google: one template per design and account
def _token_file(email: str, t: dict, name: str) -> dict:
    """A new token template (one slide per layout) in "A.F.R.A Decks/Templates", in a design's colours and fonts."""
    folders = g.ensure_folders(email)
    f = g.create_file(name, g.MIME_SLIDES, folders["templates"], email)
    pres = g.presentation(f["id"], email)
    reqs = template_requests(t)
    reqs += [{"deleteObject": {"objectId": s["objectId"]}} for s in pres.get("slides", [])]
    g.batch_update(f["id"], reqs, email)
    pres = g.presentation(f["id"], email)
    marks = [{"insertText": {"objectId": _notes_shape(s)[0], "text": f"afra:layout={s['objectId'].split('_', 1)[1]}"}}
             for s in pres.get("slides", []) if s["objectId"].startswith("afraT_") and _notes_shape(s)[0]]
    if marks:
        g.batch_update(f["id"], marks, email)
    return {"file_id": f["id"], "url": f.get("webViewLink", ""), "kind": "token"}


def _layout_info(email: str, f: dict) -> dict:
    pres = g.presentation(f["id"], email)
    kind = "layout" if map_layouts(pres) else "token" if token_slides(pres) else ""
    if not kind:
        raise ValueError("Google couldn't find usable layouts in that file.")
    return {"file_id": f["id"], "url": f.get("webViewLink", ""), "kind": kind, "layouts": sorted(map_layouts(pres))}


def google_template(design: dict, email: str, step=lambda s: None) -> dict:
    """The design's template in this account, made on first use. Returns {file_id, kind, theme, name, note}."""
    t = designs.theme(design)
    have = (design.get("google") or {}).get(email)
    if have:
        try:
            if not g.file_meta(have["file_id"], email).get("trashed"):
                return {**have, "theme": t, "name": design["name"]}
        except g.GoogleError:
            pass
    step(f"Preparing the design \"{design['name']}\" in your Drive (first time only)…")
    folders = g.ensure_folders(email)
    info, note = None, ""
    data = designs.pptx_bytes(design["id"]) if design.get("kind") == "pptx" else None
    if data:
        f = g.upload(data, design["name"], g.MIME_PPTX, folders["templates"], g.MIME_SLIDES, email)
        try:
            info = _layout_info(email, f)
        except ValueError:
            note = "its layouts couldn't be used, so its colours and fonts are"
    elif design.get("kind") == "slides" and design.get("source_url"):
        try:
            f = g.copy_file(g.parse_id(design["source_url"]), design["name"], folders["templates"], email)
            info = _layout_info(email, f)
        except (g.GoogleError, ValueError):
            note = ("Google lets A.F.R.A copy only files it made or files you pick, so this account uses the deck's colours and fonts. "
                    "For the exact layouts: in Google Slides use File › Download › Microsoft PowerPoint (.pptx) and add that file as a design.")
    if not info:
        info = _token_file(email, t, design["name"])
    info["note"] = note
    designs.set_google(design["id"], email, info)
    return {**info, "theme": t, "name": design["name"]}


def add_slides_design(link: str, email: str | None = None) -> dict:
    """A Google Slides link becomes a design: its colours and fonts now, its layouts when Google lets A.F.R.A copy it."""
    email, _ = g.access_token(email)
    pid = g.parse_id(link)
    pres = g.presentation(pid, email)  # the presentations scope reads any deck this account can open
    t = designs.slides_theme(pres)
    rec = {"id": designs.new_id(), "name": (pres.get("title") or "Google Slides design")[:120], "kind": "slides", "theme": t,
           "layouts": sorted(map_layouts(pres)), "source": "Google Slides link", "source_url": f"https://docs.google.com/presentation/d/{pid}/edit",
           "default": False, "added": time.time()}
    designs.put(rec)
    try:
        if pres.get("slides"):
            data = g.thumbnail(pid, pres["slides"][0]["objectId"], email, "SMALL")
            designs.DIR.mkdir(parents=True, exist_ok=True)
            (designs.DIR / f"{rec['id']}.png").write_bytes(data)
    except (g.GoogleError, httpx.HTTPError, KeyError):
        pass
    info = google_template(designs.get(rec["id"]), email)
    return {**designs.update(rec["id"]), "note": info.get("note", "")}


def add_designs(text: str, email: str | None = None) -> dict:
    """One or several links at once: template pages (Slidesgo and similar) and Google Slides links."""
    links = designs.split_links(text)
    if not links:
        raise ValueError("Paste one or more links (one per line).")
    added, errors = [], []
    for u in links:
        try:
            if designs.is_slides_link(u):
                added.append(add_slides_design(u, email or None))
            else:
                added.append(designs.add_web(u))
        except (ValueError, g.GoogleError) as e:
            errors.append({"url": u, "error": str(e)})
    return {"added": added, "errors": errors, "how": designs.HOW}


def forget_design(did: str) -> dict:
    """Delete a design; its Google copies go to the Drive bin (restorable there)."""
    r = designs.delete(did)
    for email, info in (r.get("google") or {}).items():
        try:
            g.trash(info["file_id"], email)
        except (g.GoogleError, httpx.HTTPError):
            pass
    return {"deleted": did}


# ---------------------------------------------------------------- material for the AI
def add_material(filename: str, data: bytes) -> dict:
    from .gaps import manuscript_text
    text = manuscript_text(filename, data).strip()
    if not text:
        raise ValueError(f"No text found in {filename}.")
    mid = secrets.token_hex(5)
    MATERIALS[mid] = {"name": filename, "text": text[:60000], "added": time.time()}
    for k in sorted(MATERIALS, key=lambda k: MATERIALS[k]["added"])[:-20]:
        MATERIALS.pop(k, None)
    return {"id": mid, "name": filename, "chars": len(text)}


def read_sheet(link: str, rng: str = "", email: str | None = None) -> dict:
    sid = g.parse_id(link)
    meta = g.sheet_meta(sid, email)
    first = rng or (f"'{meta['tabs'][0]['title']}'!A1:L60" if meta["tabs"] else "A1:L60")
    return {**meta, "range": first, "values": g.sheet_values(sid, first, email)[:60]}


def upload_sheet(data: bytes, filename: str, email: str | None = None) -> dict:
    """An .xlsx/.csv becomes a Google Sheet in A.F.R.A Decks (so its charts can be linked)."""
    email, _ = g.access_token(email)
    folders = g.ensure_folders(email)
    mime = "text/csv" if filename.lower().endswith(".csv") else g.MIME_XLSX
    f = g.upload(data, "Data - " + Path(filename).stem, mime, folders["decks"], g.MIME_SHEET, email)
    return read_sheet(f["id"], "", email)


def _project_style(pid: str) -> str:
    return next((p.get("citation_style") or "apa" for p in db.list_projects() if p["id"] == pid), "apa")


def materials_text(body: dict) -> str:
    from .writer import library_context
    parts = []
    for mid in body.get("materials") or []:
        m = MATERIALS.get(mid)
        if m:
            parts.append(f"### File: {m['name']}\n{m['text'][:14000]}")
    if (body.get("sources_text") or "").strip():  # several sources read together (sources.digest): tagged blocks [S1], [S2]...
        parts.append("### Sources (each block starts with its tag)\n" + body["sources_text"].strip()[:52000])
    if (body.get("pasted") or "").strip():
        parts.append("### Notes from the user\n" + body["pasted"].strip()[:20000])
    if body.get("document_id"):
        d = db.get_document(body["document_id"])
        if d:
            parts.append(f"### A.F.R.A document: {d['title']}\n{(d.get('content') or '')[:20000]}")
    if body.get("project_id") and (body.get("use_library") or body.get("keys")):
        lib = library_context(body["project_id"], keys=body.get("keys") or None, max_items=25, chars=500, query=body.get("topic") or body.get("title") or "")
        parts.append("### Library papers (cite as [@key], only these keys)\n" + lib)
    sh = body.get("sheet") or {}
    if sh.get("id"):
        rows = sh.get("values") or []
        table = "\n".join("\t".join(str(c) for c in r[:12]) for r in rows[:40])
        charts = [f"- sheet {sh['id']} chart {c['id']}: {c['title']} (tab {t['title']})" for t in sh.get("tabs", []) for c in t.get("charts", [])]
        parts.append(f"### Google Sheet: {sh.get('title', '')}\n{table}" + ("\nCharts you can place (layout \"chart\"):\n" + "\n".join(charts) if charts else ""))
    imgs = [u for u in body.get("images") or [] if str(u).startswith(("http://", "https://"))]
    if imgs:
        parts.append("### Image links you can place (layout \"figure\")\n" + "\n".join(imgs))
    return "\n\n".join(parts)[:60000] or "(no material: use general, well-established knowledge and say so in the notes)"


SYSTEM = ("You design clear, honest research presentations for students and researchers. Slides carry short points, "
          "not paragraphs; the speaker notes carry the explanation. Use only facts, numbers and sources from the material.")
LAYOUT_HELP = """- title: first slide (title + subtitle)
- section: divider between parts (title + one line)
- content: title + 3-6 short bullet points
- two_column: comparison, before/after, pros/cons (two short lists, each with a heading)
- table: numbers to compare from the material (max 6 columns, 10 rows)
- chart: ONLY a chart listed under "Charts you can place"
- figure: ONLY an image listed under "Image links you can place"
- references: the source list (A.F.R.A writes it from the citations; give no bullets)
- closing: last slide (take-home message, questions)"""
# what the PowerPoint wizard (wizard.py) adds: native charts and pictures that are slots, plus the three extra layouts
WIZARD_HELP = LAYOUT_HELP.replace('- chart: ONLY a chart listed under "Charts you can place"', '- chart: ONLY when the material has numbers to compare; give them as data (see the rules)')     .replace('- figure: ONLY an image listed under "Image links you can place"', '- figure: a picture that fills the slide; ONLY when a picture would help') + """
- image_left / image_right: 2-4 short bullets beside a picture (the picture is a slot the person fills)
- quote: one short, striking quotation or key sentence from the material (with who said it)"""


def _opts(body: dict) -> str:
    minutes = int(body.get("minutes") or 10)
    tone = "make it punchy: bold claims, vivid wording, one idea per slide" if body.get("tone") == "punchy" else "keep it academic: precise, neutral, cited"
    return (f"Title: {body.get('title') or '(suggest one)'}\nTopic / message: {body.get('topic') or ''}\n"
            f"Audience: {body.get('audience') or 'academic audience'}\nTalk length: {minutes} minutes\n"
            f"Language: {body.get('language') or 'the language of the material'} (write every slide in it)\nTone: {tone}")


def slide_count(minutes) -> int:
    return {5: 6, 10: 10, 20: 16}.get(int(minutes or 10), max(5, min(30, int(minutes or 10))))


def _layouts(body: dict) -> tuple[list[str], str]:
    return (KNOWN, WIZARD_HELP) if body.get("wizard") else (LAYOUTS, LAYOUT_HELP)


async def outline(body: dict) -> dict:
    n = max(3, min(40, int(body.get("slides") or 0))) if body.get("slides") else slide_count(body.get("minutes"))
    allowed, help_ = _layouts(body)
    prompt = f"""Plan a presentation of about {n} slides.
{_opts(body)}

Layouts:
{help_}

MATERIAL:
{materials_text(body)}

Start with a title slide; end with references (if any source is cited) and a closing slide.
Return JSON: {{"title": "...", "subtitle": "...", "slides": [{{"layout": "...", "title": "...", "goal": "what this slide must say, one sentence"}}]}}"""
    j = await complete_json(prompt, SYSTEM, task="slides-outline")
    slides = [{"layout": s.get("layout") if s.get("layout") in allowed else "content", "title": str(s.get("title") or "")[:200],
               "goal": str(s.get("goal") or "")[:400]} for s in (j.get("slides") or []) if isinstance(s, dict)]
    if not slides:
        raise ValueError("The AI returned no slides. Try again, or add more material.")
    return {"title": j.get("title") or body.get("title") or slides[0]["title"], "subtitle": j.get("subtitle") or "", "slides": slides}


def _clean(s: dict, o: dict) -> dict:
    s = s if isinstance(s, dict) else {}
    L = o.get("layout") if o.get("layout") in KNOWN else s.get("layout") if s.get("layout") in KNOWN else "content"
    as_list = lambda x: [str(b).strip() for b in (x or []) if str(b).strip()] if isinstance(x, list) else []
    col = lambda c: {"heading": str((c or {}).get("heading") or ""), "bullets": as_list((c or {}).get("bullets"))} if isinstance(c, dict) else {"heading": "", "bullets": []}
    tb = s.get("table") if isinstance(s.get("table"), dict) else {}
    out = {"layout": L, "title": str(s.get("title") or o.get("title") or ""), "subtitle": str(s.get("subtitle") or ""),
           "bullets": as_list(s.get("bullets")), "left": col(s.get("left")), "right": col(s.get("right")),
           "table": {"header": [str(x) for x in tb.get("header") or []], "rows": [[str(c) for c in r] for r in tb.get("rows") or [] if isinstance(r, list)]},
           "chart": s.get("chart") if isinstance(s.get("chart"), dict) else {}, "figure": s.get("figure") if isinstance(s.get("figure"), dict) else {},
           "quote": {"text": str((s.get("quote") or {}).get("text") or ""), "by": str((s.get("quote") or {}).get("by") or "")} if isinstance(s.get("quote"), dict) else {"text": "", "by": ""},
           "caption": str(s.get("caption") or ""), "notes": str(s.get("notes") or "")}
    if isinstance(s.get("sources"), list) and s["sources"]:  # which of several sources (S1, S2...) the slide draws on
        out["sources"] = [str(x).strip()[:8] for x in s["sources"] if str(x).strip()][:6]
    if out["chart"].get("chart_id") is not None and not out["chart"].get("sheet_id"):
        out["chart"]["sheet_id"] = o.get("sheet_id", "")
    return out


def finish_content(slides: list[dict], project_id: str = "", notes: bool = True, style: str = "") -> dict:
    """Citations written out in the project's style, the References slide from the library, then fit_slides()."""
    style = style or (_project_style(project_id) if project_id else "apa")
    missing, refs = [], []
    if project_id:
        SEP = "\n␞\n"
        fields = []
        for s in slides:
            fields += [s["title"], s["subtitle"], s["caption"], s["notes"], s["quote"]["text"], s["quote"]["by"], *s["bullets"], s["left"]["heading"],
                       *s["left"]["bullets"], s["right"]["heading"], *s["right"]["bullets"], *[c for r in s["table"]["rows"] for c in r]]
        text, cited, missing = process_citations(SEP.join(fields), db.library_map(project_id), style)
        out = iter(text.split(SEP))
        for s in slides:
            s["title"], s["subtitle"], s["caption"], s["notes"] = next(out), next(out), next(out), next(out)
            s["quote"]["text"], s["quote"]["by"] = next(out), next(out)
            s["bullets"] = [next(out) for _ in s["bullets"]]
            s["left"]["heading"] = next(out)
            s["left"]["bullets"] = [next(out) for _ in s["left"]["bullets"]]
            s["right"]["heading"] = next(out)
            s["right"]["bullets"] = [next(out) for _ in s["right"]["bullets"]]
            s["table"]["rows"] = [[next(out) for _ in r] for r in s["table"]["rows"]]
        refs = bibliography(cited, style)
    has_refs = [s for s in slides if s["layout"] == "references"]
    if refs:
        if not has_refs:
            at = len(slides) - 1 if slides and slides[-1]["layout"] == "closing" else len(slides)
            slides.insert(at, _clean({"title": "References"}, {"layout": "references"}))
            has_refs = [slides[at]]
        has_refs[0]["bullets"] = refs
        for extra in has_refs[1:]:
            slides.remove(extra)
    else:
        slides = [s for s in slides if s["layout"] != "references" or s["bullets"]]
    if not notes:
        for s in slides:
            s["notes"] = ""
    fitted, flags = fit_slides(slides)
    return {"slides": fitted, "flags": flags, "missing": missing, "style": style}


async def content(body: dict) -> dict:
    plan = body.get("outline") or {}
    outl = [o for o in plan.get("slides") or [] if isinstance(o, dict)]
    if not outl:
        raise ValueError("The outline is empty.")
    sheet_id = (body.get("sheet") or {}).get("id", "")
    mat = materials_text(body)
    listing = "\n".join(f"{i + 1}. [{o.get('layout')}] {o.get('title')}: {o.get('goal', '')}" for i, o in enumerate(outl))
    notes = body.get("notes", True)
    out = []
    if body.get("wizard"):
        chart_rule = 'chart: {"kind": "bar", "label": "what is measured", "labels": [...], "values": [numbers from the material]} (never invent numbers);'
        figure_rule = ('figure / image_left / image_right: {"url": "<only from the image list, else empty>", "description": "what the picture should show", "caption": "..."}; '
                       'quote: {"text": "...", "by": "who said it"}; leave "bullets" empty on a quote slide.')
        quote_key = '"quote": {}, '
    else:
        chart_rule = 'chart: {"chart_id": number} from the list;'
        figure_rule = 'figure: {"url": "...", "caption": "..."} from the image list.'
        quote_key = ""
    src_rule = src_key = ""
    if body.get("source_tags"):
        src_rule = '\n- sources: the tags of the source blocks the slide really draws on, e.g. ["S1", "S3"]; never a tag that is not in the material.'
        src_key = '"sources": [], '
    for a in range(0, len(outl), 12):
        b = min(len(outl), a + 12)
        prompt = f"""Write the slides {a + 1}-{b} of this presentation.
{_opts(body)}
Deck title: {plan.get('title') or body.get('title') or ''}

OUTLINE:
{listing}

MATERIAL:
{mat}

Rules:
- At most 6 bullets per slide, each under 18 words. No paragraphs on slides.
- Cite a source right after the claim with [@key], only keys from the library list. Never invent sources or numbers.
- table: {{"header": [...], "rows": [[...]]}} with values from the material; {chart_rule}
  {figure_rule}
- two_column: "left"/"right" each {{"heading": "...", "bullets": [...]}}.
- references: leave bullets empty (A.F.R.A writes the list).
- notes: {"what the speaker says, 60-120 words" if notes else "empty string"}.{src_rule}
Return JSON: {{"slides": [{{"n": {a + 1}, "layout": "...", "title": "...", "subtitle": "", "bullets": [], "left": {{}}, "right": {{}}, "table": {{}}, "chart": {{}}, "figure": {{}}, {quote_key}{src_key}"caption": "", "notes": ""}}]}}
with exactly one entry per outline slide {a + 1}-{b}, in order."""
        j = await complete_json(prompt, SYSTEM, task="slides-content")
        got = j.get("slides") if isinstance(j, dict) else j
        got = got if isinstance(got, list) else []
        for k, o in enumerate(outl[a:b]):
            out.append(_clean(got[k] if k < len(got) else {}, {**o, "sheet_id": sheet_id}))
    return finish_content(out, body.get("project_id", ""), notes)


async def rewrite_slide(name: str, cur: dict, ask: str, layout: str = "", wizard: bool = False) -> dict:
    """The AI rewrites one slide (JSON in, cleaned JSON out). Used by redo() on built decks and by the wizard's mockup."""
    shapes = ('\nShapes: quote {"text": "...", "by": "..."}; chart {"kind": "bar", "label": "...", "labels": [...], "values": [numbers already on the slide]}; '
              'figure {"url": "", "description": "what the picture should show", "caption": ""}.') if wizard else ""
    j = await complete_json(f"""One slide of the deck "{name}" as JSON:
{json.dumps(cur, ensure_ascii=False)}

{ask}{(' Use the layout "' + layout + '".') if layout and layout != cur.get("layout") else ""}
Keep citations exactly as written (e.g. (Lee, 2020) or [3]); add no new sources. Same layout and JSON shape.{shapes}
Return the slide JSON only.""", SYSTEM, task="slides-redo")
    return _clean(j.get("slide", j) if isinstance(j, dict) else {}, {"layout": layout or cur.get("layout"), "title": cur.get("title")})


async def redo(deck_id: str, index: int, action: str, instruction: str = "") -> dict:
    d = deck(deck_id)
    if not 0 <= index < len(d["slides"]):
        raise ValueError("No such slide.")
    cur = d["slides"][index]["content"]
    ask = {"shorter": "Make it shorter: at most 4 bullets, each under 12 words. Keep the meaning and the citations.",
           "rewrite": "Rewrite it to be clearer and more engaging. Keep the facts and the citations."}.get(action, instruction or "Improve it.")
    if instruction and action in ("shorter", "rewrite"):
        ask += " " + instruction
    return update_slide(deck_id, index, await rewrite_slide(d["name"], cur, ask))


# ---------------------------------------------------------------- building (background job)
def _decks() -> list[dict]:
    return _load("decks.json")


def deck(did: str) -> dict:
    d = next((d for d in _decks() if d["id"] == did), None)
    if not d:
        raise ValueError("A.F.R.A doesn't know that deck (it may have been built on the other computer).")
    return d


def _put_deck(rec: dict):
    with _LOCK:
        _save("decks.json", [d for d in _decks() if d["id"] != rec["id"]] + [rec])


def decks(email: str | None = None) -> list[dict]:
    rows = [d for d in _decks() if not email or d.get("account") == email]
    return [{k: d.get(k) for k in ("id", "name", "account", "url", "created", "source", "template_name", "flags", "path", "in_drive")}
            | {"slides": len(d.get("slides") or []), "rebuild": bool(d.get("slides") and d["slides"][0].get("content"))}
            for d in sorted(rows, key=lambda d: -d.get("created", 0))]


def forget_deck(did: str):
    with _LOCK:
        _save("decks.json", [d for d in _decks() if d["id"] != did])


def build(body: dict) -> dict:
    """Start a build in the background; returns the job id.
    body: slides (content JSON), title, design_id, targets ["google", "pptx"], account, project_id, notes, missing."""
    slides = [_clean(s, s) for s in body.get("slides") or []]
    if not slides:
        raise ValueError("No slides to build.")
    targets = [t for t in body.get("targets") or ["google"] if t in ("google", "pptx")] or ["pptx"]
    email = ""
    if "google" in targets:
        if not g.accounts():
            if "pptx" not in targets:
                raise ValueError("Connect a Google account first, or build the PowerPoint file only.")
            targets.remove("google")
        else:
            email, _ = g.access_token(body.get("account") or None)
    designs.pick(body.get("design_id", ""))  # fails now, not in the background, if the design is gone
    jid = secrets.token_hex(5)
    JOBS[jid] = {"id": jid, "state": "working", "step": "Starting…", "error": "", "started": time.time(), "deck": None, "pptx": None,
                 "flags": [], "targets": targets}
    for k in [k for k, v in JOBS.items() if time.time() - v["started"] > 86400]:
        JOBS.pop(k, None)
    threading.Thread(target=_run, args=(jid, email, body, slides, targets), daemon=True).start()
    return {"job": jid, "targets": targets}


def job(jid: str) -> dict:
    j = JOBS.get(jid)
    if not j:
        raise ValueError("Unknown job (A.F.R.A was restarted?). Look under My decks.")
    return j


def _run(jid: str, email: str, body: dict, slides: list[dict], targets: list[str]):
    J = JOBS[jid]
    try:
        if "pptx" in targets:
            J["pptx"] = build_pptx_now(body, slides, lambda step: J.update(step=step))
            J["flags"] = J["pptx"]["flags"]
        if "google" in targets:
            J["deck"] = build_now(email, body, slides, lambda step: J.update(step=step))
            J["flags"] = J["deck"]["flags"]
        J.update(state="done", step="Done")
    except Exception as e:  # shown in the page
        J.update(state="error", error=str(e) or e.__class__.__name__)


def build_pptx_now(body: dict, slides: list[dict], step=lambda s: None) -> dict:
    """The PowerPoint engine: no Google connection needed. Goes to My Drive/A.F.R.A Decks if Drive for desktop is there."""
    from . import pptx_build
    design = designs.pick(body.get("design_id", ""))
    title = (body.get("title") or slides[0].get("title") or "A.F.R.A deck").strip()[:150]
    step("Fetching linked pictures…")
    images = pptx_build.fetch_images(slides)
    step(f"Writing the PowerPoint ({len(slides)} slides, design \"{design['name']}\")…")
    data, flags = pptx_build.build_pptx(slides, designs.theme(design), title, images)
    where = pptx_build.save(data, title, body.get("to_drive", True))
    flags += review({}, [], slides, body.get("missing") or [])
    rec = {"id": "p" + secrets.token_hex(5), "name": title, "account": "", "url": "", "created": time.time(), "source": "pptx",
           "path": where["path"], "in_drive": where["in_drive"], "design": design["id"], "template_name": design["name"],
           "project_id": body.get("project_id", ""), "notes": body.get("notes", True),
           "slides": [{"id": "", "layout": s["layout"], "content": s} for s in slides], "flags": _dedupe(flags)}
    _put_deck(rec)
    return {"id": rec["id"], "name": title, "flags": rec["flags"], "slides": len(slides), **where}


def build_now(email: str, body: dict, slides: list[dict], step=lambda s: None) -> dict:
    step("Finding your A.F.R.A Decks folder…")
    folders = g.ensure_folders(email)
    design = designs.pick(body.get("design_id", ""))
    tpl = google_template(design, email, step)
    title = (body.get("title") or slides[0].get("title") or "A.F.R.A deck").strip()[:150]
    step(f"Copying the design \"{tpl['name']}\"…")
    f = g.copy_file(tpl["file_id"], title, folders["decks"], email)
    pres = g.presentation(f["id"], email)
    step(f"Filling {len(slides)} slides…")
    reqs, recs, flags = build_requests(pres, slides, tpl, _nonce())
    g.batch_update(f["id"], reqs, email)
    step("Speaker notes…")
    pres = g.presentation(f["id"], email)
    wanted = {r["id"]: (slides[i].get("notes") or "") for i, r in enumerate(recs)}
    nreq = notes_requests(pres, wanted)
    if nreq:
        g.batch_update(f["id"], nreq, email)
    step("Checking the result…")
    flags += review(pres, recs, slides, body.get("missing") or [])
    if tpl.get("note"):
        flags.append({"slide": 0, "issue": tpl["note"]})
    rec = {"id": f["id"], "name": title, "account": email, "url": f.get("webViewLink") or f"https://docs.google.com/presentation/d/{f['id']}/edit",
           "created": time.time(), "source": "built", "design": design["id"], "template_name": design["name"], "kind": tpl.get("kind"),
           "theme": tpl.get("theme"), "project_id": body.get("project_id", ""), "notes": body.get("notes", True),
           "slides": [{**r, "content": slides[i]} for i, r in enumerate(recs)], "flags": _dedupe(flags)}
    _put_deck(rec)
    return {k: rec[k] for k in ("id", "name", "url", "account", "flags")} | {"slides": len(recs)}


def _dedupe(flags: list[dict]) -> list[dict]:
    seen, out = set(), []
    for f in sorted(flags, key=lambda f: f["slide"]):
        k = (f["slide"], f["issue"])
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out


def restyle(did: str, design_id: str, targets: list[str] | None = None) -> dict:
    """Change style: the same content built again in another design, as a new deck."""
    d = deck(did)
    if not d.get("slides") or not d["slides"][0].get("content"):
        raise ValueError("This deck was converted from a file, so A.F.R.A doesn't have its content to rebuild.")
    return build({"account": d["account"], "design_id": design_id, "title": d["name"], "project_id": d.get("project_id", ""),
                  "notes": d.get("notes", True), "targets": targets or (["google"] if d.get("account") else ["pptx"]),
                  "slides": [s["content"] for s in d["slides"]]})


def update_slide(did: str, index: int, new: dict) -> dict:
    """Rewrite one slide of a Google deck in place: its text boxes, its table and its speaker notes."""
    d = deck(did)
    if not d.get("account"):
        raise ValueError("That deck is a PowerPoint file: edit it in PowerPoint or Google Slides.")
    if not 0 <= index < len(d["slides"]):
        raise ValueError(f"The deck has {len(d['slides'])} slides.")
    s = d["slides"][index]
    old = s["content"]
    merged = _clean({**old, **{k: v for k, v in new.items() if v not in (None, "")}}, {"layout": old.get("layout")})
    email = d["account"]
    pres = g.presentation(did, email)
    page = next((p for p in pres.get("slides", []) if p["objectId"] == s["id"]), None)
    if not page:
        raise ValueError("That slide was deleted in Google Slides. Build the deck again to change it.")
    has_text = {el["objectId"]: bool(_text_of(el).strip()) for el in page.get("pageElements", [])}
    texts = slide_texts(merged)
    reqs = []
    for f, oid in s["fields"].items():
        if oid not in has_text:
            continue
        if has_text[oid]:
            reqs.append({"deleteText": {"objectId": oid, "textRange": {"type": "ALL"}}})
        if texts.get(f):
            reqs.append({"insertText": {"objectId": oid, "text": texts[f]}})
    theme = {**THEME, **(d.get("theme") or {})}
    if merged["layout"] == "table" and s.get("area") and merged["table"]["rows"] and merged["table"] != old.get("table"):
        if s.get("element") and s["element"] in has_text:
            reqs.append({"deleteObject": {"objectId": s["element"]}})
        s["element"] = f"{s['id']}_t{_nonce()}"
        reqs += table_requests(s["element"], s["id"], s["area"], merged["table"]["header"], merged["table"]["rows"][:MAX_ROWS], theme)
    if "notes" in new:
        reqs += notes_requests({"slides": [page]}, {s["id"]: merged["notes"]})
    if reqs:
        g.batch_update(did, reqs, email)
    s["content"] = merged
    _put_deck(d)
    return {"deck": did, "slide": index + 1, "content": merged, "url": d["url"]}


def export_deck(did: str, fmt: str = "pptx") -> tuple[bytes, str]:
    d = deck(did)
    if not d.get("account"):  # a PowerPoint the .pptx engine wrote
        f = Path(d.get("path") or "")
        if fmt != "pptx" or not f.exists():
            raise ValueError("That PowerPoint file was moved or deleted.")
        return f.read_bytes(), f.name
    mime = "application/pdf" if fmt == "pdf" else g.MIME_PPTX
    return g.export(did, mime, d["account"]), re.sub(r'[\\/:*?"<>|]+', "_", d["name"])[:100] + (".pdf" if fmt == "pdf" else ".pptx")


# ---------------------------------------------------------------- PPT Master decks -> Google Slides
def pptmaster_files() -> list[dict]:
    from .slides import FOLDER
    done = {d.get("path"): d for d in _decks() if d.get("path")}
    out = []
    if FOLDER.exists():
        for p in FOLDER.rglob("*.pptx"):
            if any(part in (".venv", ".claude", "node_modules") or part.startswith("~$") for part in p.relative_to(FOLDER).parts):
                continue
            st = p.stat()
            d = done.get(str(p))
            out.append({"path": str(p), "name": p.name, "folder": str(p.parent.relative_to(FOLDER)), "size": st.st_size, "modified": st.st_mtime,
                        "google": {"url": d["url"], "account": d["account"]} if d and d.get("modified") == st.st_mtime else None})
    return sorted(out, key=lambda x: -x["modified"])[:200]


def pptx_to_slides(path: str = "", data: bytes | None = None, filename: str = "", email: str | None = None) -> dict:
    """Upload a .pptx into A.F.R.A Decks with conversion to Google Slides."""
    from .slides import FOLDER
    mtime = None
    if path:
        p = Path(path).resolve()
        if FOLDER.resolve() not in p.parents or p.suffix.lower() != ".pptx" or not p.exists():
            raise ValueError("Only .pptx files inside the AFRA-Slides folder can be sent this way.")
        data, filename, mtime = p.read_bytes(), p.name, p.stat().st_mtime
    if not data:
        raise ValueError("No .pptx given.")
    email, _ = g.access_token(email)
    folders = g.ensure_folders(email)
    name = Path(filename or "Deck.pptx").stem[:150]
    f = g.upload(data, name, g.MIME_PPTX, folders["decks"], g.MIME_SLIDES, email)
    url = f.get("webViewLink") or f"https://docs.google.com/presentation/d/{f['id']}/edit"
    _put_deck({"id": f["id"], "name": name, "account": email, "url": url, "created": time.time(), "source": "converted",
               "path": str(Path(path).resolve()) if path else "", "modified": mtime, "slides": [], "flags": []})
    return {"id": f["id"], "name": name, "url": url, "account": email}


def run(coro):
    """For callers outside the event loop (tests)."""
    return asyncio.run(coro)
