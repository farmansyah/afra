"""Submission checks.

Reference checker (no AI tokens): every reference is verified against OpenAlex / Crossref. It confirms the DOI
exists, that the title, year and first author match, that the paper is not retracted, and it finds
missing DOIs. Fixes come straight from the registry record.

Journal-fit checker: manuscript statistics (no AI) versus the journal's requirements (extracted once from pasted
author guidelines by the fast model, then cached), journal metrics, the journal's recent articles
closest to your topic (worth citing), and an optional scope-fit opinion (1 small fast call).
"""
import asyncio
import difflib
import hashlib
import json
import re

from . import db, quality
from .llm import complete_json
from .search import STOP, _client, _crossref_to_paper, _norm_doi, _openalex_to_paper, OA_WORK_FIELDS


def _n(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (s or "").lower())).strip()


def _sim(a, b):
    return difflib.SequenceMatcher(None, _n(a), _n(b)).ratio()


def _fam(p):
    a = (p.get("authors") or [{}])[0]
    return _n(a.get("family") or a.get("literal") or "")


def _compare(p, rec):
    issues, fix = [], {}
    if rec.get("title") and _sim(p.get("title"), rec["title"]) < 0.85:
        issues.append(f"Title differs from the registry: “{rec['title']}”")
        fix["title"] = rec["title"]
    if rec.get("year") and p.get("year") and abs(int(p["year"]) - int(rec["year"])) > 1:
        issues.append(f"Year {p['year']} — registry says {rec['year']}")
        fix["year"] = rec["year"]
    elif rec.get("year") and not p.get("year"):
        fix["year"] = rec["year"]
    fams = [_n(a.get("family") or a.get("literal") or "") for a in rec.get("authors") or []]
    if fams and _fam(p) and _fam(p) not in fams and not any(_fam(p) in f or f in _fam(p) for f in fams if f):
        issues.append(f"First author “{(p.get('authors') or [{}])[0].get('family', '')}” not among the registry's authors")
        fix["authors"] = rec["authors"]
    for f in ("venue", "volume", "issue", "pages"):
        if rec.get(f) and not p.get(f):
            fix[f] = rec[f]
    return issues, fix


async def check_references(papers: list[dict]) -> list[dict]:
    results = {p["key"]: {"key": p["key"], "id": p.get("id"), "title": p.get("title"), "status": "ok", "issues": [], "fix": {}}
               for p in papers}
    by_doi = {_norm_doi(p["doi"]).lower(): p for p in papers if p.get("doi")}
    found = {}
    async with _client() as c:
        dois = list(by_doi)
        for i in range(0, len(dois), 50):
            chunk = "|".join(dois[i:i + 50])
            try:
                r = await c.get("https://api.openalex.org/works", params={"filter": f"doi:{chunk}", "per-page": 50, "select": OA_WORK_FIELDS})
                r.raise_for_status()
                for w in r.json().get("results", []):
                    rec = _openalex_to_paper(w)
                    found[rec["doi"].lower()] = rec
            except Exception:
                pass

        async def crossref_doi(doi):
            try:
                r = await c.get(f"https://api.crossref.org/works/{doi}")
                return _crossref_to_paper(r.json()["message"]) if r.status_code == 200 else None
            except Exception:
                return "error"

        async def crossref_find(p):
            q = f"{p.get('title', '')} {_fam(p)}"
            try:
                r = await c.get("https://api.crossref.org/works", params={"query.bibliographic": q, "rows": 3})
                for m in r.json().get("message", {}).get("items", []):
                    rec = _crossref_to_paper(m)
                    if _sim(rec["title"], p.get("title")) >= 0.92:
                        return rec
            except Exception:
                pass
            return None

        missing = [d for d in by_doi if d not in found]
        recs = await asyncio.gather(*(crossref_doi(d) for d in missing))
        for d, rec in zip(missing, recs):
            if rec == "error":
                results[by_doi[d]["key"]]["issues"].append("Could not reach Crossref to verify this DOI (try again later)")
                results[by_doi[d]["key"]]["status"] = "warn"
            elif rec:
                found[d] = rec
            else:
                res = results[by_doi[d]["key"]]
                res["status"] = "error"
                res["issues"].append(f"DOI {by_doi[d]['doi']} does not exist in Crossref or OpenAlex: wrong or invented DOI")
        no_doi = [p for p in papers if not p.get("doi") and p.get("type") not in ("webpage",)]
        hits = await asyncio.gather(*(crossref_find(p) for p in no_doi))
    for p in papers:
        res = results[p["key"]]
        if p.get("type") == "webpage":
            res["status"] = "info"
            res["issues"].append("Web source: check that the page is still online and prefer a peer-reviewed source if one exists")
            continue
        rec = found.get(_norm_doi(p.get("doi") or "").lower())
        if rec:
            issues, fix = _compare(p, rec)
            res["issues"] += issues
            res["fix"] = fix
            if rec.get("retracted"):
                res["status"] = "error"
                res["issues"].insert(0, "RETRACTED: remove this reference or explain why you cite it")
            elif issues:
                res["status"] = "fix"
            elif fix:
                res["status"] = "ok"
                res["issues"].append("Verified. Missing details can be filled in from the registry")
    for p, rec in zip(no_doi, hits):
        res = results[p["key"]]
        if rec:
            res["status"] = "fix"
            res["issues"].append(f"No DOI. Found it: {rec['doi']}")
            res["fix"] = {"doi": rec["doi"], **{f: rec[f] for f in ("venue", "volume", "issue", "pages", "year") if rec.get(f) and not p.get(f)}}
        else:
            res["status"] = "warn"
            res["issues"].append("No DOI and no exact match in Crossref: check that this reference is real and complete")
    order = {"error": 0, "fix": 1, "warn": 2, "info": 3, "ok": 4}
    return sorted(results.values(), key=lambda r: order[r["status"]])


