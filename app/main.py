"""A.F.R.A: FastAPI server. Run:  python run.py
(c) 2026 danafarmansyah. All rights reserved. Crafted with love."""
import json
import re
from pathlib import Path
from urllib.parse import quote

import asyncio
import httpx
import time

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import checks, db, discovery, gaps, integrations, models, quality, research, updater, writer
from .citations import STYLES, bibliography, parse_bibtex, to_bibtex, to_ris
from .config import BASE_DIR, get_settings, save_settings
from .docx_export import TEMPLATES, build_docx
from .llm import LLMError, complete, complete_json, stream_chat
from .search import (SOURCES, fetch_page, journals_for_topic, lookup_identifier, pdf_bytes_to_text, search_all,
                     snowball)

app = FastAPI(title="A.F.R.A: Article Finder & Research Assistant", description="(c) 2026 danafarmansyah. All rights reserved.")
STATIC = BASE_DIR / "static"
db.init()


async def _backup_loop():
    """Safety net: a consistent backup at startup if none in 6 h, then every 6 h (newest 20 kept)."""
    while True:
        try:
            if db.last_backup_age() > 6 * 3600:
                await asyncio.to_thread(db.backup, "auto")
        except Exception as e:  # never let backups crash the server
            print("backup failed:", e)
        await asyncio.sleep(1800)


async def _file_sync_loop():
    """Rewrite linked .bib/.ris files a few seconds after the library changes (Mendeley, JabRef, EndNote...)."""
    while True:
        await asyncio.sleep(4)
        while db.DIRTY:
            pid = db.DIRTY.pop()
            try:
                await asyncio.to_thread(integrations.auto_export, pid)
            except Exception as e:
                print("auto-export failed:", e)


@app.on_event("startup")
async def _startup():
    asyncio.create_task(_backup_loop())
    asyncio.create_task(_file_sync_loop())


@app.middleware("http")
async def no_stale_frontend(request, call_next):
    # the UI changes with updates; make the browser revalidate instead of running an old cached copy
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(LLMError)
async def llm_error(_, exc):
    return JSONResponse({"detail": str(exc)}, status_code=502)


@app.exception_handler(ValueError)
async def value_error(_, exc):
    return JSONResponse({"detail": str(exc)}, status_code=422)


def _404(obj, what="Not found"):
    if not obj:
        raise HTTPException(404, what)
    return obj


async def _text_stream(gen):
    try:
        async for piece in gen:
            yield piece
    except Exception as e:  # surface errors inside the stream
        yield f"\n\n[ERROR] {e}"


def _stream(prompt, system="", task="chat", **kw):
    return StreamingResponse(_text_stream(stream_chat([{"role": "user", "content": prompt}], system=system, task=task, **kw)),
                             media_type="text/plain; charset=utf-8")


# ---------------------------------------------------------------- meta
@app.get("/api/meta")
def meta():
    return {"styles": {k: v["name"] for k, v in STYLES.items()},
            "templates": {k: v["name"] for k, v in TEMPLATES.items()},
            "sources": SOURCES, "tools": {k: v[0] for k, v in writer.TOOLS.items()},
            "rubric": writer.RUBRIC, "gate": writer.GATE, "sjr_journals": quality.sjr_count()}


@app.get("/api/version")
def version():
    from .version import VERSION
    return {"version": VERSION, "dev_copy": updater.is_dev_copy()}


@app.get("/api/update/check")
async def update_check():
    return await updater.check()


@app.post("/api/update/apply")
async def update_apply():
    db.backup("before-update")
    result = await updater.apply()
    asyncio.get_running_loop().call_later(1.5, updater.restart)  # respond first, then restart on the new code
    return result


@app.get("/api/settings")
def get_s():
    return get_settings()


@app.put("/api/settings")
def put_s(body: dict):
    return save_settings(body)


@app.post("/api/settings/test")
async def test_llm():
    out = {"ok": True}
    s = get_settings()
    for role, key in (("main", "llm_model"), ("fast", "llm_fast_model"), ("review", "llm_review_model")):
        if role == "main" or s.get(key):
            try:
                out[role] = (await complete("Reply with exactly: OK", max_tokens=20, role=role, task="test"))[:100]
            except LLMError as e:
                out[role] = "ERROR: " + str(e)[:200]
                out["ok"] = False
    return out


