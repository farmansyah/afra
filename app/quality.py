"""Journal quality: SJR quartiles (SCImago file imported by the user) + OpenAlex journal statistics.

No AI tokens are used. Results are cached in the local database for 30 days.

- Official quartile: the user downloads SCImago's free "Journal Rankings" file (scimagojr.com -> Download data)
  once a year and imports it in Settings. Matched by ISSN, then by journal title.
- Fallback: an *estimated* quartile from OpenAlex — the journal's 2-year mean citedness percentile among
  established journals (>100 works) in its main field. Clearly labelled "est.".
"""
import asyncio
import csv
import io
import json
import re

from . import db
from .search import _client

QORDER = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}


def norm_issn(s: str) -> str:
    s = re.sub(r"[^0-9Xx]", "", s or "").upper()
    return s if len(s) == 8 else ""


def norm_title(t: str) -> str:
    t = (t or "").lower().replace("&", "and")
    t = re.sub(r"^the\s+", "", t)
    return re.sub(r"[^a-z0-9]", "", t)[:150]


# ---------------------------------------------------------------- SCImago import
def import_sjr(raw: bytes) -> int:
    text = raw.decode("utf-8-sig", "ignore")
    reader = csv.reader(io.StringIO(text), delimiter=";")
    header = [h.strip().lower() for h in next(reader)]

    def col(name):
        return header.index(name) if name in header else None

    i_title, i_issn, i_sjr, i_q, i_h, i_cat, i_oa = (col("title"), col("issn"), col("sjr"), col("sjr best quartile"),
                                                     col("h index"), col("categories"), col("open access"))
    if i_title is None or i_q is None:
        raise ValueError("This is not a SCImago Journal Rank file (expected columns 'Title' and 'SJR Best Quartile').")
    rows, issn_rows = [], []
    for n, r in enumerate(reader, 1):
        if len(r) <= max(i_title, i_q):
            continue
        q = r[i_q].strip().upper()
        try:
            sjr = float(r[i_sjr].replace(",", ".")) if i_sjr is not None and r[i_sjr].strip() else None
        except ValueError:
            sjr = None
        try:
            h = int(r[i_h]) if i_h is not None and r[i_h].strip() else None
        except ValueError:
            h = None
        rows.append((n, norm_title(r[i_title]), r[i_title].strip(), sjr, q if q in QORDER else None, h,
                     r[i_cat].strip() if i_cat is not None else "",
                     1 if i_oa is not None and r[i_oa].strip().lower() == "yes" else 0))
        if i_issn is not None:
            issn_rows += [(norm_issn(x), n) for x in r[i_issn].split(",") if norm_issn(x)]
    if not rows:
        raise ValueError("No journals found in the file.")
    db.backup("before-sjr-import")
    with db.conn() as c:
        c.execute("DELETE FROM sjr")
        c.execute("DELETE FROM sjr_issn")
        c.executemany("INSERT INTO sjr VALUES (?,?,?,?,?,?,?,?)", rows)
        c.executemany("INSERT INTO sjr_issn VALUES (?,?)", issn_rows)
    return len(rows)


def sjr_count() -> int:
    with db.conn() as c:
        return c.execute("SELECT COUNT(*) FROM sjr").fetchone()[0]


def sjr_lookup(issns, title) -> dict | None:
    with db.conn() as c:
        for i in issns or []:
            r = c.execute("SELECT s.* FROM sjr_issn x JOIN sjr s ON s.id=x.sjr_id WHERE x.issn=?", (norm_issn(i),)).fetchone()
            if r:
                return dict(r)
        if title:
            r = c.execute("SELECT * FROM sjr WHERE tkey=?", (norm_title(title),)).fetchone()
            if r:
                return dict(r)
    return None


# ---------------------------------------------------------------- OpenAlex journal stats
SRC_FIELDS = "id,display_name,issn_l,issn,summary_stats,is_in_doaj,is_oa,apc_usd,topics,type,host_organization_name,works_count,homepage_url"
_sem = asyncio.Semaphore(6)


async def openalex_sources(ids=(), issns=()) -> dict:
    """Returns {source_id: source} and {issn: source} merged into one dict."""
    out, need_ids, need_issn = {}, [], []
    for i in {x.rsplit("/", 1)[-1] for x in ids if x}:
        hit = db.cache_get("src:" + i)
        if hit:
            out[hit["id"]] = hit
        else:
            need_ids.append(i)
    for i in {norm_issn(x) for x in issns if norm_issn(x)}:
        hit = db.cache_get("issn:" + i)
        if hit:
            out["issn:" + i] = hit
        else:
            need_issn.append(f"{i[:4]}-{i[4:]}")
    async with _client() as c:
        async def fetch(filt):
            async with _sem:
                r = await c.get("https://api.openalex.org/sources", params={"filter": filt, "per-page": 50, "select": SRC_FIELDS})
                r.raise_for_status()
                return r.json().get("results", [])
        jobs = [fetch("openalex:" + "|".join(need_ids[i:i + 50])) for i in range(0, len(need_ids), 50)]
        jobs += [fetch("issn:" + "|".join(need_issn[i:i + 50])) for i in range(0, len(need_issn), 50)]
        for res in await asyncio.gather(*jobs, return_exceptions=True):
            if isinstance(res, Exception):
                continue
            for src in res:
                src.pop("topics_extra", None)
                src["topics"] = (src.get("topics") or [])[:3]
                db.cache_set("src:" + src["id"].rsplit("/", 1)[-1], src)
                out[src["id"]] = src
                for i in src.get("issn") or []:
                    db.cache_set("issn:" + norm_issn(i), src)
                    out["issn:" + norm_issn(i)] = src
    return out


