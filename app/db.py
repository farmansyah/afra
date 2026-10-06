"""SQLite storage: projects, library papers, documents/manuscripts and research runs."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

from .config import DATA_DIR
from .search import make_key

DB_PATH = DATA_DIR / "research.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT DEFAULT '',
  citation_style TEXT DEFAULT 'apa', created REAL);
CREATE TABLE IF NOT EXISTS papers (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, key TEXT NOT NULL, data TEXT NOT NULL,
  tags TEXT DEFAULT '', notes TEXT DEFAULT '', fulltext TEXT DEFAULT '', created REAL);
CREATE INDEX IF NOT EXISTS papers_project ON papers(project_id);
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT DEFAULT 'doc', title TEXT,
  content TEXT DEFAULT '', meta TEXT DEFAULT '{}', created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, query TEXT, params TEXT, status TEXT,
  tree TEXT DEFAULT '[]', learnings TEXT DEFAULT '[]', sources TEXT DEFAULT '[]',
  report TEXT DEFAULT '', created REAL);
CREATE TABLE IF NOT EXISTS usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, task TEXT, model TEXT, input INTEGER, output INTEGER,
  cached INTEGER DEFAULT 0, cost REAL, estimated INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS usage_ts ON usage(ts);
CREATE TABLE IF NOT EXISTS sjr (
  id INTEGER PRIMARY KEY, tkey TEXT, title TEXT, sjr REAL, quartile TEXT, h_index INTEGER, categories TEXT, oa INTEGER);
CREATE INDEX IF NOT EXISTS sjr_tkey ON sjr(tkey);
CREATE TABLE IF NOT EXISTS sjr_issn (issn TEXT, sjr_id INTEGER);
CREATE INDEX IF NOT EXISTS sjr_issn_i ON sjr_issn(issn);
CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, data TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS collections (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, rule TEXT, created REAL);
CREATE TABLE IF NOT EXISTS paper_collections (paper_id TEXT, collection_id TEXT, PRIMARY KEY (paper_id, collection_id));
CREATE TABLE IF NOT EXISTS quotes (
  id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, project_id TEXT, page TEXT, quote TEXT, comment TEXT, tag TEXT, created REAL);
CREATE INDEX IF NOT EXISTS quotes_paper ON quotes(paper_id);
"""
BACKUP_DIR = DATA_DIR / "backups"
KEEP_BACKUPS = 20


DIRTY: set[str] = set()  # projects whose library changed (picked up by the linked-file auto-export)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA synchronous=FULL")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")  # crash-safe: a killed process never leaves a half-written DB
        c.executescript(SCHEMA)
        # runs left "running" by a previous server process were interrupted; their progress is kept
        c.execute("UPDATE runs SET status='interrupted' WHERE status='running'")
        if not c.execute("SELECT 1 FROM projects LIMIT 1").fetchone():
            c.execute("INSERT INTO projects VALUES (?,?,?,?,?)",
                      (new_id(), "My First Project", "Default project", "apa", time.time()))


# ---------------------------------------------------------------- projects
def list_projects():
    with conn() as c:
        rows = c.execute("""SELECT p.*, (SELECT COUNT(*) FROM papers WHERE project_id=p.id) AS n_papers,
                            (SELECT COUNT(*) FROM documents WHERE project_id=p.id) AS n_docs,
                            (SELECT COUNT(*) FROM runs WHERE project_id=p.id) AS n_runs
                            FROM projects p ORDER BY created""").fetchall()
        return [dict(r) for r in rows]


def create_project(name, description="", citation_style="apa"):
    pid = new_id()
    with conn() as c:
        c.execute("INSERT INTO projects VALUES (?,?,?,?,?)", (pid, name, description, citation_style, time.time()))
    return pid


def update_project(pid, **fields):
    allowed = {k: v for k, v in fields.items() if k in ("name", "description", "citation_style")}
    if not allowed:
        return
    with conn() as c:
        c.execute(f"UPDATE projects SET {', '.join(f'{k}=?' for k in allowed)} WHERE id=?", (*allowed.values(), pid))


