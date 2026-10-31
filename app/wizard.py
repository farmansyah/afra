"""Create into PPT: the wizard behind Tugas › New deck, the document and reading buttons and the Slides page.

A draft (one JSON file in data/wizard/) carries everything between visits, so closing the page and coming back resumes it:
  source    where the slides come from: an essay A.F.R.A writes for approval, uploaded documents, an A.F.R.A document,
            a library paper, or nothing (blank)
  options   slide count, audience, language, speaker notes, academic or punchy
  design    a design id (designs.py)
  mockup    the contract: plain wireframes, one dict per slide (see LAYOUTS below). Nothing is built until the person
            has reviewed it, and the build follows it exactly; only the design changes how it looks
  output    .pptx into a Tugas folder, Google Slides in "A.F.R.A Decks", or both

The AI steps reuse the Slides studio (gslides.outline / content / rewrite_slide), Deep Research (research.py) and the
Word export (docx_export.py); they work in API mode and in Claude-app mode (handoff.py) like every other AI button.
Everything else here is plain code, so it is tested without an AI.
"""
import asyncio
import copy
import hashlib
import json
import re
import secrets
import threading
import time

from . import db, designs, gslides, research, simple_pdf, sources, tugas, tugas_decks
from . import google_api as g
from .citations import bibliography, process_citations
from .config import DATA_DIR, get_settings, replace_file

DIR = DATA_DIR / "wizard"
STEPS = ["content", "design", "mockup", "review", "create"]
LAYOUTS = gslides.KNOWN  # title section content two_column figure image_left image_right table chart quote references closing
MAX_SLIDES = 80
NO_GOOGLE = "Connect a Google account first: Settings \u203a Google."
NEEDS_APPROVAL = "Approve the essay first: Create into PPT only starts from an essay you have approved."
UPLOADS = sources.SUPPORTED  # .txt .md .markdown .docx .pdf .pptx
JOBS: dict[str, dict] = {}
_LOCK = threading.RLock()
WID_RE = re.compile(r"^[0-9a-f]{10}$")


# ---------------------------------------------------------------- drafts on disk
def _file(wid: str):
    if not isinstance(wid, str) or not WID_RE.match(wid):
        raise ValueError("That deck draft doesn't exist.")
    return DIR / f"{wid}.json"