async def estimate_quartile(src: dict) -> str | None:
    if (src.get("type") or "journal") != "journal":
        return None
    topics = src.get("topics") or []
    stats = src.get("summary_stats") or {}
    x = stats.get("2yr_mean_citedness")
    if not topics or not x:  # no citations in 2 years (new/discontinued journal): leave unranked
        return None
    field = topics[0]["field"]["id"].rsplit("/", 2)[-2:]  # ['fields', '22']
    fid = "/".join(field)
    key = f"estq:{fid}:{round(x, 2)}"
    hit = db.cache_get(key)
    if hit is not None:
        return hit or None
    base = f"type:journal,works_count:>100,topics.field.id:{fid}"
    async with _client() as c:
        async def count(extra=""):
            async with _sem:
                r = await c.get("https://api.openalex.org/sources", params={"filter": base + extra, "per-page": 1, "select": "id"})
                r.raise_for_status()
                return r.json()["meta"]["count"]
        total = db.cache_get("estq-total:" + fid)
        if total is None:
            total = await count()
            db.cache_set("estq-total:" + fid, total)
        above = await count(f",summary_stats.2yr_mean_citedness:>{x}")
    pct = above / total if total else 1
    q = "Q1" if pct < .25 else "Q2" if pct < .5 else "Q3" if pct < .75 else "Q4"
    db.cache_set(key, q)
    return q


async def quality_for(src: dict | None, issns=(), venue="", estimate=True) -> dict | None:
    q = {}
    sj = sjr_lookup(list(issns) + list((src or {}).get("issn") or []), venue or (src or {}).get("display_name"))
    if sj:
        q.update(quartile=sj["quartile"], q_source="SJR", sjr=sj["sjr"], h_index=sj["h_index"],
                 categories=sj["categories"][:200])
    if src:
        st = src.get("summary_stats") or {}
        q.update(impact2y=round(st["2yr_mean_citedness"], 2) if st.get("2yr_mean_citedness") is not None else None,
                 doaj=bool(src.get("is_in_doaj")), oa_journal=bool(src.get("is_oa")), apc_usd=src.get("apc_usd"),
                 publisher=src.get("host_organization_name") or "", homepage=src.get("homepage_url") or "")
        q.setdefault("h_index", st.get("h_index"))
        if not q.get("h_index"):
            q["h_index"] = st.get("h_index")
        if not q.get("quartile") and estimate:
            try:
                eq = await estimate_quartile(src)
            except Exception:
                eq = None
            if eq:
                q.update(quartile=eq, q_source="est.")
    return q or None


async def enrich(papers: list[dict], estimate=True) -> list[dict]:
    """Attach paper['quality'] to journal papers (in place)."""
    targets = [p for p in papers if p.get("type") not in ("webpage", "preprint") and (p.get("venue") or p.get("source_id"))]
    if not targets:
        return papers
    try:
        srcs = await openalex_sources([p.get("source_id") for p in targets],
                                      [i for p in targets if not p.get("source_id") for i in (p.get("issn") or [])])
    except Exception:
        srcs = {}

    async def one(p):
        src = srcs.get(p.get("source_id")) or next(
            (srcs.get("issn:" + norm_issn(i)) for i in p.get("issn") or [] if srcs.get("issn:" + norm_issn(i))), None)
        p["quality"] = await quality_for(src, p.get("issn") or [], p.get("venue"), estimate)
    await asyncio.gather(*(one(p) for p in targets))
    return papers


def passes(p: dict, max_quartile: str | None, allow_unranked=False, allow_preprints=False) -> bool:
    if p.get("retracted"):
        return False
    if not max_quartile:
        return True
    if p.get("type") in ("webpage", "preprint"):
        return allow_preprints
    q = (p.get("quality") or {}).get("quartile")
    if not q:
        return allow_unranked
    return QORDER[q] <= QORDER[max_quartile]


async def journal_table(groups: list[dict]) -> list[dict]:
    srcs = await openalex_sources([g["source_id"] for g in groups])
    out = []

    async def one(g):
        src = srcs.get(g["source_id"])
        q = await quality_for(src, venue=g["name"]) or {}
        out.append({**g, **q})
    await asyncio.gather(*(one(g) for g in groups))
    out.sort(key=lambda r: (-(r.get("matching_articles") or 0)))
    return out