# ---------------------------------------------------------------- journal fit
def manuscript_stats(md: str) -> dict:
    body = re.split(r"\n#+\s*(references|bibliography|daftar pustaka|works cited)\s*\n", md, flags=re.I)
    main_text, refs = body[0], (body[-1] if len(body) > 1 else "")
    abstract = ""
    m = re.search(r"#+\s*(abstract|abstrak)\s*\n(.*?)(?=\n#|\Z)", main_text, re.S | re.I)
    if m:
        abstract = re.split(r"\n\s*\**(?:keywords?|kata kunci)\**\s*[:—-]", m.group(2), flags=re.I)[0].strip()
    kw = re.search(r"(keywords?|kata kunci)\s*[:—-]\s*(.+)", md, re.I)
    keywords = [k.strip() for k in re.split(r"[;,·]", kw.group(2)) if k.strip()] if kw else []
    cited = set(re.findall(r"@([A-Za-z][\w:\-]*[A-Za-z0-9])", main_text))
    ref_lines = [l for l in refs.splitlines() if len(l.strip()) > 30]
    words = len(re.findall(r"\b\w+\b", re.sub(r"\[[^\]]*@[^\]]*\]", "", main_text)))
    return {
        "words": words, "abstract_words": len(re.findall(r"\b\w+\b", abstract)), "abstract_structured":
            bool(re.search(r"\b(background|objectives?|methods?|results?|conclusions?)\s*:", abstract, re.I)),
        "keywords": len(keywords), "references": max(len(cited), len(ref_lines)),
        "figures": len(set(re.findall(r"\b(?:figure|fig\.|gambar)\s*(\d+)", main_text, re.I))),
        "tables": len(set(re.findall(r"\b(?:table|tabel)\s*(\d+)", main_text, re.I))),
        "headings": re.findall(r"^#{1,3}\s+(.+)$", main_text, re.M)[:30],
        "title": (re.search(r"^#\s+(.+)$", md, re.M) or [None, ""])[1], "abstract": abstract[:2500],
    }


async def find_journals(q: str) -> list[dict]:
    async with _client() as c:
        r = await c.get("https://api.openalex.org/sources", params={"search": q, "filter": "type:journal", "per-page": 8,
                                                                    "select": "id,display_name,issn_l,host_organization_name,works_count"})
        r.raise_for_status()
    return [{"id": s["id"], "name": s["display_name"], "issn": s.get("issn_l"), "publisher": s.get("host_organization_name"),
             "works": s.get("works_count")} for s in r.json().get("results", [])]


def _terms(stats):
    text = f"{stats['title']} {stats['title']} {stats['abstract'][:1200]}"
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z\-]{3,}", text) if w.lower() not in STOP]
    freq = {}
    for w in words:
        freq[w.lower()] = freq.get(w.lower(), 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda x: -x[1])][:6]


async def _extract_requirements(src: dict, guidelines: str) -> dict:
    return await complete_json(
        f"Author guidelines of the journal {src['display_name']}:\n<guidelines>\n{guidelines[:30000]}\n</guidelines>\n\n"
        "Extract the submission requirements for a regular research article. Use null when not stated.\n"
        'Return JSON: {"word_limit":null,"abstract_word_limit":null,"abstract_structured":null,"keywords_min":null,'
        '"keywords_max":null,"max_references":null,"max_figures_tables":null,"reference_style":"","required_sections":[""],'
        '"other_requirements":[""]}', fast=True, task="journal-guidelines", max_tokens=1200)


