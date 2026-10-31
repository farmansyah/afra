"""Cheatsheets: many lectures, documents and materials in, one dense printable sheet out.

A draft (one JSON file in data/cheatsheet/) carries everything between visits:
  sources   what to read (sources.py: A.F.R.A documents, library papers, uploads, Tugas files and whole folders)
  options   length (1 page, 2 pages, both sides of one A4 sheet), 2 or 3 columns, font size, what to include, language
  sections  what the AI proposed and the person edited: [{"id", "title", "items": [{"id", "text", "kind", "source", "on"}]}]
  output    Word (.docx, real columns), PDF, Google Doc, saved into a Tugas folder

Steps: Sources -> Options -> Review -> Make. Only `propose` and `suggest` use the AI (API mode or the Claude app, handoff.py);
validation, review operations, the fit estimate and the files are plain code.
"""
import copy
import io
import json
import math
import re
import secrets
import threading
import time

from . import db, google_api as g, simple_pdf, sources, tugas
from .config import DATA_DIR, replace_file
from .llm import complete_json

DIR = DATA_DIR / "cheatsheet"
STEPS = ["sources", "options", "review", "make"]
NO_GOOGLE = "Connect a Google account first: Settings › Google."
KINDS = {"definition": "definitions", "formula": "formulas", "example": "examples", "date": "dates", "step": "steps", "comparison": "comparisons"}
KIND_ALL = list(KINDS) + ["concept"]  # "concept" (a key idea) is always welcome
LENGTHS = {"1": 1, "2": 2, "both": 2}
MAX_SECTIONS, MAX_ITEMS, MAX_TEXT = 40, 400, 500
PAGE = (595.28, 841.89)
MARGIN = 28.35  # 1 cm
GAP = 14.0
WID_RE = re.compile(r"^[0-9a-f]{10}$")
_LOCK = threading.RLock()


# ---------------------------------------------------------------- drafts on disk
def _file(cid: str):
    if not isinstance(cid, str) or not WID_RE.match(cid):
        raise ValueError("That cheatsheet draft doesn't exist.")
    return DIR / f"{cid}.json"