def load(wid: str) -> dict:
    try:
        return json.loads(_file(wid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("That deck draft no longer exists.")


def save(st: dict) -> dict:
    with _LOCK:
        DIR.mkdir(parents=True, exist_ok=True)
        st["updated"] = time.time()
        f = _file(st["id"])
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        replace_file(tmp, f)
    return st


def public(st: dict) -> dict:
    """What the page gets: the uploaded texts stay on the server (name and size only)."""
    out = {k: v for k, v in st.items() if k != "materials"}
    out["materials"] = [{"id": m["id"], "name": m["name"], "chars": len(m["text"])} for m in st.get("materials") or []]
    out["essay_info"] = essay_info(st)
    if st["source"]["type"] == "multi":
        out["source_labels"] = [sources.label(r) for r in st["source"].get("items") or []]
    return out


def listing() -> list[dict]:
    rows = []
    for f in DIR.glob("*.json") if DIR.exists() else []:
        try:
            st = json.loads(f.read_text(encoding="utf-8"))
            rows.append({"id": st["id"], "title": st.get("title") or "Untitled deck", "step": st.get("step", "content"), "source": st["source"]["type"],
                         "updated": st.get("updated", 0), "done": bool(st.get("result")), "slides": len((st.get("mockup") or {}).get("slides") or [])})
        except (OSError, ValueError, KeyError):
            continue
    return sorted(rows, key=lambda r: -r["updated"])


def delete(wid: str):
    _file(wid).unlink(missing_ok=True)


def new(body: dict) -> dict:
    mode = body.get("mode") or "blank"
    if mode not in ("topic", "upload", "doc", "paper", "multi", "blank"):
        raise ValueError("Pick how to start: a topic, uploaded documents, a document, a reading, several sources or a blank deck.")
    pid = body.get("project_id") or ""
    if not any(p["id"] == pid for p in db.list_projects()):
        raise ValueError("Pick a project first.")
    st = {"id": secrets.token_hex(5), "created": time.time(), "step": "content", "project_id": pid, "title": (body.get("title") or "").strip()[:150],
          "topic": (body.get("topic") or "").strip(), "options": {"slides": 10, "audience": "", "language": "", "notes": True, "tone": "academic"},
          "design": "", "output": {"pptx": True, "google": False, "folder": body["folder"] if isinstance(body.get("folder"), str) else None, "account": ""},
          "source": {"type": "blank"}, "essay": {}, "materials": [], "analysis": None, "mockup": None, "result": None}
    if mode == "topic":
        st["source"] = {"type": "essay"}
    elif mode == "upload":
        st["source"] = {"type": "uploads"}
    elif mode == "multi":
        refs = sources.clean_refs(body.get("sources") or [])
        if not refs:
            raise ValueError("Pick at least one source.")
        st["source"] = {"type": "multi", "items": refs}
        st["title"] = st["title"] or "New deck"
    elif mode == "doc":
        d = db.get_document(body.get("doc_id") or "")
        if not d:
            raise ValueError("That document no longer exists.")
        st.update(project_id=d["project_id"], title=st["title"] or d["title"][:150], topic=d["title"])
        st["source"] = {"type": "doc", "id": d["id"]}
    elif mode == "paper":
        p = db.get_paper(body.get("paper_id") or "", with_fulltext=False)
        if not p:
            raise ValueError("That paper is no longer in the library.")
        st.update(project_id=p["project_id"], title=st["title"] or (p.get("title") or "")[:150], topic=p.get("title") or "")
        st["source"] = {"type": "paper", "id": p["id"]}
    if body.get("options") and mode != "blank":
        st["options"].update(_options(body["options"]))
    return save(st)


def _options(o: dict) -> dict:
    out = {}
    if "slides" in o:
        out["slides"] = max(3, min(40, int(o["slides"] or 10)))
    for k in ("audience", "language"):
        if k in o:
            out[k] = str(o[k] or "").strip()[:200]
    if "notes" in o:
        out["notes"] = bool(o["notes"])
    if "tone" in o:
        out["tone"] = "punchy" if o["tone"] == "punchy" else "academic"
    return out


def patch(wid: str, body: dict) -> dict:
    st = load(wid)
    if "title" in body:
        st["title"] = str(body["title"] or "").strip()[:150]
    if "topic" in body:
        st["topic"] = str(body["topic"] or "").strip()[:4000]
    if "options" in body and isinstance(body["options"], dict):
        st["options"].update(_options(body["options"]))
    if "design" in body:
        st["design"] = str(body["design"] or "")
    if "output" in body and isinstance(body["output"], dict):
        o, n = st["output"], body["output"]
        for k in ("pptx", "google"):
            if k in n:
                o[k] = bool(n[k])
        for k in ("folder", "account"):
            if k in n:
                o[k] = str(n[k] or "")
    if "sources" in body:
        if st["source"]["type"] != "multi":
            raise ValueError("This draft doesn't start from several sources.")
        st["source"]["items"] = sources.clean_refs(body["sources"])
        st["mockup"] = None  # the mockup belongs to the sources it was written from
    if body.get("step") in STEPS:
        st["step"] = body["step"]
    return save(st)


# ---------------------------------------------------------------- the essay (topic or description first)
def content_hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]


def essay_doc(st: dict):
    did = (st.get("essay") or {}).get("doc_id") or (st["source"].get("id") if st["source"]["type"] == "doc" else "")
    return db.get_document(did) if did else None


def essay_info(st: dict) -> dict:
    """What the Content page shows about the essay: the text, and whether the approval still holds."""
    e = st.get("essay") or {}
    d = essay_doc(st) if st["source"]["type"] == "essay" else None
    approved = bool(d and e.get("approved") and e["approved"] == content_hash(d["content"]))
    return {"status": e.get("status", "idle"), "doc_id": d["id"] if d else "", "title": d["title"] if d else "", "words": len((d["content"] if d else "").split()),
            "approved": approved, "changed_after_approval": bool(d and e.get("approved") and not approved), "error": e.get("error", "")}


def source_ready(st: dict):
    """The one gate in front of every step that reads the content: an essay needs its approval first."""
    t = st["source"]["type"]
    if t == "essay":
        info = essay_info(st)
        if not info["doc_id"]:
            raise ValueError("Write the essay first (or pick a document as the essay).")
        if not info["approved"]:
            raise ValueError(NEEDS_APPROVAL)
    elif t == "uploads":
        if not st.get("materials"):
            raise ValueError("Add at least one document first.")
    elif t == "multi":
        if not st["source"].get("items") and not st.get("materials"):
            raise ValueError("Pick at least one source first.")
    elif t == "doc":
        if not db.get_document(st["source"].get("id") or ""):
            raise ValueError("That A.F.R.A document no longer exists.")
    elif t == "paper":
        if not db.get_paper(st["source"].get("id") or "", with_fulltext=False):
            raise ValueError("That paper is no longer in the library.")


def ensure_ai():
    """API mode needs a model and a key; in Claude mode the hand-off layer answers."""
    if get_settings().get("ai_mode") == "claude":
        return
    from .llm import LLMError, resolve
    provider, base, key, model = resolve("main")
    if not model or not (key or re.search(r"localhost|127\.0\.0\.1", base or "")):
        raise LLMError("No AI is connected. Add an API key under Settings \u203a AI model, or set A.F.R.A to use the Claude app.")


def essay_query(st: dict) -> str:
    return st["topic"] + (f"\n\nThe essay is for: {st['options']['audience']}." if st["options"].get("audience") else "")


ESSAY_NOTES = ("Write it as a flowing academic essay, not a list: clear sections, every claim cited, and where the evidence has numbers or a "
               "comparison, state them plainly (a short table is welcome) so they can go on slides later.")


async def start_essay(wid: str) -> dict:
    """API mode: Deep Research writes the essay in the background (real papers only, added to the library).
    Claude mode: returns the request for the Claude app, which searches, cites and saves the essay as a document."""
    st = load(wid)
    if st["source"]["type"] != "essay":
        raise ValueError("This draft doesn't start from an essay.")
    if len(st["topic"]) < 8:
        raise ValueError("Write the topic, or a few lines about what the deck should cover.")
    words = int(st["options"].get("slides", 10)) * 150
    words = max(1200, min(3000, words))
    if get_settings().get("ai_mode") == "claude":
        proj = next((p["name"] for p in db.list_projects() if p["id"] == st["project_id"]), "")
        prompt = (f"Use the A.F.R.A literature_review workflow to write an essay (about {words} words) on:\n{essay_query(st)}\n"
                  f"Save the sources to the A.F.R.A project \"{proj}\" and the essay as a document titled \"Essay: {st['title'] or st['topic'][:60]}\" with save_document. "
                  f"{ESSAY_NOTES} Language: {st['options'].get('language') or 'the language of the topic'}. "
                  "Do not make slides: the essay goes back to the person for approval first.")
        st["essay"] = {"status": "claude", "started": time.time()}
        save(st)
        return {"mode": "claude", "prompt": prompt}
    ensure_ai()
    s = get_settings()
    params = {"breadth": 3, "depth": 2, "sources": s.get("default_sources") or ["openalex"], "report_type": "Essay", "per_query": 6,
              "language": st["options"].get("language") or s.get("default_language") or "English", "report_words": words,
              "report_instructions": ESSAY_NOTES, "auto_add_sources": True}
    run = research.start(st["project_id"], essay_query(st), params)
    st["essay"] = {"status": "running", "run_id": run.id, "started": time.time()}
    save(st)
    return {"mode": "api", "run": run.id}


def essay_status(wid: str) -> dict:
    """Polled by the page: progress while the research runs; when it is done the essay becomes an A.F.R.A document."""
    with _LOCK:
        st = load(wid)
        e = st.get("essay") or {}
        info = {}
        if e.get("status") == "running":
            run = research.RUNS.get(e.get("run_id", ""))
            snap = run.snapshot() if run else db.get_run(e.get("run_id", ""))
            if not snap or snap["status"] in ("interrupted", "running") and not run:
                e.update(status="error", error="The research was interrupted (A.F.R.A was restarted). Start it again.")
            elif snap["status"] == "error":
                msg = next((ev.get("message") for ev in reversed(run.events) if ev["type"] == "error"), "") if run else ""
                e.update(status="error", error=msg or "The research failed. Check the AI settings and try again.")
            elif snap["status"] == "done":
                report = snap.get("report") or ""
                if len(report.strip()) < 40:
                    e.update(status="error", error="The research finished without an essay. Try again with a longer description.")
                else:
                    title = (re.search(r"^#\s+(.+)$", report, re.M) or [None, st["title"] or st["topic"][:80]])[1].strip()[:150]
                    d = db.create_document(st["project_id"], title, "doc", report, {"wizard": wid, "essay": True, "run_id": e.get("run_id")})
                    e.update(status="done", doc_id=d["id"])
                    st["title"] = st["title"] or title
            else:
                info = {"queries": len(snap.get("tree") or []), "sources": len(snap.get("sources") or []), "learnings": len(snap.get("learnings") or []),
                        "writing": bool(snap.get("report"))}
            st["essay"] = e
            save(st)
        d = essay_doc(st)
        return {**essay_info(st), "progress": info, "content": d["content"] if d else "", "updated": d["updated"] if d else 0}


def essay_candidates(wid: str) -> list[dict]:
    """Documents made since the Claude request began: Claude saves the essay with save_document, the person confirms which one."""
    st = load(wid)
    since = (st.get("essay") or {}).get("started", 0) - 5
    return [{"id": d["id"], "title": d["title"], "updated": d["updated"]} for d in db.list_documents(st["project_id"], "doc") if d["updated"] >= since]


def attach_essay(wid: str, doc_id: str) -> dict:
    st = load(wid)
    d = db.get_document(doc_id or "")
    if not d:
        raise ValueError("That document no longer exists.")
    st["essay"] = {**(st.get("essay") or {}), "status": "done", "doc_id": d["id"], "approved": ""}
    st["title"] = st["title"] or d["title"][:150]
    return save(st)


def approve_essay(wid: str, yes: bool = True) -> dict:
    st = load(wid)
    d = essay_doc(st)
    if not d:
        raise ValueError("There is no essay to approve yet.")
    if yes and len(d["content"].strip()) < 40:
        raise ValueError("The essay is empty.")
    st["essay"]["approved"] = content_hash(d["content"]) if yes else ""
    st["essay"]["approved_at"] = time.time() if yes else 0
    return save(st)


async def revise_essay(wid: str, instruction: str) -> dict:
    """The AI changes the essay as asked and keeps the citations; the document is saved, so any approval has to be given again."""
    from .llm import complete
    st = load(wid)
    d = essay_doc(st)
    if not d:
        raise ValueError("There is no essay yet.")
    if not (instruction or "").strip():
        raise ValueError("Say what to change, for example: shorter, more about costs, add a counter-argument.")
    ensure_ai()
    lib = db.library_map(st["project_id"])
    out = await complete(f"""Revise this essay as the author asks.
Request: {instruction.strip()[:1500]}

Rules: keep the Markdown headings and every [@key] citation that still applies; add no new sources, numbers or claims that the essay does not
already support; return the whole revised essay and nothing else.

<essay>
{d['content']}
</essay>""", "You are an academic editor. Output only the revised essay in Markdown.", task="wizard-essay-revise")
    out = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", out.strip())
    if len(out) < 40:
        raise ValueError("The AI returned no text. Try again.")
    keys = set(re.findall(r"@([\w:\-]+)", out))
    unknown = sorted(k for k in keys if k not in lib and f"@{k}" not in d["content"])
    nd = db.update_document(d["id"], None, out)
    return {"content": out, "updated": nd["updated"], "unknown_keys": unknown, "previous": d["content"]}


def _essay_for_export(wid: str):
    st = load(wid)
    d = essay_doc(st)
    if not d:
        raise ValueError("There is no essay yet.")
    style = gslides._project_style(st["project_id"])
    return st, d, style


def _file_name(title: str, ext: str) -> str:
    return (re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "", title or "Essay").strip(" .") or "Essay")[:80] + ext