@app.get("/api/models")
async def list_models(src: str = "1", refresh: bool = False):
    try:
        return {"models": await models.catalogue(src, refresh)}
    except Exception as e:
        return {"models": [], "error": f"{type(e).__name__}: {str(e)[:200]}"}


@app.get("/api/models/recommend")
async def recommend_models():
    return await models.recommend()


# ---------------------------------------------------------------- projects
@app.get("/api/projects")
def projects():
    return db.list_projects()


@app.post("/api/projects")
def create_project(body: dict):
    pid = db.create_project(body.get("name") or "Untitled project", body.get("description", ""),
                            body.get("citation_style", get_settings()["default_citation_style"]))
    return {"id": pid}


@app.put("/api/projects/{pid}")
def update_project(pid: str, body: dict):
    db.update_project(pid, **body)
    return {"ok": True}


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    db.backup("before-delete-project")
    db.delete_project(pid)
    return {"ok": True}


# ---------------------------------------------------------------- search & library
@app.get("/api/search")
async def search(q: str, sources: str = "openalex,arxiv", limit: int = 10,
                 year_from: int | None = None, year_to: int | None = None, enrich: bool = True):
    results, errors = await search_all(q, sources.split(","), limit, year_from, year_to)
    if enrich:
        await quality.enrich(results)
    return {"results": results, "errors": errors}


@app.get("/api/snowball")
async def snowball_ep(ident: str, mode: str = "references", limit: int = 30):
    data = await snowball(ident, mode, limit)
    await quality.enrich(data["results"])
    return data


@app.get("/api/journals")
async def journals(q: str, years: int = 5, limit: int = 25):
    return await quality.journal_table(await journals_for_topic(q, years, limit))


@app.post("/api/discover")
async def discover_ep(body: dict):
    return await discovery.discover(body["title"], body.get("sources") or ["openalex", "europepmc", "crossref"],
                                    body.get("extra", ""), body.get("year_from"), body.get("year_to"))


@app.post("/api/evidence")
async def evidence_ep(body: dict):
    lib = db.list_papers(body["project_id"]) if body.get("project_id") and body.get("use_library", True) else []
    return await discovery.evidence(body["claim"], body.get("sources") or ["openalex", "europepmc"], lib,
                                    body.get("year_from"), body.get("year_to"))


@app.post("/api/manuscript/analyze")
async def manuscript_analyze(project_id: str, file: UploadFile = File(...)):
    text = gaps.manuscript_text(file.filename, await file.read())
    if len(text.strip()) < 200:
        raise HTTPException(422, "Could not read enough text from this file (scanned PDF?). Try the .docx version.")
    analysis = await gaps.analyze_manuscript(text)
    doc = db.create_document(project_id, analysis.get("title") or Path(file.filename).stem, "doc", text,
                             {"source_file": file.filename, "analysis": analysis})
    return {"doc_id": doc["id"], "analysis": analysis}


@app.post("/api/gaps/analyze")
async def gaps_analyze(body: dict):
    topic = body["topic"].strip()
    fs = await gaps.facets(topic)
    recent = body.get("recent_from")
    cov, prior = await asyncio.gather(gaps.coverage(fs["facets"], recent),
                                      gaps.prior_work(topic, body.get("sources") or ["openalex", "europepmc", "crossref"]))
    gaps.assign_keys(body["project_id"], prior["papers"])
    meta = {"topic": topic, "facets": fs["facets"], "contribution": fs["contribution"], "coverage": cov,
            "papers": prior["papers"], "table": prior["table"], "total_found": prior.get("total_found", 0),
            "language": body.get("language", "English")}
    doc = db.create_document(body["project_id"], "Gap analysis: " + topic[:90], "gap", "", meta)
    return {"doc_id": doc["id"], **meta}


@app.post("/api/gaps/{doc_id}/write")
async def gaps_write(doc_id: str):
    doc = _404(db.get_document(doc_id))
    m = doc["meta"]
    prompt = gaps.p_gap_writeup(m["topic"], m["facets"], m["contribution"], m["coverage"], m["papers"], m["table"],
                                m.get("language", "English"))
    return _stream(prompt, "You are a senior researcher writing a rigorous, honest gap analysis.", task="gaps-write")