def load(cid: str) -> dict:
    try:
        return json.loads(_file(cid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("That cheatsheet draft no longer exists.")


def save(st: dict) -> dict:
    with _LOCK:
        DIR.mkdir(parents=True, exist_ok=True)
        st["updated"] = time.time()
        f = _file(st["id"])
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        replace_file(tmp, f)
    return st


def delete(cid: str):
    _file(cid).unlink(missing_ok=True)


def listing() -> list[dict]:
    rows = []
    for f in DIR.glob("*.json") if DIR.exists() else []:
        try:
            st = json.loads(f.read_text(encoding="utf-8"))
            rows.append({"id": st["id"], "title": st.get("title") or "Untitled cheatsheet", "step": st.get("step", "sources"), "updated": st.get("updated", 0),
                         "done": bool(st.get("result")), "items": sum(len(s["items"]) for s in st.get("sections") or [])})
        except (OSError, ValueError, KeyError):
            continue
    return sorted(rows, key=lambda r: -r["updated"])


def default_options() -> dict:
    return {"length": "1", "columns": 3, "font": 7.0, "language": "", "include": {v: True for v in KINDS.values()}}


def public(st: dict) -> dict:
    """What the page gets: the uploaded texts stay on the server (name and size only), plus the fit estimate."""
    out = {k: v for k, v in st.items() if k != "materials"}
    out["materials"] = [{"id": m["id"], "name": m["name"], "chars": len(m["text"])} for m in st.get("materials") or []]
    out["source_labels"] = [sources.label(r) for r in st.get("sources") or []]
    out["estimate"] = estimate(st.get("sections") or [], st["options"])
    out["non_latin"] = not all(simple_pdf.latin_only(t) for t in all_text(st.get("sections") or [], st.get("title", "")))
    return out


def new(body: dict) -> dict:
    pid = body.get("project_id") or ""
    if not any(p["id"] == pid for p in db.list_projects()):
        raise ValueError("Pick a project first.")
    refs = sources.clean_refs(body.get("sources") or [])
    folder = body.get("folder")
    st = {"id": secrets.token_hex(5), "created": time.time(), "step": "sources", "project_id": pid, "title": str(body.get("title") or "").strip()[:120],
          "sources": refs, "materials": [], "options": default_options(), "sections": [], "notes": [],
          "output": {"docx": True, "pdf": False, "google": False, "folder": folder if isinstance(folder, str) else None, "account": ""}, "result": None}
    if body.get("options"):
        st["options"].update(clean_options(body["options"], st["options"]))
    return save(st)


def clean_options(o: dict, cur: dict | None = None) -> dict:
    out = {}
    if "length" in o:
        if o["length"] not in LENGTHS:
            raise ValueError("Length is 1 page, 2 pages or both sides of A4.")
        out["length"] = o["length"]
    if "columns" in o:
        out["columns"] = 3 if int(o["columns"]) == 3 else 2
    if "font" in o:
        try:
            out["font"] = max(5.0, min(12.0, round(float(o["font"]) * 2) / 2))
        except (TypeError, ValueError):
            raise ValueError("The font size should be a number between 5 and 12.")
    if "language" in o:
        out["language"] = str(o["language"] or "").strip()[:60]
    if isinstance(o.get("include"), dict):
        out["include"] = {**((cur or {}).get("include") or {v: True for v in KINDS.values()}), **{k: bool(v) for k, v in o["include"].items() if k in KINDS.values()}}
    return out


def patch(cid: str, body: dict) -> dict:
    st = load(cid)
    if "title" in body:
        st["title"] = str(body["title"] or "").strip()[:120]
    if "options" in body and isinstance(body["options"], dict):
        st["options"].update(clean_options(body["options"], st["options"]))
    if "sources" in body:
        st["sources"] = sources.clean_refs(body["sources"])
    if "output" in body and isinstance(body["output"], dict):
        o, n = st["output"], body["output"]
        for k in ("docx", "pdf", "google"):
            if k in n:
                o[k] = bool(n[k])
        for k in ("folder", "account"):
            if k in n:
                o[k] = None if n[k] is None else str(n[k])
    if body.get("step") in STEPS:
        st["step"] = body["step"]
        if st["step"] != "make":
            st["result"] = None  # going back to change something: the Make page offers the files again
    return save(st)


def add_material(cid: str, filename: str, data: bytes) -> dict:
    name = (filename or "file").replace("\\", "/").rsplit("/", 1)[-1]
    text = sources.extract_text(name, data)
    if not text:
        raise ValueError(f"No text found in {name} (a scanned PDF has no text to read).")
    st = load(cid)
    m = {"id": secrets.token_hex(4), "name": name[:120], "text": text[:sources.CHAR_CAP]}
    st["materials"] = (st.get("materials") or []) + [m]
    st["materials"] = st["materials"][-40:]
    save(st)
    return {"id": m["id"], "name": m["name"], "chars": len(m["text"])}


def remove_material(cid: str, mid: str):
    st = load(cid)
    st["materials"] = [m for m in st.get("materials") or [] if m["id"] != mid]
    save(st)


# ---------------------------------------------------------------- sections and items (validation and review operations)
def _id(prefix: str) -> str:
    return prefix + secrets.token_hex(3)


def validate_sections(secs) -> list[dict]:
    """Strict check of sections coming from the page, then the cleaned copy. Raises ValueError with a plain message."""
    if not isinstance(secs, list):
        raise ValueError("The sheet needs a list of sections.")
    if len(secs) > MAX_SECTIONS:
        raise ValueError(f"A sheet can have up to {MAX_SECTIONS} sections.")
    out, seen, count = [], set(), 0
    for n, s in enumerate(secs, 1):
        if not isinstance(s, dict) or not isinstance(s.get("items"), list):
            raise ValueError(f"Section {n} needs a title and a list of items.")
        title = s.get("title")
        if not isinstance(title, str):
            raise ValueError(f"Section {n}: the title must be text.")
        sid = s["id"] if isinstance(s.get("id"), str) and s["id"] and s["id"] not in seen else _id("c")
        seen.add(sid)
        items = []
        for k, it in enumerate(s["items"], 1):
            if not isinstance(it, dict) or not isinstance(it.get("text"), str):
                raise ValueError(f"Section {n}, item {k}: the text must be a string.")
            text = re.sub(r"\s+", " ", it["text"]).strip()
            if not text:
                continue
            iid = it["id"] if isinstance(it.get("id"), str) and it["id"] and it["id"] not in seen else _id("i")
            seen.add(iid)
            items.append({"id": iid, "text": text[:MAX_TEXT], "kind": it.get("kind") if it.get("kind") in KIND_ALL else "concept",
                          "source": str(it.get("source") or "")[:80], "on": it.get("on") is not False})
        count += len(items)
        if count > MAX_ITEMS:
            raise ValueError(f"A sheet can have up to {MAX_ITEMS} items.")
        out.append({"id": sid, "title": re.sub(r"\s+", " ", title).strip()[:80] or "Untitled", "items": items})
    return out


def from_ai(j, items: list[dict], include: dict) -> tuple[str, list[dict]]:
    """The AI's JSON -> (title, sections). Kinds the person switched off are dropped; source tags S1.. become the source names."""
    if not isinstance(j, dict) or not isinstance(j.get("sections"), list):
        raise ValueError("The AI's answer had no sections. Try again.")
    secs = []
    for s in j["sections"]:
        if not isinstance(s, dict) or not isinstance(s.get("items"), list):
            continue
        its = []
        for it in s["items"]:
            if isinstance(it, str):
                it = {"text": it}
            if not isinstance(it, dict) or not isinstance(it.get("text"), str) or not it["text"].strip():
                continue
            kind = it.get("kind") if it.get("kind") in KIND_ALL else "concept"
            if kind in KINDS and not include.get(KINDS[kind], True):
                continue
            tags = it.get("source") if isinstance(it.get("source"), list) else re.findall(r"S\d+", str(it.get("source") or "").upper())
            src = "; ".join(dict.fromkeys(short_source(i) for i in sources.map_tags(tags, items)))
            its.append({"text": it["text"], "kind": kind, "source": src, "on": True})
        if its:
            secs.append({"title": str(s.get("title") or "Untitled"), "items": its})
    secs = validate_sections(secs)
    if not any(s["items"] for s in secs):
        raise ValueError("The AI found nothing to put on the sheet. Add more material or switch more content types on.")
    return str(j.get("title") or "").strip()[:120], secs


def short_source(it: dict) -> str:
    """A small tag for an item: the file or document name without its extension, shortened."""
    name = re.sub(r"\.(pptx|pdf|docx|md|markdown|txt)$", "", it["label"], flags=re.I)
    return name if len(name) <= 24 else name[:23].rstrip() + "…"


def _find(secs: list[dict], iid: str):
    for si, s in enumerate(secs):
        for ii, it in enumerate(s["items"]):
            if it["id"] == iid:
                return si, ii
    raise ValueError("That item no longer exists.")


def _sec(secs: list[dict], sid: str) -> int:
    for i, s in enumerate(secs):
        if s["id"] == sid:
            return i
    raise ValueError("That section no longer exists.")


def apply_op(secs: list[dict], op: dict) -> list[dict]:
    """toggle, edit_item, edit_section, move_item, move_section, add_item, add_section, delete_item, delete_section, set_section
    (tick a whole section on or off). Returns the new sections."""
    secs = validate_sections(copy.deepcopy(secs))
    kind = op.get("op")
    if kind == "toggle":
        si, ii = _find(secs, op.get("item"))
        secs[si]["items"][ii]["on"] = bool(op.get("on", not secs[si]["items"][ii]["on"]))
    elif kind == "set_section":
        for it in secs[_sec(secs, op.get("section"))]["items"]:
            it["on"] = bool(op.get("on"))
    elif kind == "edit_item":
        si, ii = _find(secs, op.get("item"))
        text = re.sub(r"\s+", " ", str(op.get("text") or "")).strip()
        if not text:
            raise ValueError("An item can't be empty: delete it instead.")
        secs[si]["items"][ii]["text"] = text[:MAX_TEXT]
        if op.get("kind") in KIND_ALL:
            secs[si]["items"][ii]["kind"] = op["kind"]
    elif kind == "edit_section":
        secs[_sec(secs, op.get("section"))]["title"] = str(op.get("title") or "").strip()[:80] or "Untitled"
    elif kind == "move_item":
        si, ii = _find(secs, op.get("item"))
        it = secs[si]["items"].pop(ii)
        to = secs[_sec(secs, op.get("to"))]["items"]
        at = op.get("index")
        to.insert(len(to) if at is None else max(0, min(len(to), int(at))), it)
    elif kind == "move_section":
        i = _sec(secs, op.get("section"))
        s = secs.pop(i)
        secs.insert(max(0, min(len(secs), int(op.get("to", i)))), s)
    elif kind == "add_item":
        text = re.sub(r"\s+", " ", str(op.get("text") or "")).strip()
        if not text:
            raise ValueError("Write the item first.")
        secs[_sec(secs, op.get("section"))]["items"].append({"id": _id("i"), "text": text[:MAX_TEXT], "kind": op.get("kind") if op.get("kind") in KIND_ALL else "concept",
                                                              "source": "mine", "on": True})
    elif kind == "add_section":
        secs.append({"id": _id("c"), "title": str(op.get("title") or "New section").strip()[:80] or "New section", "items": []})
    elif kind == "delete_item":
        si, ii = _find(secs, op.get("item"))
        del secs[si]["items"][ii]
    elif kind == "delete_section":
        del secs[_sec(secs, op.get("section"))]
    else:
        raise ValueError("Unknown change to the sheet.")
    return validate_sections(secs)


def edit(cid: str, op: dict, secs: list[dict] | None = None) -> dict:
    st = load(cid)
    st["sections"] = apply_op(secs if secs is not None else st.get("sections") or [], op)
    return public(save(st))


def save_sections(cid: str, secs, title=None) -> dict:
    st = load(cid)
    st["sections"] = validate_sections(secs)
    if title is not None:
        st["title"] = str(title).strip()[:120]
    return public(save(st))


def all_text(secs: list[dict], title: str = "") -> list[str]:
    return [title] + [s["title"] for s in secs if any(i["on"] for i in s["items"])] + [i["text"] + " " + i["source"] for s in secs for i in s["items"] if i["on"]]


# ---------------------------------------------------------------- does it fit?
def geometry(o: dict) -> dict:
    cols, font = int(o.get("columns") or 3), float(o.get("font") or 7)
    pages = LENGTHS.get(o.get("length"), 1)
    colw = (PAGE[0] - 2 * MARGIN - GAP * (cols - 1)) / cols
    per_col = (PAGE[1] - 2 * MARGIN) / (font * 1.2)
    return {"cols": cols, "font": font, "pages": pages, "cpl": max(8.0, colw / (font * 0.5)), "per_col": per_col,
            "capacity": pages * cols * per_col - 3.0}  # three lines for the title on page one


def section_lines(s: dict, geo: dict) -> float:
    on = [i for i in s["items"] if i["on"]]
    if not on:
        return 0.0
    n = 1.6  # heading with its spacing
    for i in on:
        n += math.ceil((len(i["text"]) + (len(i["source"]) + 3 if i["source"] else 0) + 2) / geo["cpl"]) + 0.15
    return n


def estimate(secs: list[dict], o: dict) -> dict:
    """A rough fit check: lines the ticked items need against the lines the chosen length, columns and font give."""
    geo = geometry(o)
    need = sum(section_lines(s, geo) for s in secs)
    cap = geo["capacity"] * 0.96  # columns never fill to the last line
    ratio = need / cap if cap else 0
    page_cap = cap / geo["pages"]
    out = {"needed": round(need), "capacity": round(cap), "ratio": round(ratio, 2), "pages": round(need / page_cap, 1) if page_cap else 0,
           "fits": need <= cap, "items": sum(1 for s in secs for i in s["items"] if i["on"]), "tip": ""}
    if not out["items"]:
        out["tip"] = "Nothing is ticked yet."
    elif not out["fits"]:
        out["tip"] = f"About {round((need - cap) / max(1, need) * 100)}% too much for this length: untick some items, pick a smaller font, or choose a longer sheet."
    elif ratio < 0.6:
        out["tip"] = "There is room left: you can tick more items or use a bigger font."
    else:
        out["tip"] = "Fits."
    return out


def break_section(secs: list[dict], o: dict) -> int | None:
    """For "both sides of A4": the section where the back side starts (the boundary nearest to half of the content)."""
    if o.get("length") != "both":
        return None
    geo = geometry(o)
    lines = [section_lines(s, geo) for s in secs]
    live = [i for i, v in enumerate(lines) if v]
    if len(live) < 2:
        return None
    half, run, best = sum(lines) / 2, 0.0, None
    for n, i in enumerate(live):
        if n and (best is None or abs(run - half) < best[0]):
            best = (abs(run - half), i)
        run += lines[i]
    return best[1] if best else None


# ---------------------------------------------------------------- the AI steps
SYSTEM = ("You make dense, accurate cheatsheets for students from their own lecture slides and notes. Short items, no filler, "
          "and only what is in the material: never add facts, formulas or numbers it does not contain.")


def wants(o: dict) -> str:
    inc = o.get("include") or {}
    names = {"definitions": "definition", "formulas": "formula", "examples": "example", "dates": "date (dates and names)", "steps": "step (procedures, in order)", "comparisons": "comparison"}
    on = [names[k] for k in names if inc.get(k, True)]
    return ", ".join(on + ["concept (a key idea)"])


def answers_text(st: dict) -> str:
    qs = {q["id"]: q["q"] for q in st.get("questions") or []}
    got = [f"- {qs[k]} {v}" for k, v in (st.get("answers") or {}).items() if k in qs and str(v).strip()]
    return ("\nWhat the person told you about this sheet:\n" + "\n".join(got) + "\n") if got else ""

async def questions(cid: str) -> dict:
    """Optional "ask me first": a few short questions about what the sheet is for, from a quick look at the sources."""
    import asyncio
    from .wizard import ensure_ai
    st = load(cid)
    if not st["sources"] and not st["materials"]:
        raise ValueError("Pick at least one source first.")
    ensure_ai()
    items, _ = await asyncio.to_thread(sources.collect, st["sources"], st["materials"])
    text = await sources.digest(items, budget=12000)
    j = await complete_json(f"""Someone wants a printed cheatsheet from these sources. Ask 2-4 short questions that would change what goes on it
(for example: which exam or topic to focus on, which chapters matter most, whether formulas need worked examples, what they already know).
Give 2-4 short answer choices for each; the person may also type their own.

SOURCES:
{text}

Return JSON: {{"questions": [{{"q": "...", "options": ["...", "..."]}}]}}""", SYSTEM, task="cheatsheet-questions")
    qs = [{"id": f"q{n}", "q": str(q.get("q") or "").strip()[:200], "options": [str(o).strip()[:80] for o in (q.get("options") or [])][:4]}
          for n, q in enumerate((j.get("questions") if isinstance(j, dict) else j) or [], 1) if isinstance(q, dict) and str(q.get("q") or "").strip()][:4]
    if not qs:
        raise ValueError("The AI had no questions. You can go on without them.")
    st = load(cid)
    st.update(questions=qs, answers={})
    return public(save(st))


def answer(cid: str, answers: dict) -> dict:
    st = load(cid)
    ids = {q["id"] for q in st.get("questions") or []}
    st["answers"] = {k: str(v).strip()[:300] for k, v in (answers or {}).items() if k in ids}
    return public(save(st))


async def propose(cid: str, step=lambda s: None) -> dict:
    """Read the sources and propose the sections and items. Works in API mode and in the Claude app."""
    from .wizard import ensure_ai
    st = load(cid)
    if not st["sources"] and not st["materials"]:
        raise ValueError("Pick at least one source first.")
    ensure_ai()
    step("Reading the sources…")
    import asyncio
    items, notes = await asyncio.to_thread(sources.collect, st["sources"], st["materials"])
    o = st["options"]
    text = await sources.digest(items, language=o.get("language", ""))
    geo = geometry(o)
    target = max(12, int(geo["capacity"] * 0.9 / 2.2))  # about two lines an item
    j = await complete_json(f"""Make a cheatsheet from these sources. It will be printed on {'one side of one A4 page' if o.get('length') == '1' else 'two A4 pages' if o.get('length') == '2' else 'both sides of one A4 sheet'} in {geo['cols']} columns, so be compact.
{('Write in ' + o['language'] + '.') if o.get('language') else 'Write in the language of the sources.'}

{answers_text(st)}Group the items into 4-12 sections by topic. Aim for about {target} items in total, most important first in each section.
Item kinds you may use: {wants(o)}.
- definition: "Term: short definition".  formula: the formula in plain text or Unicode (a*b^2, x̄ = Σx/n) with what the symbols mean.
- example: one line with the numbers.  step: one numbered procedure per item, steps separated by " > ".  comparison: "A vs B: the difference".
- Every item is one to three lines. No item may repeat another.
- "source": the tags of the blocks it comes from, e.g. ["S1"].

SOURCES:
{text}

Return JSON: {{"title": "short sheet title", "sections": [{{"title": "...", "items": [{{"text": "...", "kind": "definition", "source": ["S1"]}}]}}]}}""", SYSTEM, task="cheatsheet-propose")
    title, secs = from_ai(j, items, o.get("include") or {})
    st = load(cid)  # the page may have saved something while the AI worked
    st.update(sections=secs, title=st["title"] or title, step="review", notes=notes, source_list=[{k: i[k] for k in ("tag", "kind", "label", "key")} for i in items])
    return public(save(st))


async def suggest(cid: str, section_id: str, instruction: str = "") -> dict:
    """The AI offers more items for one section (not added: the page shows them and the person ticks what to keep)."""
    from .wizard import ensure_ai
    st = load(cid)
    secs = st.get("sections") or []
    sec = secs[_sec(secs, section_id)]
    ensure_ai()
    import asyncio
    items, _ = await asyncio.to_thread(sources.collect, st["sources"], st["materials"])
    o = st["options"]
    text = await sources.digest(items, budget=24000, language=o.get("language", ""))
    j = await complete_json(f"""The cheatsheet section "{sec['title']}" has these items:
{chr(10).join('- ' + i['text'] for i in sec['items']) or '(none yet)'}

{('The person asks: ' + instruction.strip()[:400]) if instruction.strip() else 'Suggest up to 6 more items that belong in this section and are not there yet.'}
Item kinds: {wants(o)}. Only what the sources contain; each item one to three lines.

SOURCES:
{text}

Return JSON: {{"items": [{{"text": "...", "kind": "definition", "source": ["S1"]}}]}}""", SYSTEM, task="cheatsheet-suggest")
    _, got = from_ai({"sections": [{"title": sec["title"], "items": (j.get("items") if isinstance(j, dict) else j) or []}]}, items, o.get("include") or {})
    have = {re.sub(r"\W+", "", i["text"].lower()) for i in sec["items"]}
    return {"section": section_id, "items": [i for i in got[0]["items"] if re.sub(r"\W+", "", i["text"].lower()) not in have]}


# ---------------------------------------------------------------- the files
def title_of(st: dict) -> str:
    return (st.get("title") or "Cheatsheet").strip() or "Cheatsheet"


def live_sections(st: dict) -> list[dict]:
    return [{**s, "items": [i for i in s["items"] if i["on"]]} for s in st.get("sections") or [] if any(i["on"] for i in s["items"])]


def _file_name(title: str, ext: str) -> str:
    return (re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "", title).strip(" .") or "Cheatsheet")[:80] + ext


def build_docx(st: dict) -> bytes:
    """A4, 1 cm margins, compact fonts, a title across the top and the sections in real Word columns."""
    from docx import Document
    from docx.enum.section import WD_SECTION
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor
    o = st["options"]
    font, cols = float(o["font"]), int(o["columns"])
    secs = live_sections(st)
    if not secs:
        raise ValueError("Tick at least one item first.")
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.left_margin = sec.right_margin = sec.top_margin = sec.bottom_margin = Cm(1.0)
    sec.header_distance = sec.footer_distance = Cm(0.5)
    doc.core_properties.title = title_of(st)
    normal = doc.styles["Normal"]
    normal.font.name, normal.font.size = "Calibri", Pt(font)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")
    normal.paragraph_format.space_before = normal.paragraph_format.space_after = Pt(0)
    normal.paragraph_format.line_spacing = 1.0

    def set_cols(section, n, sep=False):
        pr = section._sectPr
        c = pr.find(qn("w:cols"))
        if c is None:
            c = OxmlElement("w:cols")
            pr.append(c)
        c.set(qn("w:num"), str(n))
        c.set(qn("w:space"), "284")
        if sep and n > 1:
            c.set(qn("w:sep"), "1")

    set_cols(sec, 1)
    t = doc.add_paragraph()
    r = t.add_run(title_of(st))
    r.bold, r.font.size, r.font.color.rgb = True, Pt(font + 5), RGBColor(0x1F, 0x38, 0x64)
    t.paragraph_format.space_after = Pt(2)
    body = doc.add_section(WD_SECTION.CONTINUOUS)  # the sheet itself: N columns from here on
    set_cols(body, cols, sep=True)
    brk = break_section(live_sections(st), o)
    for n, s in enumerate(secs):
        h = doc.add_paragraph()
        hr = h.add_run(s["title"])
        hr.bold, hr.font.size, hr.font.color.rgb = True, Pt(font + 1), RGBColor(0x1F, 0x38, 0x64)
        pf = h.paragraph_format
        pf.space_before, pf.space_after, pf.keep_with_next = Pt(3), Pt(1.5), True
        if brk is not None and n == brk:
            pf.page_break_before = True
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), "DCE6F2")
        h._p.get_or_add_pPr().append(shd)
        for it in s["items"]:
            p = doc.add_paragraph()
            pf = p.paragraph_format
            pf.left_indent, pf.first_line_indent, pf.space_after = Pt(font * 0.9), Pt(-font * 0.9), Pt(1.2)
            p.add_run("• ")
            text = it["text"]
            m = re.match(r"^([^:]{2,40}):\s+(.*)$", text) if it["kind"] in ("definition", "concept", "comparison") else None
            if m:
                p.add_run(m.group(1) + ": ").bold = True
                text = m.group(2)
            run = p.add_run(text)
            if it["kind"] == "formula":
                run.font.name = "Cambria Math"
                run._r.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), "Cambria Math")
            if it["source"]:
                tag = p.add_run(f" [{it['source']}]")
                tag.font.size, tag.font.color.rgb = Pt(max(4.5, font - 1.5)), RGBColor(0x7F, 0x7F, 0x7F)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def build_pdf(st: dict) -> bytes:
    o = st["options"]
    secs = live_sections(st)
    if not secs:
        raise ValueError("Tick at least one item first.")
    blocks = [{"heading": s["title"], "items": [(i["text"], i["source"]) for i in s["items"]]} for s in secs]
    return simple_pdf.build_columns(title_of(st), blocks, int(o["columns"]), float(o["font"]), break_before=break_section(secs, o))