def export_pdf(wid: str) -> tuple[bytes, str]:
    st, d, style = _essay_for_export(wid)
    text, cited, missing = process_citations(d["content"], db.library_map(st["project_id"]), style)
    body = re.sub(r"^#\s+.+\n?", "", text, count=1, flags=re.M) if re.match(r"\s*#\s", text) else text
    return simple_pdf.build(d["title"], body, bibliography(cited, style)), _file_name(d["title"], ".pdf")


def export_docx(wid: str, template: str = "", style: str = "") -> tuple[bytes, str]:
    from .docx_export import build_docx
    st, d, pstyle = _essay_for_export(wid)
    data, _ = build_docx(d["content"], {"title": d["title"]}, db.library_map(st["project_id"]), template or get_settings().get("default_template") or "apa7", style or pstyle, {})
    return data, _file_name(d["title"], ".docx")


def export_google_doc(wid: str, account: str | None = None) -> dict:
    """The Word file goes to "A.F.R.A Decks" in Drive and Google converts it to a Google Doc (drive.file scope only)."""
    if not g.accounts():
        raise ValueError(NO_GOOGLE)
    data, name = export_docx(wid)
    email, _ = g.access_token(account or None)
    folders = g.ensure_folders(email)
    f = g.upload(data, name.rsplit(".", 1)[0], g.MIME_DOCX, folders["decks"], g.MIME_DOC, email)
    return {"id": f["id"], "name": f.get("name") or name, "account": email, "url": f.get("webViewLink") or f"https://docs.google.com/document/d/{f['id']}/edit"}