@app.post("/api/gaps/{doc_id}/finalize")
def gaps_finalize(doc_id: str, body: dict):
    """Save the written text and add the cited papers to the library (keys stay identical)."""
    doc = _404(db.get_document(doc_id))
    text = body.get("content", doc["content"])
    cited = set(re.findall(r"@([A-Za-z][\w:\-]*[A-Za-z0-9])", text))
    added = 0
    for p in doc["meta"].get("papers", []):
        if p.get("key") in cited:
            stored = db.add_paper(doc["project_id"], p, tags="gap-finder")
            if stored["key"] != p["key"]:
                text = re.sub(r"@" + re.escape(p["key"]) + r"\b", "@" + stored["key"], text)
            added += 1
    db.update_document(doc_id, content=text)
    return {"added": added}


# ---------------------------------------------------------------- submission checks
@app.post("/api/refcheck")
async def refcheck(body: dict):
    papers = db.list_papers(body["project_id"])
    missing = []
    if body.get("text"):
        cited = set(re.findall(r"@([A-Za-z][\w:\-]*[A-Za-z0-9])", body["text"]))
        keys = {p["key"] for p in papers}
        missing = sorted(cited - keys)
        papers = [p for p in papers if p["key"] in cited]
    return {"results": await checks.check_references(papers), "missing_keys": missing, "checked": len(papers)}


@app.get("/api/journal-search")
async def journal_search(q: str):
    return await checks.find_journals(q)


@app.post("/api/journal-fit")
async def journal_fit(body: dict):
    return await checks.journal_fit(body["source_id"], body.get("markdown", ""), body.get("guidelines", ""), body.get("ai", True))


# ---------------------------------------------------------------- Zotero / Mendeley sync
@app.get("/api/zotero/collections")
async def zotero_collections(mode: str = "local"):
    return await integrations.collections(mode)


@app.post("/api/projects/{pid}/zotero/import")
async def zotero_import(pid: str, body: dict):
    return await integrations.import_zotero(pid, body.get("mode", "local"), body.get("collection", ""))


@app.post("/api/projects/{pid}/zotero/export")
async def zotero_export(pid: str, body: dict):
    return await integrations.export_zotero(pid, body.get("collection", ""))


@app.get("/api/projects/{pid}/filesync")
def filesync_get(pid: str):
    return integrations.file_settings(pid)


@app.put("/api/projects/{pid}/filesync")
def filesync_put(pid: str, body: dict):
    cfg = integrations.save_file_settings(pid, body)
    out = {"settings": cfg}
    if cfg["export_path"]:
        out["export"] = integrations.auto_export(pid)
    return out


@app.post("/api/projects/{pid}/filesync/import")
def filesync_import(pid: str):
    return integrations.import_file(pid)


@app.post("/api/quality/sjr")
async def import_sjr(file: UploadFile = File(...)):
    n = await asyncio.to_thread(quality.import_sjr, await file.read())
    return {"journals": n}


# ---------------------------------------------------------------- usage & backups
@app.get("/api/usage")
def usage(days: int = 0):
    return db.usage_summary(time.time() - days * 86400 if days else 0)


@app.get("/api/backups")
def backups():
    return {"backups": db.list_backups(), "dir": str(db.BACKUP_DIR), "db": str(db.DB_PATH)}


@app.post("/api/backups")
def backup_now():
    return {"name": db.backup("manual")}


@app.get("/api/backups/{name}")
def download_backup(name: str):
    path = db.BACKUP_DIR / Path(name).name
    _404(path.exists() and path.suffix == ".db")
    return FileResponse(path, filename=path.name)


@app.post("/api/fetch")
async def fetch(body: dict):
    return await fetch_page(body["url"], max_chars=int(body.get("max_chars", 30000)))


@app.get("/api/projects/{pid}/papers")
def papers(pid: str):
    return db.list_papers(pid)


@app.post("/api/projects/{pid}/papers")
def add_papers(pid: str, body: dict):
    items = body.get("papers") or [body.get("paper")]
    return [db.add_paper(pid, p, tags=body.get("tags", "")) for p in items if p]