def delete_project(pid):
    with conn() as c:
        for t in ("papers", "documents", "runs"):
            c.execute(f"DELETE FROM {t} WHERE project_id=?", (pid,))
        c.execute("DELETE FROM projects WHERE id=?", (pid,))


# ---------------------------------------------------------------- papers
PDF_DIR = DATA_DIR / "pdfs"


def pdf_path(paper_id):
    return PDF_DIR / f"{paper_id}.pdf"


def _paper_row(r, with_fulltext=False, cols=None, nquotes=None):
    d = json.loads(r["data"])
    d.update(id=r["id"], key=r["key"], tags=r["tags"], notes=r["notes"], project_id=r["project_id"],
             has_fulltext=bool(r["fulltext"]), has_pdf=pdf_path(r["id"]).exists(),
             collections=(cols or {}).get(r["id"], []), quotes=(nquotes or {}).get(r["id"], 0))
    if with_fulltext:
        d["fulltext"] = r["fulltext"]
    return d


def list_papers(pid, with_fulltext=False):
    with conn() as c:
        rows = c.execute("SELECT * FROM papers WHERE project_id=? ORDER BY created DESC", (pid,)).fetchall()
        cols = {}
        for x in c.execute("""SELECT pc.paper_id, pc.collection_id FROM paper_collections pc
                              JOIN collections k ON k.id=pc.collection_id WHERE k.project_id=?""", (pid,)):
            cols.setdefault(x[0], []).append(x[1])
        nq = {x[0]: x[1] for x in c.execute("SELECT paper_id, COUNT(*) FROM quotes WHERE project_id=? GROUP BY paper_id", (pid,))}
        return [_paper_row(r, with_fulltext, cols, nq) for r in rows]


def library_map(pid) -> dict:
    return {p["key"]: p for p in list_papers(pid)}


def get_paper(paper_id, with_fulltext=True):
    with conn() as c:
        r = c.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
        return _paper_row(r, with_fulltext) if r else None


def find_existing(pid, paper) -> dict | None:
    for p in list_papers(pid):
        if paper.get("doi") and p.get("doi") and p["doi"].lower() == paper["doi"].lower():
            return p
        if paper.get("type") == "webpage" and p.get("url") and p.get("url") == paper.get("url"):
            return p
        if (p.get("title") or "").strip().lower() == (paper.get("title") or "").strip().lower() and \
                str(p.get("year")) == str(paper.get("year")):
            return p
    return None


def unique_key(pid, base):
    with conn() as c:
        existing = {r["key"] for r in c.execute("SELECT key FROM papers WHERE project_id=?", (pid,))}
    if base not in existing:
        return base
    for suffix in "bcdefghijklmnopqrstuvwxyz":
        if base + suffix not in existing:
            return base + suffix
    return base + new_id()[:4]


STRIP = {"id", "key", "tags", "notes", "project_id", "has_fulltext", "fulltext", "content", "sid",
         "has_pdf", "collections", "quotes"}  # computed / stored in their own columns


def add_paper(pid, paper: dict, fulltext="", tags="") -> dict:
    """Add a paper (deduplicated). Returns the stored paper (existing one if duplicate)."""
    existing = find_existing(pid, paper)
    if existing:
        return existing
    key = unique_key(pid, paper.get("key") or make_key(paper))
    data = {k: v for k, v in paper.items() if k not in STRIP}
    fulltext = fulltext or paper.get("content") or ""
    pid_ = new_id()
    with conn() as c:
        c.execute("INSERT INTO papers VALUES (?,?,?,?,?,?,?,?)",
                  (pid_, pid, key, json.dumps(data), tags or paper.get("tags") or "", paper.get("notes") or "",
                   fulltext[:400000], time.time()))
    DIRTY.add(pid)
    return get_paper(pid_, with_fulltext=False)