# ---------------------------------------------------------------- uploaded documents
def add_material(wid: str, filename: str, data: bytes) -> dict:
    name = (filename or "file").replace("\\", "/").rsplit("/", 1)[-1]
    if not name.lower().endswith(UPLOADS):
        raise ValueError("Upload a .txt, .md, .docx, .pdf or .pptx file.")
    st = load(wid)
    if st["source"]["type"] not in ("uploads", "multi"):
        raise ValueError("This draft doesn't start from uploaded documents.")
    text = sources.extract_text(name, data)
    if not text:
        raise ValueError(f"No text found in {name} (a scanned PDF has no text to read).")
    return _put_material(st, name, text)


def _put_material(st: dict, name: str, text: str) -> dict:
    m = {"id": secrets.token_hex(4), "name": name[:120], "text": text[:60000]}
    st.setdefault("materials", []).append(m)
    st["materials"] = st["materials"][-(30 if st["source"]["type"] == "multi" else 12):]
    st["analysis"] = None  # the analysis belongs to the files it read
    st["mockup"] = None
    save(st)
    return {"id": m["id"], "name": m["name"], "chars": len(m["text"])}


def add_google_doc(wid: str, link: str, account: str | None = None) -> dict:
    """A Google Doc by link, through the existing Google connection (plain-text export)."""
    st = load(wid)
    if st["source"]["type"] != "uploads":
        raise ValueError("This draft doesn't start from uploaded documents.")
    if not g.accounts():
        raise ValueError(NO_GOOGLE)
    fid = g.parse_id(link)
    try:
        email, _ = g.access_token(account or None)
        meta = g.file_meta(fid, email)
        text = g.export(fid, "text/plain", email).decode("utf-8", "ignore").strip()
    except g.GoogleError as e:
        raise ValueError("A.F.R.A couldn't open that Google Doc. It can read documents it made or ones your account can open; "
                         "otherwise download it as .docx and upload it here. " + str(e)[:200])
    if not text:
        raise ValueError("That Google Doc has no text.")
    return _put_material(st, meta.get("name") or "Google Doc", text)


def remove_material(wid: str, mid: str):
    st = load(wid)
    st["materials"] = [m for m in st.get("materials") or [] if m["id"] != mid]
    st["analysis"] = None
    save(st)


async def analyze(wid: str) -> dict:
    """The AI reads the uploaded documents and says what could go on slides (key points, figures, tables, quotes)."""
    st = load(wid)
    if not st.get("materials"):
        raise ValueError("Add at least one document first.")
    ensure_ai()
    docs = "\n\n".join(f"### {m['name']}\n{m['text'][:12000]}" for m in st["materials"])
    j = await gslides.complete_json(f"""Read these documents and plan what belongs on presentation slides.
Language of the answer: {st['options'].get('language') or 'the language of the documents'}.

{docs[:50000]}

Return JSON: {{"title": "a short deck title", "summary": "3-5 sentences on what the documents say",
"key_points": ["the 5-10 points a talk must make"], "figures": ["a figure or graph in the documents that would work on a slide"],
"tables": ["a table of numbers worth showing, and what it compares"], "quotes": [{{"text": "a striking sentence, word for word", "by": "who wrote or said it"}}]}}
Only what is really in the documents; empty lists when there is none.""", gslides.SYSTEM, task="wizard-analyze")
    j = j if isinstance(j, dict) else {}
    lst = lambda k: [str(x).strip()[:400] for x in (j.get(k) or []) if str(x).strip()][:12] if isinstance(j.get(k), list) else []
    quotes = [{"text": str(q.get("text") or "").strip()[:400], "by": str(q.get("by") or "").strip()[:120]} for q in (j.get("quotes") or [])
              if isinstance(q, dict) and str(q.get("text") or "").strip()][:6]
    st["analysis"] = {"title": str(j.get("title") or "").strip()[:150], "summary": str(j.get("summary") or "").strip()[:1500], "key_points": lst("key_points"),
                      "figures": lst("figures"), "tables": lst("tables"), "quotes": quotes}
    st["title"] = st["title"] or st["analysis"]["title"]
    save(st)
    return st["analysis"]


def analysis_text(a: dict | None) -> str:
    if not a:
        return ""
    parts = ["### Analysis of the uploaded documents (use it to choose what goes on the slides)", "Summary: " + a["summary"]]
    for label, k in (("Key points", "key_points"), ("Figures worth showing", "figures"), ("Tables worth showing", "tables")):
        if a.get(k):
            parts.append(label + ":\n" + "\n".join("- " + x for x in a[k]))
    if a.get("quotes"):
        parts.append("Quotes worth a slide:\n" + "\n".join(f"- \"{q['text']}\" ({q['by']})" for q in a["quotes"]))
    return "\n".join(parts)


# ---------------------------------------------------------------- the mockup (the contract)
def new_slide_id() -> str:
    return "s" + secrets.token_hex(3)


def _text_list(n: int, name: str, v):
    if v is None:
        return
    if not isinstance(v, list) or any(not isinstance(x, (str, int, float)) for x in v):
        raise ValueError(f"Slide {n}: {name} must be a list of text lines.")