@app.post("/api/projects/{pid}/papers/identifier")
async def add_by_identifier(pid: str, body: dict):
    added, failed = [], []
    for ident in re.split(r"[\s,;]+", body.get("text", "")):
        if not ident.strip():
            continue
        try:
            p = await lookup_identifier(ident)
            if p:
                added.append(db.add_paper(pid, p))
            else:
                failed.append(ident)
        except Exception:
            failed.append(ident)
    return {"added": added, "failed": failed}


@app.post("/api/projects/{pid}/papers/bibtex")
def import_bibtex(pid: str, body: dict):
    items = parse_bibtex(body.get("text", ""))
    return {"added": [db.add_paper(pid, p) for p in items]}


@app.post("/api/projects/{pid}/papers/pdf")
async def import_pdf(pid: str, file: UploadFile = File(...)):
    data = await file.read()
    text = pdf_bytes_to_text(data)
    if not text.strip():
        raise HTTPException(422, "No extractable text (scanned PDF?). Try OCR first.")
    paper = None
    doi = re.search(r"\b(10\.\d{4,9}/[^\s\"<>]+[^\s\"<>.,;)])", text[:8000])
    if doi:
        try:
            paper = await lookup_identifier(doi.group(1))
        except Exception:
            paper = None
    if not paper:
        try:
            meta_ = await complete_json(
                "Extract bibliographic metadata from the first page(s) of this paper. Return JSON: "
                '{"title":"","authors":[{"given":"","family":""}],"year":2020,"venue":"","volume":"","issue":"",'
                f'"pages":"","doi":"","abstract":"","type":"article"}}\n\n<text>\n{text[:6000]}\n</text>', fast=True, task="pdf-metadata")
            paper = {k: v for k, v in meta_.items() if v}
        except Exception:
            first = next((l.strip() for l in text.splitlines() if len(l.strip()) > 15), "")
            paper = {"title": first[:200] or Path(file.filename or "document.pdf").stem, "authors": [], "type": "article"}
    paper["source"] = "pdf"
    paper["filename"] = file.filename
    stored = db.add_paper(pid, paper, fulltext=text)
    db.update_paper(stored["id"], {"fulltext": text[:400000]})
    return db.get_paper(stored["id"], with_fulltext=False)


@app.get("/api/papers/{paper_id}")
def get_paper(paper_id: str):
    return _404(db.get_paper(paper_id))


@app.put("/api/papers/{paper_id}")
def update_paper(paper_id: str, body: dict):
    return _404(db.update_paper(paper_id, body))


@app.delete("/api/papers/{paper_id}")
def delete_paper(paper_id: str):
    if db.last_backup_age() > 3600:
        db.backup("before-delete")
    db.delete_paper(paper_id)
    return {"ok": True}


@app.post("/api/papers/{paper_id}/fulltext")
async def fetch_fulltext(paper_id: str):
    p = _404(db.get_paper(paper_id))
    url = p.get("pdf_url") or p.get("url")
    if not url:
        raise HTTPException(422, "Paper has no URL")
    page = await fetch_page(url, max_chars=400000)
    db.update_paper(paper_id, {"fulltext": page["text"]})
    return {"chars": len(page["text"])}


@app.post("/api/papers/{paper_id}/summarize")
async def summarize_paper(paper_id: str):
    p = _404(db.get_paper(paper_id))
    text = p.get("fulltext") or p.get("abstract") or ""
    if not text:
        raise HTTPException(422, "No abstract or full text available")
    prompt = (f"Paper: {p.get('title')} ({p.get('year')})\n\n<text>\n{text[:40000]}\n</text>\n\n"
              "Write structured reading notes in Markdown: **Research question**, **Method** (design, data, sample), "
              "**Key findings** (with numbers), **Contribution**, **Limitations**, **Relevance / how to cite it**. Be concise.")
    return _stream(prompt, task="reading-notes", fast=True)


@app.get("/api/projects/{pid}/bibtex")
def export_bibtex(pid: str):
    return Response(to_bibtex(db.list_papers(pid)), media_type="application/x-bibtex",
                    headers={"Content-Disposition": 'attachment; filename="library.bib"'})


# ---------------------------------------------------------------- PDFs, quotes, collections, duplicates
def _store_pdf(paper_id: str, data: bytes) -> int:
    if not data.startswith(b"%PDF"):
        raise HTTPException(422, "That file is not a PDF")
    db.PDF_DIR.mkdir(parents=True, exist_ok=True)
    tmp = db.pdf_path(paper_id).with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(db.pdf_path(paper_id))
    text = pdf_bytes_to_text(data)
    if text.strip():
        db.update_paper(paper_id, {"fulltext": text[:400000]})
    return len(text)