async def journal_fit(source_id: str, md: str, guidelines: str = "", ai: bool = True) -> dict:
    stats = manuscript_stats(md)
    srcs = await quality.openalex_sources([source_id])
    src = srcs.get(source_id) or next(iter(srcs.values()), None)
    if not src:
        raise ValueError("Journal not found in OpenAlex")
    qual = await quality.quality_for(src) or {}
    sid = src["id"].rsplit("/", 1)[-1]

    # requirements: from pasted guidelines (fast model, once) or a previous extraction for this journal
    req = db.cache_get("journal-req:" + sid, max_age=10 * 365 * 86400) or {}
    if guidelines.strip():
        h = hashlib.sha1(guidelines.encode()).hexdigest()
        if req.get("_hash") != h:
            try:
                req = await _extract_requirements(src, guidelines)
            except Exception:
                req = {"other_requirements": ["Could not read the pasted guidelines with the AI model; limits not checked."]}
            req["_hash"] = h
            db.cache_set("journal-req:" + sid, req)

    def chk(label, yours, limit, kind="max"):
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = None
        if not limit:
            return {"item": label, "yours": yours, "required": "not stated", "ok": None}
        ok = yours <= limit if kind == "max" else yours >= limit
        return {"item": label, "yours": yours, "required": f"{'≤' if kind == 'max' else '≥'} {limit}", "ok": ok}

    checks = [chk("Main text words", stats["words"], req.get("word_limit")),
              chk("Abstract words", stats["abstract_words"], req.get("abstract_word_limit")),
              chk("Keywords (min)", stats["keywords"], req.get("keywords_min"), "min"),
              chk("Keywords (max)", stats["keywords"], req.get("keywords_max")),
              chk("References", stats["references"], req.get("max_references")),
              chk("Figures + tables", stats["figures"] + stats["tables"], req.get("max_figures_tables"))]
    if req.get("abstract_structured") is not None:
        checks.append({"item": "Structured abstract", "yours": "yes" if stats["abstract_structured"] else "no",
                       "required": "yes" if req["abstract_structured"] else "no",
                       "ok": stats["abstract_structured"] == bool(req["abstract_structured"])})
    heads = " ".join(stats["headings"]).lower()
    for sec in req.get("required_sections") or []:
        if sec:
            present = any(w in heads for w in _n(sec).split()[:2])
            checks.append({"item": f"Section: {sec}", "yours": "present" if present else "missing", "required": "required", "ok": present})

    # the journal's own recent papers closest to your topic: show scope fit and give candidates to cite
    terms = _terms(stats)
    recent, matching = [], None
    if terms:
        import datetime
        since = datetime.date.today().year - 5
        async with _client() as c:
            r = await c.get("https://api.openalex.org/works", params={
                "search": " ".join(terms), "per-page": 8, "select": OA_WORK_FIELDS,
                "filter": f"primary_location.source.id:{sid},from_publication_date:{since}-01-01"})
            if r.status_code == 200:
                matching = r.json()["meta"]["count"]
                recent = [_openalex_to_paper(w) for w in r.json().get("results", [])]
    fit = None
    if ai and (stats["title"] or stats["abstract"]):
        topics = [t["display_name"] for t in (src.get("topics") or [])][:3]
        try:
            fit = await complete_json(
                f"Journal: {src['display_name']} (main topics: {', '.join(topics)}). Recent articles in it on similar terms "
                f"({matching} in 5 years):\n" + "\n".join(f"- {p['title']}" for p in recent) +
                f"\n\nManuscript title: {stats['title']}\nAbstract: {stats['abstract'][:1500]}\n\n"
                "Judge the scope fit of this manuscript for this journal and how to improve its chances (framing, which "
                'recent journal articles to engage with). Return JSON: {"fit":"high|medium|low","reasons":[""],"suggestions":[""]}',
                fast=True, task="journal-fit", max_tokens=700)
        except Exception:
            fit = None
    return {"journal": {"id": src["id"], "name": src["display_name"], "publisher": src.get("host_organization_name"),
                        "homepage": src.get("homepage_url"), "topics": [t["display_name"] for t in (src.get("topics") or [])][:3],
                        **qual},
            "stats": stats, "requirements": {k: v for k, v in req.items() if not k.startswith("_")}, "checks": checks,
            "terms": terms, "matching_recent": matching, "recent": recent, "fit": fit}