def render(st: dict, fmt: str) -> tuple[bytes, str]:
    if fmt == "pdf":
        return build_pdf(st), _file_name(title_of(st), ".pdf")
    if fmt == "docx":
        return build_docx(st), _file_name(title_of(st), ".docx")
    raise ValueError("Pick Word or PDF.")


def to_google_doc(st: dict, account: str | None = None) -> dict:
    """The Word file goes to "A.F.R.A Decks" in Drive and Google converts it to a Google Doc (drive.file scope only)."""
    if not g.accounts():
        raise ValueError(NO_GOOGLE)
    data, name = render(st, "docx")
    email, _ = g.access_token(account or None)
    folders = g.ensure_folders(email)
    f = g.upload(data, name.rsplit(".", 1)[0], g.MIME_DOCX, folders["decks"], g.MIME_DOC, email)
    return {"id": f["id"], "name": f.get("name") or name, "account": email, "url": f.get("webViewLink") or f"https://docs.google.com/document/d/{f['id']}/edit"}


def make(cid: str) -> dict:
    """Write the chosen files: into the Tugas folder (Word, PDF; "name (2)" on a clash) and/or a Google Doc."""
    st = load(cid)
    out = st["output"]
    if not live_sections(st):
        raise ValueError("Tick at least one item first.")
    if not (out.get("docx") or out.get("pdf") or out.get("google")):
        raise ValueError("Choose what to make: Word, PDF or a Google Doc.")
    if (out.get("docx") or out.get("pdf")) and (out.get("folder") is None or not tugas.resolve(out["folder"]).is_dir()):  # "" is the top level of Tugas
        raise ValueError("Pick a Tugas folder to save the files in.")
    if out.get("google") and not g.accounts():
        if not (out.get("docx") or out.get("pdf")):
            raise ValueError(NO_GOOGLE)
    res = {"files": [], "google": None, "google_error": "", "folder": out.get("folder"), "estimate": estimate(st["sections"], st["options"]),
           "non_latin": bool(out.get("pdf")) and not all(simple_pdf.latin_only(t) for t in all_text(live_sections(st), title_of(st)))}
    for fmt in ("docx", "pdf"):
        if out.get(fmt):
            data, name = render(st, fmt)
            res["files"].append(tugas.save_bytes(out["folder"], name, data))
    if out.get("google"):
        if not g.accounts():
            res["google_error"] = NO_GOOGLE
        else:
            try:
                res["google"] = to_google_doc(st, out.get("account") or None)
            except (ValueError, g.GoogleError) as e:
                res["google_error"] = str(e)
    st["result"], st["step"] = res, "make"
    save(st)
    return res