@app.post("/api/papers/{paper_id}/pdf")
async def attach_pdf(paper_id: str, file: UploadFile = File(...)):
    _404(db.get_paper(paper_id, with_fulltext=False))
    return {"chars": _store_pdf(paper_id, await file.read())}


@app.post("/api/papers/{paper_id}/pdf/fetch")
async def fetch_pdf(paper_id: str):
    """Find and download a legal open-access PDF (paper's own link, OpenAlex OA locations, Europe PMC)."""
    p = _404(db.get_paper(paper_id, with_fulltext=False))
    urls = [p.get("pdf_url")] if p.get("pdf_url") else []
    if p.get("doi"):
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get(f"https://api.openalex.org/works/doi:{p['doi']}", params={"select": "best_oa_location,locations"})
                if r.status_code == 200:
                    w = r.json()
                    for loc in [w.get("best_oa_location")] + (w.get("locations") or []):
                        if loc and loc.get("pdf_url") and loc.get("is_oa"):
                            urls.append(loc["pdf_url"])
        except Exception:
            pass
    tried = []
    async with httpx.AsyncClient(timeout=40, follow_redirects=True,
                                 headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AFRA"}) as c:
        for u in dict.fromkeys(x for x in urls if x):
            try:
                r = await c.get(u)
                if r.status_code == 200 and r.content.startswith(b"%PDF"):
                    return {"chars": _store_pdf(paper_id, r.content), "url": u}
                tried.append(f"{u} ({r.status_code})")
            except Exception as e:
                tried.append(f"{u} ({type(e).__name__})")
    raise HTTPException(404, "No open-access PDF could be downloaded" + (f". Tried: {'; '.join(tried[:3])}" if tried else
                        " (no open-access copy known). Download it via your library/publisher and attach it."))


@app.get("/api/papers/{paper_id}/pdf")
def get_pdf(paper_id: str):
    path = db.pdf_path(paper_id)
    _404(path.exists(), "No PDF attached")
    return FileResponse(path, media_type="application/pdf", headers={"Content-Disposition": "inline"})


@app.get("/api/papers/{paper_id}/quotes")
def quotes(paper_id: str):
    return db.list_quotes(paper_id)


@app.post("/api/papers/{paper_id}/quotes")
def add_quote(paper_id: str, body: dict):
    p = _404(db.get_paper(paper_id, with_fulltext=False))
    return {"id": db.add_quote(paper_id, p["project_id"], body.get("page", ""), body.get("quote", ""),
                               body.get("comment", ""), body.get("tag", ""))}


@app.put("/api/quotes/{qid}")
def edit_quote(qid: str, body: dict):
    db.update_quote(qid, body)
    return {"ok": True}


@app.delete("/api/quotes/{qid}")
def remove_quote(qid: str):
    db.delete_quote(qid)
    return {"ok": True}


@app.get("/api/projects/{pid}/collections")
def collections(pid: str):
    return db.list_collections(pid)


@app.post("/api/projects/{pid}/collections")
def new_collection(pid: str, body: dict):
    return {"id": db.create_collection(pid, body.get("name") or "New collection", body.get("rule"))}


@app.put("/api/collections/{cid}")
def edit_collection(cid: str, body: dict):
    db.update_collection(cid, body.get("name"), body.get("rule"))
    return {"ok": True}


@app.delete("/api/collections/{cid}")
def remove_collection(cid: str):
    db.delete_collection(cid)
    return {"ok": True}


@app.post("/api/collections/{cid}/papers")
def collection_papers(cid: str, body: dict):
    db.set_membership(cid, body.get("paper_ids", []), bool(body.get("remove")))
    return {"ok": True}


@app.post("/api/papers/bulk")
def bulk(body: dict):
    ids, action, value = body.get("ids", []), body.get("action"), (body.get("value") or "").strip()
    if action == "delete" and ids:
        db.backup("before-bulk-delete")
    for i in ids:
        p = db.get_paper(i, with_fulltext=False)
        if not p:
            continue
        tags = [t.strip() for t in (p.get("tags") or "").split(",") if t.strip()]
        if action == "tag" and value and value not in tags:
            db.update_paper(i, {"tags": ", ".join(tags + [value])})
        elif action == "untag":
            db.update_paper(i, {"tags": ", ".join(t for t in tags if t != value)})
        elif action == "delete":
            db.delete_paper(i)
    return {"done": len(ids)}


@app.get("/api/projects/{pid}/duplicates")
def duplicates(pid: str):
    return [[{k: p.get(k) for k in ("id", "key", "title", "authors", "year", "venue", "doi", "has_pdf", "quotes", "tags", "source")}
             for p in g] for g in db.find_duplicates(pid)]


@app.post("/api/papers/merge")
def merge(body: dict):
    return db.merge_papers(body["master_id"], body["other_ids"])


@app.post("/api/projects/{pid}/bibtex/selected")
def bibtex_selected(pid: str, body: dict):
    ids = set(body.get("ids", []))
    return Response(to_bibtex([p for p in db.list_papers(pid) if p["id"] in ids]), media_type="application/x-bibtex",
                    headers={"Content-Disposition": 'attachment; filename="selection.bib"'})


@app.get("/api/projects/{pid}/ris")
def export_ris(pid: str):
    return Response(to_ris(db.list_papers(pid)), media_type="application/x-research-info-systems",
                    headers={"Content-Disposition": 'attachment; filename="library.ris"'})


@app.get("/api/projects/{pid}/bibliography")
def bib(pid: str, style: str = "apa"):
    return {"entries": bibliography(db.list_papers(pid), style)}


# ---------------------------------------------------------------- deep research
@app.post("/api/research/clarify")
async def clarify(body: dict):
    return {"questions": await research.clarify(body["query"], body.get("language", "English"))}


@app.post("/api/research/start")
async def start_research(body: dict):
    run = research.start(body["project_id"], body["query"], body.get("params", {}))
    return {"id": run.id}


@app.get("/api/research/{run_id}/events")
async def research_events(run_id: str):
    return StreamingResponse(research.event_stream(run_id), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/projects/{pid}/runs")
def runs(pid: str):
    live = [{"id": r.id, "query": r.query, "status": r.status, "created": r.created}
            for r in research.RUNS.values() if r.project_id == pid and r.status == "running"]
    live_ids = {r["id"] for r in live}
    return live + [r for r in db.list_runs(pid) if r["id"] not in live_ids]


@app.get("/api/research/{run_id}")
def get_run(run_id: str):
    if run_id in research.RUNS:
        return research.RUNS[run_id].snapshot()
    return _404(db.get_run(run_id))


@app.delete("/api/research/{run_id}")
def delete_run(run_id: str):
    research.RUNS.pop(run_id, None)
    db.delete_run(run_id)
    return {"ok": True}


@app.post("/api/research/{run_id}/add-sources")
def add_run_sources(run_id: str):
    run = get_run(run_id)
    added = [db.add_paper(run["project_id"], s, tags="deep-research") for s in run["sources"]]
    return {"added": len(added)}


# ---------------------------------------------------------------- documents
@app.get("/api/projects/{pid}/documents")
def documents(pid: str, kind: str | None = None):
    return db.list_documents(pid, kind)


@app.post("/api/projects/{pid}/documents")
def create_document(pid: str, body: dict):
    return db.create_document(pid, body.get("title") or "Untitled", body.get("kind", "doc"),
                              body.get("content", ""), body.get("meta"))


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: str):
    return _404(db.get_document(doc_id))


@app.put("/api/documents/{doc_id}")
def update_document(doc_id: str, body: dict):
    return _404(db.update_document(doc_id, body.get("title"), body.get("content"), body.get("meta")))


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: str):
    db.delete_document(doc_id)
    return {"ok": True}