def validate(m) -> dict:
    """Strict check of a mockup coming from the page, then the cleaned copy. Raises ValueError with a plain message."""
    if not isinstance(m, dict) or not isinstance(m.get("slides"), list):
        raise ValueError("The mockup needs a list of slides.")
    if not 1 <= len(m["slides"]) <= MAX_SLIDES:
        raise ValueError(f"A deck needs between 1 and {MAX_SLIDES} slides.")
    out, seen = [], set()
    for n, s in enumerate(m["slides"], 1):
        if not isinstance(s, dict):
            raise ValueError(f"Slide {n} is not a slide.")
        if s.get("layout") not in LAYOUTS:
            raise ValueError(f"Slide {n}: \"{s.get('layout')}\" is not a layout A.F.R.A knows ({', '.join(LAYOUTS)}).")
        for k in ("title", "subtitle", "caption", "notes", "source"):
            if s.get(k) is not None and not isinstance(s[k], str):
                raise ValueError(f"Slide {n}: {k} must be text.")
        _text_list(n, "bullets", s.get("bullets"))
        for k in ("left", "right", "figure", "chart", "quote", "table"):
            if s.get(k) is not None and not isinstance(s[k], dict):
                raise ValueError(f"Slide {n}: {k} must be an object.")
        for k in ("left", "right"):
            _text_list(n, k + ".bullets", (s.get(k) or {}).get("bullets"))
        tb = s.get("table") or {}
        _text_list(n, "table.header", tb.get("header"))
        if tb.get("rows") is not None and (not isinstance(tb["rows"], list) or any(not isinstance(r, list) for r in tb["rows"])):
            raise ValueError(f"Slide {n}: table.rows must be a list of rows.")
        ch = s.get("chart") or {}
        _text_list(n, "chart.labels", ch.get("labels"))
        try:
            vals = [float(v) for v in ch.get("values") or []]
        except (TypeError, ValueError):
            raise ValueError(f"Slide {n}: chart values must be numbers.")
        c = gslides._clean(s, s)
        c["chart"] = {"kind": "bar", "label": str(ch.get("label") or ""), "labels": [str(x) for x in ch.get("labels") or []][:12], "values": vals[:12]} if ch.get("labels") or vals or ch.get("label") else {}
        c["figure"] = {k: str(v) for k, v in (s.get("figure") or {}).items() if k in ("url", "description", "caption") and v is not None}
        sid = s.get("id") if isinstance(s.get("id"), str) and s.get("id") and s["id"] not in seen else new_slide_id()
        seen.add(sid)
        c["id"] = sid
        c["source"] = str(s.get("source") or "")
        out.append(c)
    return {"version": 1, "title": str(m.get("title") or "")[:150], "slides": out}


def from_engine(slides: list[dict], title: str = "") -> dict:
    """Slides the Slides studio wrote (gslides content JSON) -> a mockup."""
    return validate({"title": title, "slides": [{**s, "chart": _chart_in(s.get("chart"))} for s in slides]})


def _chart_in(ch):
    """A chart the AI wrote: keep only what is drawable (labels with numbers); a Sheets chart id can't be drawn here."""
    if not isinstance(ch, dict):
        return {}
    try:
        vals = [float(v) for v in ch.get("values") or []]
    except (TypeError, ValueError):
        return {}
    labels = ch.get("labels") if isinstance(ch.get("labels"), list) else []
    return {"label": ch.get("label") or "", "labels": [str(x) for x in labels], "values": vals} if vals and labels else {}


def blank_mockup(title: str) -> dict:
    return from_engine(tugas_decks.blank_slides(title), title or "New deck")


def items_of(s: dict) -> list[str]:
    """All the text lines of a slide, whatever its layout (used when the layout changes or slides merge)."""
    out = list(s.get("bullets") or [])
    for col in ("left", "right"):
        c = s.get(col) or {}
        out += ([c["heading"]] if c.get("heading") else []) + list(c.get("bullets") or [])
    tb = s.get("table") or {}
    out += [" | ".join(r) for r in tb.get("rows") or []]
    q = s.get("quote") or {}
    out += [q["text"]] if q.get("text") else []
    return [x for x in out if x]


def set_layout(s: dict, layout: str) -> dict:
    """Swap a slide's layout. Text that has no place in the new layout is kept in the slide (switch back and it is there)."""
    if layout not in LAYOUTS:
        raise ValueError(f"\"{layout}\" is not a layout A.F.R.A knows.")
    s = copy.deepcopy(s)
    items = items_of(s)
    s["layout"] = layout
    if layout in ("content", "references", "image_left", "image_right") and not s.get("bullets"):
        s["bullets"] = items
    elif layout == "two_column" and not (s["left"]["bullets"] or s["right"]["bullets"]):
        half = (len(items) + 1) // 2
        s["left"] = {"heading": s["left"].get("heading", ""), "bullets": items[:half]}
        s["right"] = {"heading": s["right"].get("heading", ""), "bullets": items[half:]}
    elif layout == "quote" and not (s.get("quote") or {}).get("text") and items:
        s["quote"] = {"text": items[0], "by": (s.get("quote") or {}).get("by", "")}
    elif layout == "table" and not (s.get("table") or {}).get("rows") and items:
        rows = [[c.strip() for c in x.split("|")] for x in items]
        w = max(len(r) for r in rows)
        s["table"] = {"header": [f"Column {i + 1}" for i in range(w)], "rows": [r + [""] * (w - len(r)) for r in rows]}
    elif layout in ("title", "section", "closing") and not s.get("subtitle") and items:
        s["subtitle"] = items[0]
    return s


def blank_slide(layout: str) -> dict:
    if layout not in LAYOUTS:
        raise ValueError(f"\"{layout}\" is not a layout A.F.R.A knows.")
    base = gslides._clean({"title": "Slide title"}, {"layout": layout})
    base.update(id=new_slide_id(), source="")
    if layout in ("content", "image_left", "image_right"):
        base["bullets"] = ["First point", "Second point"]
    if layout == "two_column":
        base["left"], base["right"] = {"heading": "Left", "bullets": ["Point"]}, {"heading": "Right", "bullets": ["Point"]}
    if layout == "table":
        base["table"] = {"header": ["Column 1", "Column 2"], "rows": [["", ""]]}
    if layout == "quote":
        base["quote"] = {"text": "A short quotation", "by": ""}
    return base


def merge_slides(a: dict, b: dict) -> dict:
    """Two slides into one: two tables with the same columns join; otherwise the second one's text is added to the first."""
    ta, tb = a.get("table") or {}, b.get("table") or {}
    out = copy.deepcopy(a)
    out["title"] = a["title"] or b["title"]
    out["notes"] = "\n".join(x for x in (a.get("notes"), b.get("notes")) if x)
    out["source"] = "; ".join(dict.fromkeys(x for x in (a.get("source"), b.get("source")) if x))[:400]
    if a.get("sources") or b.get("sources"):
        out["sources"] = list(dict.fromkeys((a.get("sources") or []) + (b.get("sources") or [])))
    if a["layout"] == "table" and b["layout"] == "table" and ta.get("rows") and tb.get("rows") and len(ta["rows"][0]) == len(tb["rows"][0]):
        out["table"] = {"header": ta.get("header") or tb.get("header") or [], "rows": ta["rows"] + tb["rows"]}
        return out
    if a["layout"] not in ("content", "two_column", "image_left", "image_right"):
        out = set_layout(out, "content")
    extra = items_of(b) + ([b["caption"]] if b.get("caption") and b["layout"] in ("figure", "chart") else [])
    if b["layout"] in ("title", "section", "closing") and b.get("title"):
        extra = [b["title"]] + extra + ([b["subtitle"]] if b.get("subtitle") else [])
    if out["layout"] == "two_column":
        out["right"]["bullets"] = out["right"]["bullets"] + extra
    else:
        out["bullets"] = out["bullets"] + extra
    return out