def update_paper(paper_id, fields: dict):
    cur = get_paper(paper_id)
    if not cur:
        return None
    data = {k: v for k, v in cur.items() if k not in STRIP}
    for k, v in fields.items():
        if k not in STRIP:
            data[k] = v
    with conn() as c:
        c.execute("UPDATE papers SET data=?, tags=?, notes=?, key=? WHERE id=?",
                  (json.dumps(data), fields.get("tags", cur["tags"]), fields.get("notes", cur["notes"]),
                   fields.get("key", cur["key"]) or cur["key"], paper_id))
        if "fulltext" in fields:
            c.execute("UPDATE papers SET fulltext=? WHERE id=?", (fields["fulltext"], paper_id))
    DIRTY.add(cur["project_id"])
    return get_paper(paper_id, with_fulltext=False)


def delete_paper(paper_id):
    cur = get_paper(paper_id, with_fulltext=False)
    if cur:
        DIRTY.add(cur["project_id"])
    with conn() as c:
        c.execute("DELETE FROM papers WHERE id=?", (paper_id,))
        c.execute("DELETE FROM paper_collections WHERE paper_id=?", (paper_id,))
    # keep the PDF and quotes recoverable instead of destroying them
    if pdf_path(paper_id).exists():
        (PDF_DIR / "deleted").mkdir(parents=True, exist_ok=True)
        pdf_path(paper_id).replace(PDF_DIR / "deleted" / f"{paper_id}.pdf")


# ---------------------------------------------------------------- documents
def _doc_row(r):
    d = dict(r)
    d["meta"] = json.loads(d.get("meta") or "{}")
    return d


def list_documents(pid, kind=None):
    q = "SELECT id, project_id, kind, title, created, updated, substr(content,1,300) AS preview FROM documents WHERE project_id=?"
    args = [pid]
    if kind:
        q += " AND kind=?"
        args.append(kind)
    with conn() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY updated DESC", args).fetchall()]


def get_document(doc_id):
    with conn() as c:
        r = c.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        return _doc_row(r) if r else None


def create_document(pid, title, kind="doc", content="", meta=None):
    did = new_id()
    now = time.time()
    with conn() as c:
        c.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)",
                  (did, pid, kind, title, content, json.dumps(meta or {}), now, now))
    return get_document(did)


def update_document(doc_id, title=None, content=None, meta=None):
    cur = get_document(doc_id)
    if not cur:
        return None
    with conn() as c:
        c.execute("UPDATE documents SET title=?, content=?, meta=?, updated=? WHERE id=?",
                  (title if title is not None else cur["title"],
                   content if content is not None else cur["content"],
                   json.dumps(meta if meta is not None else cur["meta"]), time.time(), doc_id))
    return get_document(doc_id)


def delete_document(doc_id):
    with conn() as c:
        c.execute("DELETE FROM documents WHERE id=?", (doc_id,))


# ---------------------------------------------------------------- research runs
def save_run(run: dict):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (run["id"], run["project_id"], run["query"], json.dumps(run["params"]), run["status"],
                   json.dumps(run["tree"]), json.dumps(run["learnings"]), json.dumps(run["sources"]),
                   run.get("report", ""), run["created"]))


def list_runs(pid):
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, query, status, created FROM runs WHERE project_id=? ORDER BY created DESC", (pid,))]


def get_run(run_id):
    with conn() as c:
        r = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    for k in ("params", "tree", "learnings", "sources"):
        d[k] = json.loads(d[k] or "null")
    return d


def delete_run(run_id):
    with conn() as c:
        c.execute("DELETE FROM runs WHERE id=?", (run_id,))