# ---------------------------------------------------------------- paper workflow
def _paper(doc_id):
    doc = _404(db.get_document(doc_id))
    return doc, doc["meta"]


@app.post("/api/paper/{doc_id}/style-guide")
async def paper_style_guide(doc_id: str, body: dict):
    doc, paper = _paper(doc_id)
    return _stream(writer.p_style_guide(paper, body.get("samples", "")), writer.system_prompt(paper), task="paper-style-guide")


@app.post("/api/paper/{doc_id}/literature")
async def paper_literature(doc_id: str):
    doc, paper = _paper(doc_id)
    lib = writer.library_context(doc["project_id"], query=paper.get("research_question", "") + " " + paper.get("title", ""))
    return _stream(writer.p_literature(paper, lib), writer.system_prompt(paper), task="paper-literature")


@app.post("/api/paper/{doc_id}/outline")
async def paper_outline(doc_id: str, body: dict | None = None):
    doc, paper = _paper(doc_id)
    if (body or {}).get("revise"):
        data = await complete_json(writer.p_revise_outline(paper), writer.system_prompt(paper), task="paper-outline")
    else:
        lib = writer.library_context(doc["project_id"], chars=200)
        data = await complete_json(writer.p_outline(paper, lib), writer.system_prompt(paper), task="paper-outline")
    sections = data.get("sections", data) if isinstance(data, dict) else data
    return {"sections": sections}