def apply_op(m: dict, op: dict) -> dict:
    """add / delete / move / merge / layout / duplicate on a mockup. Returns the new mockup."""
    m = validate(m)
    slides = m["slides"]
    kind = op.get("op")
    n = len(slides)
    idx = lambda k="index": _index(op.get(k), n)
    if kind == "add":
        at = op.get("at")
        at = n if at is None else max(0, min(n, int(at)))
        slides.insert(at, blank_slide(op.get("layout") or "content"))
    elif kind == "delete":
        if n == 1:
            raise ValueError("A deck needs at least one slide.")
        del slides[idx()]
    elif kind == "move":
        i, j = idx("from"), _index(op.get("to"), n)
        slides.insert(j, slides.pop(i))
    elif kind == "merge":
        i = idx()
        if i + 1 >= n:
            raise ValueError("There is no slide after this one to merge with.")
        slides[i:i + 2] = [merge_slides(slides[i], slides[i + 1])]
    elif kind == "layout":
        i = idx()
        slides[i] = set_layout(slides[i], op.get("layout"))
    elif kind == "duplicate":
        i = idx()
        c = copy.deepcopy(slides[i])
        c["id"] = new_slide_id()
        slides.insert(i + 1, c)
    else:
        raise ValueError("Unknown change to the mockup.")
    return validate(m)


def _index(v, n: int) -> int:
    try:
        i = int(v)
    except (TypeError, ValueError):
        raise ValueError("Pick a slide.")
    if not 0 <= i < n:
        raise ValueError(f"The deck has {n} slides.")
    return i


# ---------------------------------------------------------------- checks (the final review)
CITE = re.compile(gslides.CITE_RE.pattern + r"|\[@[^\]]+\]")
NEEDS_SOURCE = ("content", "two_column", "image_left", "image_right", "table", "chart", "quote", "figure")
STARTER = {"Slide title", "First point", "Second point", "A short quotation", "Column 1", "Column 2"}


def slide_text(s: dict) -> str:
    parts = [s.get("title"), s.get("subtitle"), s.get("caption")] + items_of(s) + [(s.get("table") or {}).get("header") and " ".join(s["table"]["header"])]
    parts += [(s.get("figure") or {}).get("caption"), (s.get("quote") or {}).get("by")]
    return " ".join(str(p) for p in parts if p)


def check(m: dict) -> list[dict]:
    """[{"slide": 1-based, "kind", "issue"}]: too much text, missing source, empty placeholder, starter text."""
    flags = []
    for n, s in enumerate(m["slides"], 1):
        L = s["layout"]
        add = lambda kind, issue: flags.append({"slide": n, "kind": kind, "issue": issue})
        text = slide_text(s)
        bullets = list(s.get("bullets") or []) + [b for c in ("left", "right") for b in (s.get(c) or {}).get("bullets", [])]
        words = len(text.split())
        if words > 90 or len(bullets) > 7 or any(len(b.split()) > 25 for b in bullets):
            add("too_much_text", f"Too much text ({words} words): slides read best with short points. Shorten it or split the slide.")
        fig = s.get("figure") or {}
        ch = s.get("chart") or {}
        if L in ("figure", "image_left", "image_right") and not fig.get("url"):
            add("empty_placeholder", "The picture slot is empty: add a picture link, or the slide shows a dashed box." + (f" ({fig['description']})" if fig.get("description") else ""))
        if L == "chart" and not (ch.get("labels") and ch.get("values")):
            add("empty_placeholder", "The chart has no numbers yet.")
        if L == "table" and not (s.get("table") or {}).get("rows"):
            add("empty_placeholder", "The table has no rows.")
        if L == "content" and not bullets:
            add("empty_placeholder", "The slide has no text.")
        if L == "two_column" and not bullets:
            add("empty_placeholder", "Both columns are empty.")
        if L == "quote" and not (s.get("quote") or {}).get("text"):
            add("empty_placeholder", "The quote is empty.")
        if L not in ("quote",) and not (s.get("title") or "").strip():
            add("empty_placeholder", "The slide has no title.")
        if STARTER & set(bullets + [s.get("title", "")] + [(s.get("quote") or {}).get("text", "")]):
            add("empty_placeholder", "Still has the starter text (\"Slide title\", \"First point\"...): write your own.")
        if L in NEEDS_SOURCE and not (s.get("source") or "").strip() and not CITE.search(text + " " + " ".join(bullets)):
            add("no_source", "No source: add a citation like [@key] or fill in the Source line.")
    return flags


