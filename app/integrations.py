"""Reference-manager sync.

Zotero
- Desktop (no key): Zotero 7's local API at localhost:23119 lets you read the library when the Zotero app is running
  (Zotero → Settings → Advanced → "Allow other applications on this computer to communicate with Zotero").
- Web API (read + write): user ID + API key from zotero.org/settings/keys. AFRA can import items and
  push library papers into Zotero (tagged "AFRA").

Mendeley / JabRef / EndNote / Zotero Better BibTeX: linked files
- Auto-export: the project library is rewritten to a .bib or .ris file a few seconds after any change.
  Point Mendeley (File → Import), JabRef or EndNote at that file.
- Import file: a .bib/.ris file exported by Mendeley (or auto-exported by Zotero Better BibTeX) can be
  re-imported any time; duplicates are skipped.
"""
import re
from pathlib import Path

import httpx

from . import db
from .citations import parse_bibtex, to_bibtex, to_ris
from .config import get_settings
from .search import split_name

LOCAL = "http://127.0.0.1:23119/api/users/0"
ZTYPE = {"journalArticle": "article", "book": "book", "bookSection": "chapter", "conferencePaper": "conference",
         "thesis": "thesis", "report": "report", "webpage": "webpage", "preprint": "preprint", "blogPost": "webpage"}
VENUE_FIELD = {"journalArticle": "publicationTitle", "conferencePaper": "proceedingsTitle", "bookSection": "bookTitle",
               "webpage": "websiteTitle", "preprint": "repository", "thesis": "university", "report": "institution"}


def _base(mode):
    s = get_settings()
    if mode == "local":
        return LOCAL, {}
    if not s.get("zotero_user_id") or not s.get("zotero_api_key"):
        raise ValueError("Enter your Zotero user ID and API key in Settings (zotero.org/settings/keys)")
    return f"https://api.zotero.org/users/{s['zotero_user_id']}", {"Zotero-API-Key": s["zotero_api_key"], "Zotero-API-Version": "3"}


async def _get_all(url, headers, params):
    out, start = [], 0
    async with httpx.AsyncClient(timeout=30) as c:
        while True:
            r = await c.get(url, headers=headers, params={**params, "limit": 100, "start": start, "format": "json"})
            if r.status_code == 403:
                raise ValueError("Zotero refused access: check the API key permissions (read/write library)")
            r.raise_for_status()
            batch = r.json()
            out += batch
            total = int(r.headers.get("Total-Results", len(out)))
            start += 100
            if not batch or start >= total:
                return out


async def collections(mode="local") -> list[dict]:
    base, h = _base(mode)
    try:
        items = await _get_all(base + "/collections", h, {})
    except httpx.ConnectError:
        raise ValueError("Zotero desktop is not reachable. Open Zotero, enable Settings → Advanced → "
                         "“Allow other applications on this computer to communicate with Zotero”, or use the Web API mode.")
    return [{"key": i["key"], "name": i["data"]["name"], "items": i.get("meta", {}).get("numItems")} for i in items]


def zotero_to_paper(it: dict) -> dict | None:
    d = it.get("data", it)
    t = d.get("itemType")
    if t in ("attachment", "note", "annotation"):
        return None
    authors = []
    for c in d.get("creators", []):
        if c.get("creatorType") not in ("author", "editor", None):
            continue
        if c.get("lastName"):
            authors.append({"given": c.get("firstName", ""), "family": c["lastName"]})
        elif c.get("name"):
            authors.append({"literal": c["name"], "family": c["name"], "given": ""})
    year = re.search(r"\d{4}", d.get("date", "") or "")
    venue = d.get(VENUE_FIELD.get(t, "publicationTitle")) or d.get("publicationTitle") or d.get("publisher") or ""
    return {"title": d.get("title", ""), "authors": authors, "year": int(year.group(0)) if year else None,
            "venue": venue, "volume": d.get("volume", ""), "issue": d.get("issue", ""), "pages": (d.get("pages") or "").replace("-", "–"),
            "doi": d.get("DOI", ""), "url": d.get("url", ""), "abstract": d.get("abstractNote", ""), "publisher": d.get("publisher", ""),
            "type": ZTYPE.get(t, "article"), "zotero_key": d.get("key", ""), "source": "zotero"}


async def import_zotero(project_id: str, mode="local", collection: str = "") -> dict:
    base, h = _base(mode)
    url = base + (f"/collections/{collection}/items/top" if collection else "/items/top")
    try:
        items = await _get_all(url, h, {})
    except httpx.ConnectError:
        raise ValueError("Zotero desktop is not reachable. Open Zotero and enable “Allow other applications on this "
                         "computer to communicate with Zotero” (Settings → Advanced).")
    added = skipped = 0
    for it in items:
        p = zotero_to_paper(it)
        if not p or not p["title"]:
            continue
        before = db.find_existing(project_id, p)
        stored = db.add_paper(project_id, p, tags="zotero")
        if before:
            skipped += 1
            if not before.get("zotero_key"):
                db.update_paper(before["id"], {"zotero_key": p["zotero_key"]})
        else:
            added += 1
    return {"added": added, "already_in_library": skipped, "read": len(items)}