@app.post("/api/paper/{doc_id}/review")
async def paper_review(doc_id: str, body: dict):
    doc, paper = _paper(doc_id)
    target = body.get("target", "outline")
    data = await complete_json(writer.p_review(paper, target), writer.system_prompt(paper), task=f"review-{target}", role="review")
    data["total"] = writer.review_total(data)
    data["passed"] = data["total"] >= writer.GATE
    return data


@app.post("/api/paper/{doc_id}/section/{idx}")
async def paper_section(doc_id: str, idx: int, body: dict):
    doc, paper = _paper(doc_id)
    if body.get("current"):
        paper["sections"][idx]["content"] = body["current"]
    # the library block is identical for every section -> cacheable prefix (Claude prompt caching)
    lib = writer.library_context(doc["project_id"], query=paper.get("research_question", "") + " " + paper.get("title", ""),
                                 max_items=40, chars=500)
    stable, task = writer.p_section(paper, idx, lib, body.get("instruction", ""))
    return _stream(task, writer.system_prompt(paper) + "\n\n" + stable, task="paper-section", cache_system=True)


@app.post("/api/paper/{doc_id}/abstract")
async def paper_abstract(doc_id: str):
    doc, paper = _paper(doc_id)
    return await complete_json(writer.p_abstract(paper), writer.system_prompt(paper), task="paper-abstract")


@app.post("/api/quality")
def quality_ep(body: dict):
    keys = set(db.library_map(body["project_id"]).keys())
    return writer.quality_check(body.get("text", ""), keys, body.get("target_words"))


@app.post("/api/similarity")
def similarity(body: dict):
    return writer.similarity(body.get("text", ""), db.list_papers(body["project_id"], with_fulltext=True))


# ---------------------------------------------------------------- tools
@app.post("/api/tools/run")
async def run_tool(body: dict):
    tool = body["tool"]
    if tool not in writer.TOOLS:
        raise HTTPException(400, "Unknown tool")
    return _stream(writer.p_tool(tool, body.get("text", ""), body.get("extra", ""), body.get("language", "")),
                   "You are an expert academic writing assistant.", task=f"tool-{tool}")


@app.post("/api/tools/ask-library")
async def ask_library(body: dict):
    lib = writer.library_context(body["project_id"], query=body["question"], max_items=15, chars=1500)
    return _stream(writer.p_ask_library(body["question"], lib), "You are a meticulous research assistant.", task="ask-library")


@app.post("/api/tools/suggest-citations")
async def suggest_citations(body: dict):
    lib = writer.library_context(body["project_id"], query=body["text"], max_items=30, chars=500)
    return await complete_json(writer.p_suggest_citations(body["text"], lib), fast=True, task="suggest-citations")


# ---------------------------------------------------------------- export
@app.post("/api/export/docx")
def export_docx(body: dict):
    library = db.library_map(body["project_id"])
    data, info = build_docx(body.get("markdown", ""), body.get("meta", {}), library,
                            body.get("template", "apa7"), body.get("style"), body.get("options", {}))
    name = re.sub(r"[^\w\- ]+", "", (body.get("meta") or {}).get("title") or "document").strip()[:80] or "document"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}.docx",
                             "X-Cited": str(info["cited"]), "X-Missing": quote(",".join(info["missing"]))})


# ---------------------------------------------------------------- frontend
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