# ---------------------------------------------------------------- AI steps: mockup and one slide
def material_body(st: dict) -> dict:
    """The dict the Slides studio's outline/content steps read, for this draft's source."""
    o, src = st["options"], st["source"]
    b = {"title": st["title"], "topic": st["topic"] or st["title"], "audience": o.get("audience", ""), "language": o.get("language", ""),
         "tone": o.get("tone", "academic"), "notes": bool(o.get("notes", True)), "slides": int(o.get("slides") or 10), "minutes": int(o.get("slides") or 10),
         "project_id": st["project_id"], "wizard": True}
    t = src["type"]
    pasted = analysis_text(st.get("analysis"))
    if t in ("essay", "doc"):
        d = essay_doc(st)
        b.update(document_id=d["id"], use_library=True, topic=b["topic"] or d["title"])
    elif t == "uploads":
        ids = []
        for m in st.get("materials") or []:
            mid = f"w{st['id']}{m['id']}"
            gslides.MATERIALS[mid] = {"name": m["name"], "text": m["text"], "added": time.time()}
            ids.append(mid)
        b["materials"] = ids
    elif t == "paper":
        p = db.get_paper(src["id"], with_fulltext=True) or {}
        authors = ", ".join(" ".join(x for x in (a.get("given"), a.get("family")) if x) for a in (p.get("authors") or [])[:6])
        pasted = "\n".join(x for x in (f"### Reading: {p.get('title', '')} [@{p.get('key', '')}]", f"{authors} ({p.get('year') or 'n.d.'}), {p.get('venue') or ''}",
                                       "Abstract: " + (p.get("abstract") or ""), "My notes: " + (p.get("notes") or "") if p.get("notes") else "",
                                       "Full text (excerpt):\n" + (p.get("fulltext") or "")[:14000] if p.get("fulltext") else "", pasted) if x)
        b.update(keys=[p.get("key")] if p.get("key") else [], use_library=True)
    if pasted:
        b["pasted"] = pasted
    return b


async def make_mockup(wid: str, step=lambda s: None) -> dict:
    """Source -> mockup. The outline and the slides come from the Slides studio's own steps (two AI calls)."""
    st = load(wid)
    source_ready(st)
    if st["source"]["type"] == "blank":
        m = blank_mockup(st["title"])
        flags, missing = [], []
    else:
        ensure_ai()
        body = material_body(st)
        items = []
        if st["source"]["type"] == "multi":
            step("Reading the sources\u2026")
            items, notes = await asyncio.to_thread(sources.collect, st["source"].get("items") or [], st.get("materials"))
            body["sources_text"] = await sources.digest(items, language=st["options"].get("language", ""))
            body["source_tags"] = True
            keys = [i["key"] for i in items if i["kind"] == "paper" and i["key"]]
            body.update(keys=keys, use_library=bool(keys), topic=st["topic"] or st["title"] or "the sources")
            st["source_list"] = [{k: i[k] for k in ("tag", "kind", "label", "key")} for i in items]
            st["source_notes"] = notes
        step("Writing the outline\u2026")
        outline = await gslides.outline(body)
        step(f"Writing {len(outline['slides'])} slides\u2026")
        done = await gslides.content({**body, "outline": outline})
        m = from_engine(done["slides"], outline.get("title") or st["title"])
        if items:
            m = apply_sources(m, items)
        flags, missing = done["flags"], done["missing"]
    fresh = load(wid)  # the page may have saved something while the AI worked
    for k in ("source_list", "source_notes"):
        if k in st:
            fresh[k] = st[k]
    st = fresh
    st.update(mockup=m, step="mockup", title=st["title"] or m["title"], mockup_notes={"flags": flags, "missing": missing})
    save(st)
    return public(st)


async def rewrite(wid: str, index: int, instruction: str, layout: str = "") -> dict:
    """The AI rewrites one slide of the mockup, keeping its id and citations."""
    st = load(wid)
    m = validate(st.get("mockup") or {})
    i = _index(index, len(m["slides"]))
    cur = m["slides"][i]
    if layout and layout not in LAYOUTS:
        raise ValueError(f"\"{layout}\" is not a layout A.F.R.A knows.")
    ensure_ai()
    ask = (instruction or "").strip() or "Rewrite it to be clearer and more engaging. Keep the facts and the citations."
    new_s = await gslides.rewrite_slide(m["title"] or st["title"], {k: v for k, v in cur.items() if k != "id"}, ask, layout, wizard=True)
    new_s.update(id=cur["id"], source=cur.get("source", ""))
    new_s["notes"] = new_s["notes"] or cur.get("notes", "")
    new_s["chart"] = _chart_in(new_s.get("chart")) or (cur.get("chart") if (layout or cur["layout"]) == "chart" else {})
    st = load(wid)
    m = validate(st.get("mockup") or {})
    m["slides"][i] = new_s
    st["mockup"] = validate(m)
    save(st)
    return public(st)


def save_mockup(wid: str, m: dict) -> dict:
    st = load(wid)
    source_ready(st)
    st["mockup"] = validate(m)
    return public(save(st))


def edit_mockup(wid: str, op: dict, m: dict | None = None) -> dict:
    st = load(wid)
    source_ready(st)
    st["mockup"] = apply_op(m if m is not None else (st.get("mockup") or {}), op)
    return public(save(st))


# ---------------------------------------------------------------- final review and create
def review(wid: str) -> dict:
    st = load(wid)
    source_ready(st)
    m = validate(st.get("mockup") or {})
    d = next((x for x in designs.all_designs() if x["id"] == (st.get("design") or designs.default()["id"])), None)
    out = st["output"]
    ok = bool(g.accounts())
    return {"checks": check(m), "slides": len(m["slides"]), "design": d, "output": out, "title": m["title"] or st["title"],
            "google": {"connected": ok, "message": "" if ok else NO_GOOGLE}}


def _strings(slide: dict):
    """(container, key) for every piece of text on a slide, so citations can be written out in one pass."""
    for k in ("title", "subtitle", "caption", "notes", "source"):
        yield slide, k
    for lst in (slide.get("bullets"), (slide.get("left") or {}).get("bullets"), (slide.get("right") or {}).get("bullets")):
        for i in range(len(lst or [])):
            yield lst, i
    for c in (slide.get("left"), slide.get("right")):
        yield c, "heading"
    for r in (slide.get("table") or {}).get("rows") or []:
        for i in range(len(r)):
            yield r, i
    yield slide["quote"], "text"
    yield slide["quote"], "by"
    yield slide["figure"], "caption"


