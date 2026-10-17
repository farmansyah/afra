"""A.F.R.A for Claude: an MCP server so the Claude desktop app can use A.F.R.A as a tool.

Claude (with its own subscription, no API key needed) does the thinking and writing; A.F.R.A supplies
real papers from the scholarly databases, the project library, documents and the Word export.

Claude Desktop starts this file itself (Settings > Connect to Claude in A.F.R.A sets that up). It speaks
MCP over stdin/stdout and forwards every call to the running A.F.R.A server, starting it when needed.
Standalone on purpose: only the standard library and httpx, and it is started by path, not as a module.
(c) 2026 danafarmansyah. All rights reserved. Crafted with love.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).resolve().parent.parent
URL = os.environ.get("AFRA_URL", "http://127.0.0.1:8765")
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = """A.F.R.A is the user's own research library and manuscript tool, running on their computer.
- Find real papers with search_papers / citation_trail. Never invent references: cite only papers that are in the
  library (add_papers first, then use their cite key).
- In documents, cite with Pandoc keys: [@key], [@key, p. 12; @key2] or @key found that... A.F.R.A turns them into
  APA, IEEE, Harvard, Chicago, Vancouver or MLA citations plus the reference list when exporting to Word.
- Documents are Markdown (# headings, **bold**, tables). save_document stores them; export_word makes the .docx.
- Prefer high-quality sources (Q1/Q2, recent, cited) and say when evidence is thin.
- A message like "A.F.R.A task <id>" means the user pressed a button in A.F.R.A: call get_task, do it, finish_task."""


# ---------------------------------------------------------------- talking to the A.F.R.A server
def _up() -> bool:
    try:
        return httpx.get(f"{URL}/api/version", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def _ensure_server():
    if _up():
        return
    py = Path(sys.executable)
    if os.name == "nt" and py.name.lower() == "python.exe" and (py.parent / "pythonw.exe").exists():
        py = py.parent / "pythonw.exe"  # no console window
    kw = {"cwd": str(BASE_DIR), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    port = URL.rsplit(":", 1)[-1].strip("/")
    subprocess.Popen([str(py), str(BASE_DIR / "run.py"), "--no-browser", "--port", port], **kw)
    for _ in range(60):
        time.sleep(1)
        if _up():
            return
    raise RuntimeError("A.F.R.A did not start. Open A.F.R.A once by hand, then try again.")


def api(method: str, path: str, **kw):
    _ensure_server()
    headers = {"x-afra-source": "claude", **kw.pop("headers", {})}  # lets open A.F.R.A pages refresh
    r = httpx.request(method, f"{URL}{path}", timeout=300, headers=headers, **kw)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text[:300]
        raise RuntimeError(f"A.F.R.A: {detail}")
    return r


def _project(name_or_id: str = "") -> dict:
    projects = api("GET", "/api/projects").json()
    if not projects:
        return {"id": api("POST", "/api/projects", json={"name": "My research"}).json()["id"], "name": "My research"}
    if name_or_id:
        for p in projects:
            if name_or_id in (p["id"], p["name"]) or name_or_id.lower() == p["name"].lower():
                return p
        raise RuntimeError(f"No project called {name_or_id!r}. Projects: " + ", ".join(p["name"] for p in projects))
    return projects[-1]  # the newest


# ---------------------------------------------------------------- compact text for Claude (saves tokens)
def _authors(p: dict) -> str:
    a = [x.get("family") or x.get("given") or "" for x in p.get("authors") or []]
    return (a[0] + " et al." if len(a) > 2 else " & ".join(a)) if a else "Unknown"


def _paper_line(p: dict, abstract_chars=500) -> str:
    q = p.get("quality") or {}
    bits = [f"**{p.get('title', '').strip()}**", f"{_authors(p)} ({p.get('year') or 'n.d.'})"]
    if p.get("venue"):
        bits.append(p["venue"] + (f" [{q['quartile']}]" if q.get("quartile") else ""))
    if p.get("citations") is not None:
        bits.append(f"{p['citations']} citations")
    if p.get("doi"):
        bits.append(f"doi:{p['doi']}")
    if p.get("key"):
        bits.insert(0, f"[@{p['key']}]")
    if p.get("retracted"):
        bits.append("RETRACTED")
    out = " · ".join(bits)
    if abstract_chars and p.get("abstract"):
        ab = p["abstract"]
        out += "\n  " + (ab[:abstract_chars] + "…" if len(ab) > abstract_chars else ab)
    return out


def _list(papers, abstract_chars=500) -> str:
    return "\n".join(f"{i}. {_paper_line(p, abstract_chars)}" for i, p in enumerate(papers, 1)) or "Nothing found."


# ---------------------------------------------------------------- tools
def t_projects(_):
    ps = api("GET", "/api/projects").json()
    return "\n".join(f"- {p['name']} (id {p['id']}): {p['n_papers']} papers, {p['n_docs']} documents" for p in ps) or "No projects yet."


def t_search(a):
    params = {"q": a["query"], "limit": min(int(a.get("limit", 10)), 25),
              "sources": ",".join(a.get("sources") or ["openalex", "europepmc", "crossref"])}
    for k in ("year_from", "year_to"):
        if a.get(k):
            params[k] = a[k]
    data = api("GET", "/api/search", params=params).json()
    res = [p for p in data["results"] if not p.get("retracted")]
    note = ("\n\n(Some databases did not answer: " + "; ".join(data["errors"]) + ")") if data.get("errors") else ""
    return _list(res) + note


def t_trail(a):
    data = api("GET", "/api/snowball", params={"ident": a["doi"], "mode": a.get("direction", "citing"),
                                                 "limit": min(int(a.get("limit", 15)), 30)}).json()
    return _list(data["results"], 300)


def t_journals(a):
    rows = api("GET", "/api/journals", params={"q": a["topic"], "limit": 15}).json()
    lines = []
    for j in rows:
        lines.append(f"- {j.get('name')}: {j.get('matching_articles', '?')} papers on this topic"
                     + (f", {j['quartile']}" if j.get("quartile") else "") + (", open access" if j.get("oa_journal") else "")
                     + (f", APC ${j['apc_usd']}" if j.get("apc_usd") else ""))
    return "\n".join(lines) or "No journals found."


def t_library(a):
    p = _project(a.get("project", ""))
    papers = api("GET", f"/api/projects/{p['id']}/papers").json()
    if a.get("filter"):
        f = a["filter"].lower()
        papers = [x for x in papers if f in (x.get("title", "") + " " + x.get("abstract", "") + " " + x.get("tags", "")).lower()]
    return f"Library of project '{p['name']}' ({len(papers)} papers):\n" + _list(papers, int(a.get("abstract_chars", 0)))


def t_add(a):
    p = _project(a.get("project", ""))
    r = api("POST", f"/api/projects/{p['id']}/papers/identifier", json={"text": " ".join(a["identifiers"])}).json()
    out = [f"Added to '{p['name']}':"] + [f"- [@{x['key']}] {x.get('title', '')}" for x in r["added"]]
    if r["failed"]:
        out.append("Not found: " + ", ".join(r["failed"]))
    return "\n".join(out)


def t_paper(a):
    p = _project(a.get("project", ""))
    key = a["key"].lstrip("@")
    hit = next((x for x in api("GET", f"/api/projects/{p['id']}/papers").json() if x["key"] == key), None)
    if not hit:
        raise RuntimeError(f"No paper with key @{key} in '{p['name']}'.")
    full = api("GET", f"/api/papers/{hit['id']}").json()
    out = _paper_line(full, 4000)
    if full.get("notes"):
        out += "\n\nNotes:\n" + full["notes"]
    if a.get("fulltext"):
        if not full.get("fulltext"):
            try:
                api("POST", f"/api/papers/{hit['id']}/fulltext")
                full = api("GET", f"/api/papers/{hit['id']}").json()
            except RuntimeError as e:
                out += f"\n\n(Full text not available: {e})"
        if full.get("fulltext"):
            out += "\n\nFull text (first 30,000 characters):\n" + full["fulltext"][:30000]
    return out


def t_documents(a):
    p = _project(a.get("project", ""))
    docs = api("GET", f"/api/projects/{p['id']}/documents").json()
    return "\n".join(f"- {d['title']} (id {d['id']}, {d['kind']})" for d in docs) or f"No documents in '{p['name']}' yet."


def t_read(a):
    d = api("GET", f"/api/documents/{a['document_id']}").json()
    return f"# {d['title']}\n\n{d['content']}"


def t_save(a):
    if not a.get("markdown") and not a.get("starter"):
        raise RuntimeError("Give the document text (markdown), or a starter outline.")
    if a.get("document_id"):
        d = api("PUT", f"/api/documents/{a['document_id']}", json={"title": a.get("title"), "content": a["markdown"]}).json()
        return f"Saved '{d['title']}' (id {d['id']}). The user sees it under Documents in A.F.R.A."
    p = _project(a.get("project", ""))
    body = {"title": a.get("title") or "Untitled", "content": a.get("markdown", "")}
    if a.get("starter"):  # coursework outline + its Word format; markdown (if given) replaces the outline text
        d = api("POST", f"/api/projects/{p['id']}/documents", json={"starter": a["starter"], "title": a.get("title")}).json()
        if a.get("markdown"):
            d = api("PUT", f"/api/documents/{d['id']}", json={"content": a["markdown"]}).json()
        return (f"Created '{d['title']}' (id {d['id']}) in '{p['name']}' as a {a['starter']} (tugas Word format).\n\n"
                + ("" if a.get("markdown") else "Outline:\n" + d["content"]))
    d = api("POST", f"/api/projects/{p['id']}/documents", json=body).json()
    return f"Created '{d['title']}' (id {d['id']}) in project '{p['name']}'. The user sees it under Documents in A.F.R.A."


def _export_dir() -> Path:
    for d in (Path.home() / "Documents" / "AFRA exports", BASE_DIR / "data" / "exports"):
        try:
            d.mkdir(parents=True, exist_ok=True)
            return d
        except OSError:
            continue
    raise RuntimeError("No folder to save the Word file in.")


def t_export(a):
    if a.get("document_id"):
        d = api("GET", f"/api/documents/{a['document_id']}").json()
        project_id, markdown, title = d["project_id"], d["content"], d["title"]
    else:
        project_id, markdown, title = _project(a.get("project", ""))["id"], a["markdown"], a.get("title") or "document"
    meta = {"title": a.get("title") or title}
    for k in ("authors", "affiliation", "keywords", "course", "instructor", "student_id", "department", "city", "date", "doc_type"):
        if a.get(k):
            meta[k] = a[k]
    tpl = a.get("template") or (d.get("meta", {}).get("template") if a.get("document_id") else None) or "apa7"
    if a.get("document_id") and d.get("meta", {}).get("doc_type"):
        meta.setdefault("doc_type", d["meta"]["doc_type"])
    lang = a.get("language") or ("id" if tpl in ("tugas", "thesis") else "en")
    r = api("POST", "/api/export/docx", json={"project_id": project_id, "markdown": markdown, "meta": meta, "template": tpl,
                                             "style": a.get("citation_style"), "options": {"lang": lang}})
    name = re.sub(r"[^\w\- ]+", "", meta["title"]).strip()[:80] or "document"
    path = _export_dir() / f"{name}.docx"
    path.write_bytes(r.content)
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])  # show it in Finder
    elif os.name == "nt":
        subprocess.Popen(["explorer", "/select,", str(path)])
    out = f"Saved the Word file: {path} ({r.headers.get('X-Cited', '?')} references)."
    missing = httpx.QueryParams(f"m={r.headers.get('X-Missing', '')}").get("m")
    if missing:
        out += f"\nCite keys not in the library (shown as-is in the document): {missing}. Add those papers first."
    return out


def t_slide_material(a):
    m = api("POST", "/api/slides/material", json={"document_id": a["document_id"], "style": a.get("citation_style")}).json()
    out = f"# {m['title']}\n\n{m['text']}\n\n## References ({m['style'].upper()})\n" + "\n".join(f"- {r}" for r in m["references"])
    if m["missing"]:
        out += "\n\n(Cited but not in the library, so left out: " + ", ".join(m["missing"]) + ")"
    return out


def t_get_task(a):
    t = api("GET", f"/api/handoff/{a['task_id']}").json()
    fmt = ("Your answer must be valid JSON in exactly the format described above (no commentary)." if t["json"]
           else "Answer with the requested text only (no preface, no closing remarks): it is inserted into A.F.R.A as is.")
    return (f"A.F.R.A task {t['id']} ({t['task']}). The user clicked a button in A.F.R.A and is waiting there.\n"
            f"Do exactly what the instructions say, then call finish_task with task_id \"{t['id']}\" and your answer. "
            f"{fmt} Don't call other tools unless the instructions need them. In the chat, just say it's done.\n\n"
            f"<instructions role=\"system\">\n{t['system']}\n</instructions>\n\n<instructions role=\"user\">\n{t['prompt']}\n</instructions>")


def t_finish_task(a):
    r = api("POST", f"/api/handoff/{a['task_id']}", json={"result": a["result"]}).json()
    if r.get("next"):
        return f"Done: A.F.R.A received it. One more step of the same button is waiting: call get_task with task_id \"{r['next']}\"."
    return "Done: the result is now in A.F.R.A, where the user clicked. Tell the user in one short sentence."


def t_pending(_):
    ts = api("GET", "/api/handoff").json()
    return "\n".join(f"- {t['id']}: {t['task']}" for t in ts) or "No A.F.R.A tasks are waiting."


def t_check_refs(a):
    p = _project(a.get("project", ""))
    r = api("POST", "/api/refcheck", json={"project_id": p["id"], "text": a.get("markdown", "")}).json()
    lines = [f"Checked {r['checked']} references."]
    if r["missing_keys"]:
        lines.append("Cited but not in the library: " + ", ".join("@" + k for k in r["missing_keys"]))
    for x in r["results"]:
        if x.get("issues"):
            lines.append(f"- @{x.get('key')}: " + "; ".join(x["issues"]))
    return "\n".join(lines) if len(lines) > 1 else lines[0] + " No problems found (no retractions, DOIs resolve)."


S = {"type": "string"}
PROJECT = {"type": "string", "description": "Project name or id. Leave out for the newest project."}
TOOLS = {
    "list_projects": (t_projects, "List the user's A.F.R.A research projects.", {}, []),
    "search_papers": (t_search, "Search scholarly databases (OpenAlex, Europe PMC/PubMed, Crossref, arXiv, Semantic Scholar) "
                      "for real papers. Returns title, authors, year, journal with quartile, citations, DOI and abstract. "
                      "Run several focused keyword queries (synonyms, sub-topics) rather than one long sentence.",
                      {"query": {**S, "description": "3-8 keywords"}, "limit": {"type": "integer", "default": 10},
                       "sources": {"type": "array", "items": S, "description": "openalex, europepmc, crossref, arxiv, semanticscholar"},
                       "year_from": {"type": "integer"}, "year_to": {"type": "integer"}}, ["query"]),
    "citation_trail": (t_trail, "Snowballing from one paper: the papers it cites (references), that cite it (citing) or related ones.",
                       {"doi": {**S, "description": "DOI or OpenAlex id"}, "direction": {"type": "string", "enum": ["references", "citing", "related"]},
                        "limit": {"type": "integer", "default": 15}}, ["doi"]),
    "find_journals": (t_journals, "Which journals publish on a topic, with quartile, open access and publication fee.",
                      {"topic": S}, ["topic"]),
    "library": (t_library, "List the papers saved in a project, with their cite keys.",
                {"project": PROJECT, "filter": {**S, "description": "optional word to filter by"},
                 "abstract_chars": {"type": "integer", "default": 0, "description": "how much abstract to include"}}, []),
    "add_papers": (t_add, "Save papers or books to the project library by DOI, arXiv id or ISBN (books: read the ISBN from a photo "
                   "of the back cover if the user sends one). Returns the cite key of each.",
                   {"identifiers": {"type": "array", "items": S}, "project": PROJECT}, ["identifiers"]),
    "read_paper": (t_paper, "Details of one library paper: full abstract, the user's notes and, if asked, the full text (open-access PDF).",
                   {"key": {**S, "description": "cite key, e.g. smith2020deep"}, "fulltext": {"type": "boolean", "default": False},
                    "project": PROJECT}, ["key"]),
    "list_documents": (t_documents, "List the user's documents (drafts, assignments, papers) in a project.", {"project": PROJECT}, []),
    "read_document": (t_read, "Read a document's Markdown text.", {"document_id": S}, ["document_id"]),
    "save_document": (t_save, "Create a document, or replace an existing one's text (give document_id). Markdown with [@key] citations. "
                      "For coursework pass starter (makalah, praktikum, proposal, resume) to get the campus outline and Word format.",
                      {"markdown": S, "title": S, "document_id": {**S, "description": "to update an existing document"},
                       "starter": {"type": "string", "enum": ["makalah", "praktikum", "proposal", "resume"]},
                       "project": PROJECT}, []),
    "export_word": (t_export, "Make a formatted Word (.docx) file with citations and the reference list, saved in Documents/AFRA exports.",
                    {"document_id": {**S, "description": "export a saved document"}, "markdown": {**S, "description": "or export this text"},
                     "title": S, "authors": {**S, "description": "several students: 'Name (NIM); Name (NIM)'"},
                     "affiliation": {**S, "description": "university"}, "keywords": S,
                     "course": {**S, "description": "mata kuliah (tugas cover)"}, "instructor": {**S, "description": "dosen pengampu"},
                     "student_id": {**S, "description": "NIM (single author)"}, "department": {**S, "description": "program studi / fakultas"},
                     "city": S, "date": {**S, "description": "e.g. the year"},
                     "doc_type": {**S, "description": "cover heading for tugas: MAKALAH, LAPORAN PRAKTIKUM, PROPOSAL PENELITIAN, RESUME JURNAL..."},
                     "language": {"type": "string", "enum": ["id", "en"], "description": "labels: Daftar Pustaka/BAB or References/Chapter"},
                     "template": {"type": "string", "enum": ["apa7", "ieee", "journal", "thesis", "tugas", "report"],
                                  "description": "tugas = Indonesian coursework (cover with logo, Kata Pengantar, Daftar Isi, BAB I...); "
                                                 "thesis = skripsi/tesis; default: the document's own format, else apa7"},
                     "citation_style": {"type": "string", "enum": ["apa", "ieee", "harvard", "chicago", "vancouver", "mla"]},
                     "project": PROJECT}, []),
    "slide_material": (t_slide_material, "A document ready for a presentation: its text with citations already formatted "
                       "(e.g. (Lee, 2020) or [1]) and the matching reference list for a final References slide.",
                       {"document_id": S, "citation_style": {"type": "string", "enum": ["apa", "ieee", "harvard", "chicago", "vancouver", "mla"]}},
                       ["document_id"]),
    "get_task": (t_get_task, "Fetch a task the user started with a button in A.F.R.A (the message names its id, e.g. "
                 "'A.F.R.A task k3x9…'). Returns the exact instructions to carry out.", {"task_id": S}, ["task_id"]),
    "finish_task": (t_finish_task, "Send the answer of an A.F.R.A task back; it appears in A.F.R.A where the user clicked.",
                    {"task_id": S, "result": {**S, "description": "the answer only (text, or JSON if the task asks for JSON)"}},
                    ["task_id", "result"]),
    "pending_tasks": (t_pending, "List A.F.R.A tasks waiting for Claude (when the user says 'do my A.F.R.A tasks').", {}, []),
    "check_references": (t_check_refs, "Check cited papers: missing from the library, retracted, broken DOIs.",
                         {"markdown": {**S, "description": "text whose citations to check; leave out to check the whole library"},
                          "project": PROJECT}, []),
}


# ---------------------------------------------------------------- ready-made workflows (Claude's "+" menu > A.F.R.A)
TOPIC = {"name": "topic", "description": "Title, question or topic", "required": True}
PROMPTS = {
    "literature_review": ("Literature review: find the key papers on a topic and write a cited review.", [TOPIC], """Write a literature review on: {topic}
1. Split the topic into its concepts and synonyms, then run 5-8 focused search_papers queries (recent years first).
2. Follow citation_trail from the 2-3 most cited relevant papers.
3. Pick the 15-25 best sources (relevant, Q1/Q2 or highly cited, no retractions); add_papers them.
4. Write the review in themes (not paper by paper): what is known, where studies disagree, and the research gaps.
   Cite every claim with [@key]. Language: the language of the topic.
5. save_document it, run check_references, then tell me the document title and offer export_word."""),
    "coursework": ("Tugas / coursework: answer an assignment with real references, ready to hand in as Word.",
                   [{"name": "assignment", "description": "The assignment question or instructions (paste them)", "required": True},
                    {"name": "length", "description": "e.g. 1500 words, 5 pages", "required": False},
                    {"name": "style", "description": "citation style: apa, ieee, harvard...", "required": False}], """Help me with this assignment:
<assignment>{assignment}</assignment>
Length: {length}. Citation style: {style}.
1. Restate what the assignment asks and make a short outline (Pendahuluan/Introduction, body, Kesimpulan/Conclusion).
   Show me the outline before writing.
2. Find 6-12 real, recent sources with search_papers and add_papers them.
3. Write in my own clear student voice, in the language of the assignment, citing with [@key].
   Explain ideas, don't just stack quotes.
4. save_document (with starter "makalah", "praktikum", "proposal" or "resume" when it is that kind of tugas), check_references,
   then export_word: template "tugas" for an Indonesian campus assignment (ask me for course, lecturer, names + NIM,
   program studi, university, city, year for the cover), or "apa7" for an English essay."""),
    "find_evidence": ("Find papers that support (or contradict) a claim, with the sentence that backs it.",
                      [{"name": "claim", "description": "Your argument or sentence", "required": True}], """Find evidence for this claim: {claim}
Search with 4-6 queries for supporting studies and 1-2 for contradicting ones. For each relevant paper give:
support / partial / contradicts, the exact sentence from the abstract, and its quality (journal quartile, year, citations).
Add the good ones with add_papers and give me a ready-to-paste sentence with [@key] citations."""),
    "write_paper": ("Research article: outline, draft section by section, then a reviewer check.",
                    [TOPIC, {"name": "journal", "description": "Target journal (optional)", "required": False}], """Help me write a research article: {topic}. Target journal: {journal}.
Work step by step and wait for my OK after each: (1) library check and search for missing literature,
(2) research gap with 3-5 citations each, (3) outline (IMRaD), (4) draft each section into one document with
[@key] citations, (5) title, abstract and keywords, (6) a strict reviewer critique, (7) export_word.
Never invent data or results: leave [TODO: my data] where my own results belong."""),
    "make_slides": ("Slides: turn a document into an editable PowerPoint presentation with a References slide.",
                    [{"name": "document", "description": "Document title (leave empty to choose)", "required": False},
                     {"name": "minutes", "description": "How long the talk is, e.g. 10 minutes", "required": False}], """\
Make a presentation from my A.F.R.A document "{document}" (talk length: {minutes}). Find it with list_documents and
get the content with slide_material (citations already formatted, plus the reference list).
- If the PPT Master skill is available here (the AFRA-Slides folder in the Code tab), use it for a designed deck.
- Otherwise create an editable .pptx file yourself: a title slide (title, my name, course, date), an outline, about
  one content slide per minute with short bullets (not paragraphs), a table or simple chart where the document has
  numbers, a conclusion slide, and a References slide with the reference list. Put what I should say in the
  speaker notes. Use the document's language. If you can't create files, tell me to switch on
  "Code execution and file creation" in Claude's Settings > Capabilities.
Use only facts and sources from the document; never invent references."""),
    "review_my_draft": ("Check a draft in A.F.R.A: structure, argument, citations and missing references.",
                        [{"name": "document", "description": "Document title (leave empty to choose)", "required": False}], """Review my draft "{document}" (use list_documents / read_document). Check structure, argument, clarity and
whether each claim is cited; run check_references; suggest papers for uncited claims with search_papers.
Give a prioritised list of fixes, then offer to apply them with save_document."""),
}


def _prompt(name: str, args: dict) -> dict:
    desc, params, text = PROMPTS[name]
    values = {p["name"]: (args.get(p["name"]) or "").strip() or "(not given)" for p in params}
    return {"description": desc, "messages": [{"role": "user", "content": {"type": "text", "text": text.format(**values)}}]}


# ---------------------------------------------------------------- MCP over stdio (JSON-RPC, one message per line)
def _send(msg):
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle(req: dict):
    method, rid, params = req.get("method"), req.get("id"), req.get("params") or {}
    if rid is None:  # notification
        return None
    if method == "initialize":
        asked = params.get("protocolVersion")
        return {"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                "capabilities": {"tools": {}, "prompts": {}}, "instructions": INSTRUCTIONS,
                "serverInfo": {"name": "afra", "title": "A.F.R.A research library", "version": _version()}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": props, "required": req_}}
                          for n, (_, d, props, req_) in TOOLS.items()]}
    if method == "prompts/list":
        return {"prompts": [{"name": n, "description": d, "arguments": a} for n, (d, a, _) in PROMPTS.items()]}
    if method == "prompts/get":
        if params.get("name") not in PROMPTS:
            raise KeyError(f"Unknown prompt {params.get('name')}")
        return _prompt(params["name"], params.get("arguments") or {})
    if method == "tools/call":
        tool = TOOLS.get(params.get("name"))
        if not tool:
            raise KeyError(f"Unknown tool {params.get('name')}")
        try:
            text, err = tool[0](params.get("arguments") or {}), False
        except Exception as e:  # tool errors go back to Claude as text so it can react
            text, err = str(e), True
        return {"content": [{"type": "text", "text": text}], "isError": err}
    raise KeyError(f"Method not found: {method}")


def _version() -> str:
    m = re.search(r'VERSION\s*=\s*"([^"]+)"', (BASE_DIR / "app" / "version.py").read_text(encoding="utf-8"))
    return m.group(1) if m else "0"


def main():
    sys.stdin.reconfigure(encoding="utf-8")  # Windows defaults to the ANSI code page
    sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            _send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        try:
            result = handle(req)
            if result is not None:
                _send({"jsonrpc": "2.0", "id": req["id"], "result": result})
        except KeyError as e:
            _send({"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": -32601, "message": str(e).strip("'\"")}})
        except Exception as e:
            _send({"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": -32603, "message": str(e)}})


if __name__ == "__main__":
    main()