# ---------------------------------------------------------------- backups
def backup(reason="auto") -> str:
    """Consistent online copy of the database (sqlite backup API); keeps the newest KEEP_BACKUPS."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    name = time.strftime("research-%Y%m%d-%H%M%S") + f"-{reason}.db"
    src = sqlite3.connect(DB_PATH, timeout=30)
    dst = sqlite3.connect(BACKUP_DIR / name)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    for old in sorted(BACKUP_DIR.glob("research-*.db"))[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)
    return name


def list_backups():
    if not BACKUP_DIR.exists():
        return []
    return [{"name": f.name, "size": f.stat().st_size, "ts": f.stat().st_mtime}
            for f in sorted(BACKUP_DIR.glob("research-*.db"), reverse=True)]


def last_backup_age() -> float:
    b = list_backups()
    return time.time() - b[0]["ts"] if b else float("inf")


# ---------------------------------------------------------------- usage log
def log_usage(task, model, input_tokens, output_tokens, cached=0, cost=None, estimated=False):
    with conn() as c:
        c.execute("INSERT INTO usage (ts, task, model, input, output, cached, cost, estimated) VALUES (?,?,?,?,?,?,?,?)",
                  (time.time(), task, model, int(input_tokens), int(output_tokens), int(cached or 0), cost, int(estimated)))


def usage_summary(since: float = 0):
    with conn() as c:
        tot = dict(c.execute("""SELECT COUNT(*) calls, COALESCE(SUM(input),0) input, COALESCE(SUM(output),0) output,
            COALESCE(SUM(cached),0) cached, COALESCE(SUM(cost),0) cost FROM usage WHERE ts>=?""", (since,)).fetchone())
        by = lambda col: [dict(r) for r in c.execute(f"""SELECT {col} k, COUNT(*) calls, SUM(input) input, SUM(output) output,
            SUM(cached) cached, COALESCE(SUM(cost),0) cost FROM usage WHERE ts>=? GROUP BY {col} ORDER BY SUM(input)+SUM(output) DESC""", (since,))]
        daily = [dict(r) for r in c.execute("""SELECT date(ts,'unixepoch','localtime') k, SUM(input) input, SUM(output) output,
            COALESCE(SUM(cost),0) cost FROM usage WHERE ts>=? GROUP BY k ORDER BY k""", (since,))]
        recent = [dict(r) for r in c.execute("SELECT * FROM usage WHERE ts>=? ORDER BY id DESC LIMIT 50", (since,))]
        return {"total": tot, "by_task": by("task"), "by_model": by("model"), "daily": daily, "recent": recent}


# ---------------------------------------------------------------- generic cache (API lookups)
def cache_get(key, max_age=30 * 86400):
    with conn() as c:
        r = c.execute("SELECT data, ts FROM cache WHERE key=?", (key,)).fetchone()
    if r and time.time() - r["ts"] < max_age:
        return json.loads(r["data"])
    return None


def cache_set(key, value):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (key, json.dumps(value), time.time()))


# ---------------------------------------------------------------- collections
def list_collections(pid):
    with conn() as c:
        rows = c.execute("SELECT * FROM collections WHERE project_id=? ORDER BY name COLLATE NOCASE", (pid,)).fetchall()
        return [{**dict(r), "rule": json.loads(r["rule"]) if r["rule"] else None} for r in rows]


def create_collection(pid, name, rule=None):
    cid = new_id()
    with conn() as c:
        c.execute("INSERT INTO collections VALUES (?,?,?,?,?)", (cid, pid, name, json.dumps(rule) if rule else None, time.time()))
    return cid


def update_collection(cid, name=None, rule=None):
    with conn() as c:
        if name is not None:
            c.execute("UPDATE collections SET name=? WHERE id=?", (name, cid))
        if rule is not None:
            c.execute("UPDATE collections SET rule=? WHERE id=?", (json.dumps(rule) if rule else None, cid))


def delete_collection(cid):
    """Removes the folder only; papers stay in the library."""
    with conn() as c:
        c.execute("DELETE FROM paper_collections WHERE collection_id=?", (cid,))
        c.execute("DELETE FROM collections WHERE id=?", (cid,))


def set_membership(cid, paper_ids, remove=False):
    with conn() as c:
        for p in paper_ids:
            if remove:
                c.execute("DELETE FROM paper_collections WHERE paper_id=? AND collection_id=?", (p, cid))
            else:
                c.execute("INSERT OR IGNORE INTO paper_collections VALUES (?,?)", (p, cid))


# ---------------------------------------------------------------- quotes & notes from PDFs
def list_quotes(paper_id):
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM quotes WHERE paper_id=? ORDER BY created", (paper_id,))]


def add_quote(paper_id, project_id, page="", quote="", comment="", tag=""):
    qid = new_id()
    with conn() as c:
        c.execute("INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?)", (qid, paper_id, project_id, page, quote, comment, tag, time.time()))
    return qid


def update_quote(qid, fields):
    allowed = {k: v for k, v in fields.items() if k in ("page", "quote", "comment", "tag")}
    if allowed:
        with conn() as c:
            c.execute(f"UPDATE quotes SET {', '.join(f'{k}=?' for k in allowed)} WHERE id=?", (*allowed.values(), qid))


def delete_quote(qid):
    with conn() as c:
        c.execute("DELETE FROM quotes WHERE id=?", (qid,))


# ---------------------------------------------------------------- duplicates
def _tnorm(t):
    import re
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def find_duplicates(pid):
    import difflib
    papers = list_papers(pid)
    groups, used = [], set()
    for i, a in enumerate(papers):
        if a["id"] in used:
            continue
        grp = [a]
        for b in papers[i + 1:]:
            if b["id"] in used:
                continue
            same_doi = a.get("doi") and b.get("doi") and a["doi"].lower() == b["doi"].lower()
            ta, tb = _tnorm(a.get("title")), _tnorm(b.get("title"))
            close_year = not a.get("year") or not b.get("year") or abs(int(a["year"]) - int(b["year"])) <= 1
            similar = ta and tb and close_year and difflib.SequenceMatcher(None, ta, tb).ratio() >= 0.93
            if same_doi or similar:
                grp.append(b)
                used.add(b["id"])
        if len(grp) > 1:
            used.add(a["id"])
            groups.append(grp)
    return groups


def merge_papers(master_id, other_ids):
    """Merge duplicates into master: fill missing fields, join tags/notes, move quotes, collections and PDF,
    and rewrite citation keys in every document of the project so nothing breaks."""
    import re
    master = get_paper(master_id)
    others = [get_paper(o) for o in other_ids if o != master_id]
    others = [o for o in others if o]
    if not master or not others:
        return {"merged": 0}
    backup("before-merge")
    fields = {}
    for o in others:
        for k, v in o.items():
            if k in STRIP or k in ("has_pdf", "collections", "quotes"):
                continue
            if v and not master.get(k) and k not in fields:
                fields[k] = v
    tags = ", ".join(dict.fromkeys(t.strip() for t in ",".join([master.get("tags") or ""] + [o.get("tags") or "" for o in others]).split(",") if t.strip()))
    notes = "\n\n".join(n for n in [master.get("notes")] + [o.get("notes") for o in others] if n)
    fields.update(tags=tags, notes=notes)
    if not master.get("fulltext"):
        ft = next((o.get("fulltext") for o in others if o.get("fulltext")), "")
        if ft:
            fields["fulltext"] = ft
    update_paper(master_id, fields)
    pid = master["project_id"]
    with conn() as c:
        for o in others:
            c.execute("UPDATE quotes SET paper_id=? WHERE paper_id=?", (master_id, o["id"]))
            c.execute("INSERT OR IGNORE INTO paper_collections SELECT ?, collection_id FROM paper_collections WHERE paper_id=?", (master_id, o["id"]))
    if not pdf_path(master_id).exists():
        src = next((pdf_path(o["id"]) for o in others if pdf_path(o["id"]).exists()), None)
        if src:
            src.replace(pdf_path(master_id))
    # rewrite @oldkey -> @masterkey in documents and manuscripts
    rewritten = 0
    for d in list_documents(pid):
        doc = get_document(d["id"])
        content, meta = doc["content"] or "", json.dumps(doc["meta"])
        new_c, new_m = content, meta
        for o in others:
            pat = re.compile(r"@" + re.escape(o["key"]) + r"(?![\w:\-]*[A-Za-z0-9])")
            new_c, new_m = pat.sub("@" + master["key"], new_c), pat.sub("@" + master["key"], new_m)
        if new_c != content or new_m != meta:
            update_document(d["id"], content=new_c, meta=json.loads(new_m))
            rewritten += 1
    for o in others:
        delete_paper(o["id"])
    return {"merged": len(others), "documents_updated": rewritten, "key": master["key"]}