def resolve_citations(slides: list[dict], project_id: str, style: str) -> tuple[list[dict], list[str]]:
    """[@key] left in the text (the person typed some) become (Author, year); the References slide lists what is cited."""
    slides = copy.deepcopy(slides)
    refs = [(c, k) for s in slides for c, k in _strings(s) if c is not None and isinstance(c.get(k) if isinstance(c, dict) else c[k], str)]
    if not any("[@" in (c[k] if not isinstance(c, dict) else c.get(k, "")) for c, k in refs):
        return slides, []
    SEP = "\n\u241e\n"
    text, cited, missing = process_citations(SEP.join((c[k] if not isinstance(c, dict) else c.get(k, "")) for c, k in refs), db.library_map(project_id), style)
    for (c, k), v in zip(refs, text.split(SEP)):
        c[k] = v
    if cited:
        lines = bibliography(cited, style)
        ref = next((s for s in slides if s["layout"] == "references"), None)
        if not ref:
            ref = blank_slide("references")
            ref.update(title="References", bullets=[])
            at = len(slides) - 1 if slides and slides[-1]["layout"] == "closing" else len(slides)
            slides.insert(at, ref)
        ref["bullets"] = list(dict.fromkeys(ref["bullets"] + lines))
    return slides, missing


def apply_sources(m: dict, items: list[dict]) -> dict:
    """Each slide records which of the sources it came from (tags S1, S2...) and shows them as its Source line;
    with a single source every content slide gets it."""
    for s in m["slides"]:
        picked = sources.map_tags(s.get("sources") or [], items)
        if not picked and len(items) == 1 and s["layout"] in NEEDS_SOURCE:
            picked = items
        if picked:
            s["sources"] = [i["tag"] for i in picked]
            if not (s.get("source") or "").strip():
                s["source"] = "; ".join(dict.fromkeys(sources.source_text(i) for i in picked))
        else:
            s.pop("sources", None)
    return validate(m)


def add_reference_lines(slides: list[dict], lines: list[str]) -> list[dict]:
    """The References slide also lists sources that are not library papers (documents, lecture files)."""
    if not lines:
        return slides
    ref = next((s for s in slides if s["layout"] == "references"), None)
    if not ref:
        ref = blank_slide("references")
        ref.update(title="References", bullets=[])
        at = len(slides) - 1 if slides and slides[-1]["layout"] == "closing" else len(slides)
        slides.insert(at, ref)
    ref["bullets"] = list(dict.fromkeys(ref["bullets"] + lines))
    return slides


def prepare(st: dict) -> tuple[list[dict], list[str]]:
    m = validate(st.get("mockup") or {})
    by = {i["tag"]: i for i in st.get("source_list") or []}
    used = list(dict.fromkeys(t for s in m["slides"] for t in s.get("sources") or [] if t in by))
    for s in m["slides"]:  # a library paper a slide drew on is always cited, so it reaches the reference list
        for t in s.get("sources") or []:
            i = by.get(t)
            if i and i["kind"] == "paper" and i["key"] and f"[@{i['key']}]" not in s.get("source", ""):
                s["source"] = "; ".join(x for x in (s.get("source"), f"[@{i['key']}]") if x)
    slides, missing = resolve_citations(m["slides"], st["project_id"], gslides._project_style(st["project_id"]))
    slides = add_reference_lines(slides, [by[t]["label"] for t in used if by[t]["kind"] != "paper" or not by[t]["key"]])
    if not st["options"].get("notes", True):
        for s in slides:
            s["notes"] = ""
    return slides, missing


def build(st: dict, step=lambda s: None) -> dict:
    """The mockup, exactly, in the chosen design: a .pptx in the Tugas folder and/or Google Slides."""
    m = validate(st.get("mockup") or {})
    title = (m["title"] or st["title"] or "New deck").strip()[:150]
    out = st["output"]
    slides, missing = prepare(st)
    step(f"Writing the PowerPoint ({len(slides)} slides)\u2026")
    data, flags = tugas_decks.render_deck(slides, st.get("design") or "", title)
    res = {"title": title, "slides": len(slides), "flags": [{"slide": f["slide"], "issue": f["issue"]} for f in flags],
           "path": "", "name": "", "folder": "", "google": None, "google_error": ""}
    if missing:
        res["flags"].append({"slide": 0, "issue": "cited but not in the library (left as ?key): " + ", ".join(missing)})
    if out.get("pptx"):
        res["path"] = tugas_decks.save_deck(out["folder"], title, data)
        res["name"], res["folder"] = res["path"].rsplit("/", 1)[-1], out["folder"]
    if out.get("google"):
        if not g.accounts():
            res["google_error"] = NO_GOOGLE
        else:
            step("Sending it to Google Slides\u2026")
            try:
                res["google"] = gslides.pptx_to_slides("", data, title + ".pptx", out.get("account") or None)
            except (ValueError, g.GoogleError) as e:
                res["google_error"] = str(e)
    return res


def start_create(wid: str) -> dict:
    st = load(wid)
    source_ready(st)
    validate(st.get("mockup") or {})
    designs.pick(st.get("design") or "")  # fails now if the design is gone
    out = st["output"]
    if not (out.get("pptx") or out.get("google")):
        raise ValueError("Choose where the deck goes: a Tugas folder, Google Slides or both.")
    if out.get("pptx") and (out.get("folder") is None or not tugas.resolve(out["folder"]).is_dir()):  # "" is the top level of Tugas
        raise ValueError("Pick a Tugas folder for the .pptx.")
    if out.get("google") and not out.get("pptx") and not g.accounts():
        raise ValueError(NO_GOOGLE)
    jid = secrets.token_hex(5)
    JOBS[jid] = {"id": jid, "wizard": wid, "state": "working", "step": "Starting\u2026", "error": "", "started": time.time(), "result": None}
    for k in [k for k, v in JOBS.items() if time.time() - v["started"] > 86400]:
        JOBS.pop(k, None)

    def run():
        J = JOBS[jid]
        try:
            J["result"] = build(load(wid), lambda s: J.update(step=s))
            cur = load(wid)
            cur.update(result=J["result"], step="create")
            save(cur)
            J.update(state="done", step="Done")
        except Exception as e:  # shown in the page
            J.update(state="error", error=str(e) or e.__class__.__name__)
    threading.Thread(target=run, daemon=True).start()
    return {"job": jid}


def job(jid: str) -> dict:
    j = JOBS.get(jid)
    if not j:
        raise ValueError("Unknown job (A.F.R.A was restarted?). Look in the folder.")
    return j