def paper_to_zotero(p: dict, collection: str = "") -> dict:
    t = {v: k for k, v in ZTYPE.items() if k != "blogPost"}.get(p.get("type", "article"), "journalArticle")
    item = {"itemType": t, "title": p.get("title", ""), "date": str(p.get("year") or ""), "url": p.get("url", ""),
            "abstractNote": p.get("abstract", "")[:10000], "tags": [{"tag": "AFRA"}],
            "creators": [({"creatorType": "author", "name": a["literal"]} if a.get("literal") else
                          {"creatorType": "author", "firstName": a.get("given", ""), "lastName": a.get("family", "")})
                         for a in p.get("authors") or []]}
    if t in VENUE_FIELD and p.get("venue"):
        item[VENUE_FIELD[t]] = p["venue"]
    if t in ("journalArticle", "conferencePaper", "preprint") and p.get("doi"):
        item["DOI"] = p["doi"]
    if t in ("journalArticle", "conferencePaper", "bookSection") and p.get("pages"):
        item["pages"] = p["pages"].replace("–", "-")
    if t in ("journalArticle", "conferencePaper", "book") and p.get("volume"):
        item["volume"] = p["volume"]
    if t == "journalArticle" and p.get("issue"):
        item["issue"] = p["issue"]
    if t in ("book", "bookSection") and p.get("publisher"):
        item["publisher"] = p["publisher"]
    if collection:
        item["collections"] = [collection]
    return item


async def export_zotero(project_id: str, collection: str = "") -> dict:
    base, h = _base("web")
    papers = [p for p in db.list_papers(project_id) if not p.get("zotero_key")]
    sent = failed = 0
    errors = []
    async with httpx.AsyncClient(timeout=60) as c:
        for i in range(0, len(papers), 50):
            chunk = papers[i:i + 50]
            r = await c.post(base + "/items", headers={**h, "Content-Type": "application/json"},
                             json=[paper_to_zotero(p, collection) for p in chunk])
            if r.status_code == 403:
                raise ValueError("Zotero refused writing: give the API key “Allow write access”")
            r.raise_for_status()
            res = r.json()
            for idx, obj in (res.get("successful") or {}).items():
                db.update_paper(chunk[int(idx)]["id"], {"zotero_key": obj.get("key", "")})
                sent += 1
            for idx, obj in (res.get("failed") or {}).items():
                failed += 1
                errors.append(f"{chunk[int(idx)]['key']}: {obj.get('message', '')[:120]}")
    return {"sent": sent, "failed": failed, "errors": errors[:10], "already_synced": len(db.list_papers(project_id)) - len(papers)}


# ---------------------------------------------------------------- linked files
def file_settings(project_id: str) -> dict:
    return db.cache_get("filesync:" + project_id, max_age=100 * 365 * 86400) or {"export_path": "", "import_path": ""}


def save_file_settings(project_id: str, cfg: dict) -> dict:
    cfg = {"export_path": (cfg.get("export_path") or "").strip().strip('"'), "import_path": (cfg.get("import_path") or "").strip().strip('"')}
    for k in ("export_path", "import_path"):
        if cfg[k] and Path(cfg[k]).suffix.lower() not in (".bib", ".ris"):
            raise ValueError(f"{k.replace('_', ' ')} must end in .bib or .ris")
    db.cache_set("filesync:" + project_id, cfg)
    return cfg


def auto_export(project_id: str):
    path = file_settings(project_id).get("export_path")
    if not path:
        return None
    papers = db.list_papers(project_id)
    text = to_ris(papers) if path.lower().endswith(".ris") else to_bibtex(papers)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)  # atomic: other apps never see a half-written file
    return {"exported": len(papers), "path": str(p)}


def parse_ris(text: str) -> list[dict]:
    rev = {"JOUR": "article", "BOOK": "book", "CHAP": "chapter", "CPAPER": "conference", "CONF": "conference",
           "THES": "thesis", "RPRT": "report", "ELEC": "webpage", "UNPB": "preprint"}
    out, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9])  - ?(.*)$", line)
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
        if tag == "TY":
            cur = {"type": rev.get(val, "article"), "authors": [], "source": "ris"}
        elif cur is None:
            continue
        elif tag == "ER":
            if cur.get("title"):
                out.append(cur)
            cur = None
        elif tag in ("AU", "A1"):
            cur["authors"].append(split_name(val))
        elif tag in ("TI", "T1"):
            cur["title"] = val
        elif tag in ("T2", "JO", "JF", "BT"):
            cur.setdefault("venue", val)
        elif tag in ("PY", "Y1", "DA"):
            y = re.search(r"\d{4}", val)
            if y:
                cur["year"] = int(y.group(0))
        elif tag == "VL":
            cur["volume"] = val
        elif tag == "IS":
            cur["issue"] = val
        elif tag == "SP":
            cur["pages"] = val
        elif tag == "EP":
            cur["pages"] = f"{cur.get('pages', '')}–{val}"
        elif tag == "DO":
            cur["doi"] = val
        elif tag == "UR":
            cur["url"] = val
        elif tag in ("AB", "N2"):
            cur["abstract"] = val
        elif tag == "PB":
            cur["publisher"] = val
        elif tag == "ID":
            cur["key"] = val
    return out


def import_file(project_id: str) -> dict:
    path = file_settings(project_id).get("import_path")
    if not path:
        raise ValueError("Set an import file path first")
    p = Path(path)
    if not p.exists():
        raise ValueError(f"File not found: {p}")
    text = p.read_text(encoding="utf-8", errors="ignore")
    items = parse_ris(text) if p.suffix.lower() == ".ris" else parse_bibtex(text)
    before = len(db.list_papers(project_id))
    for it in items:
        db.add_paper(project_id, it, tags="file-import")
    return {"read": len(items), "added": len(db.list_papers(project_id)) - before}
