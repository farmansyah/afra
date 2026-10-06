"""Search connectors. Every connector returns "paper" dicts with a common shape:

{title, authors:[{given,family}], year, venue, volume, issue, pages, doi, url, pdf_url,
 abstract, citations, type, source, publisher, content}
"""
import asyncio
import time
import html
import io
import re
import unicodedata
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from .config import get_settings

UA = "AFRA/1.0 (academic research assistant)"
SOURCES = {
    "openalex": "OpenAlex (250M+ scholarly works)",
    "semanticscholar": "Semantic Scholar (needs free API key)",
    "arxiv": "arXiv (preprints)",
    "crossref": "Crossref (DOI metadata)",
    "europepmc": "Europe PMC (PubMed + life sciences, no key)",
    "tavily": "Web - Tavily (API key)",
    "ddg": "Web - DuckDuckGo (no key, best effort)",
}


# ---------------------------------------------------------------- helpers
def split_name(name: str) -> dict:
    name = (name or "").strip()
    if not name:
        return {"given": "", "family": ""}
    if "," in name:
        family, given = name.split(",", 1)
        return {"given": given.strip(), "family": family.strip()}
    parts = name.split()
    if len(parts) == 1:
        return {"given": "", "family": parts[0]}
    # keep particles (van, de, von, al, bin...) with the family name
    particles = {"van", "von", "de", "der", "den", "da", "di", "del", "la", "le", "al", "el", "bin", "binti"}
    i = len(parts) - 1
    while i > 1 and parts[i - 1].lower() in particles:
        i -= 1
    return {"given": " ".join(parts[:i]), "family": " ".join(parts[i:])}


def strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).replace("\n", " ").strip()


def clean_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def ascii_slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


STOP = {"a", "an", "the", "on", "of", "in", "for", "and", "to", "with", "towards", "toward", "from", "by",
        "at", "is", "are", "using", "via", "how", "what", "why", "do", "does"}


def make_key(paper: dict) -> str:
    authors = paper.get("authors") or []
    fam = ascii_slug(authors[0].get("family") or authors[0].get("literal", "")) if authors else ""
    if not fam:
        fam = ascii_slug(urlparse(paper.get("url") or "").netloc.replace("www.", "").split(".")[0]) or "anon"
    year = str(paper.get("year") or "nd")
    word = ""
    for w in re.findall(r"[A-Za-z0-9]+", paper.get("title") or ""):
        if w.lower() not in STOP:
            word = ascii_slug(w)
            break
    return f"{fam}{year}{word}"[:40]


# Polite, self-healing HTTP: limit parallel requests per site and retry "too many requests" automatically,
# so big searches (Discover, Deep Research) don't fail on free API rate limits.
_HOST_LIMITS = {"api.openalex.org": 4, "api.crossref.org": 3, "export.arxiv.org": 1, "www.ebi.ac.uk": 4}
_host_sems: dict[str, asyncio.Semaphore] = {}


class _PoliteTransport(httpx.AsyncHTTPTransport):
    async def handle_async_request(self, request):
        host = request.url.host
        sem = _host_sems.setdefault(host, asyncio.Semaphore(_HOST_LIMITS.get(host, 6)))
        for attempt in range(3):
            async with sem:
                resp = await super().handle_async_request(request)
            if resp.status_code not in (429, 502, 503) or attempt == 2:
                return resp
            retry_after = resp.headers.get("retry-after", "")
            await resp.aclose()
            delay = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 1.0 * 2 ** attempt
            await asyncio.sleep(min(delay, 5))
        return resp


def _client(**kw):
    return httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10), headers={"User-Agent": UA},
                             follow_redirects=True, transport=_PoliteTransport(retries=1), **kw)


def _norm_doi(doi: str | None) -> str:
    if not doi:
        return ""
    return re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", doi.strip(), flags=re.I)


# ---------------------------------------------------------------- OpenAlex
def _inverted_abstract(ix) -> str:
    if not ix:
        return ""
    pos = {}
    for word, places in ix.items():
        for p in places:
            pos[p] = word
    return " ".join(pos[i] for i in sorted(pos))


def _openalex_to_paper(w: dict) -> dict:
    loc = w.get("primary_location") or {}
    src = loc.get("source") or {}
    bib = w.get("biblio") or {}
    pages = ""
    if bib.get("first_page"):
        pages = bib["first_page"] + (f"–{bib['last_page']}" if bib.get("last_page") and bib["last_page"] != bib["first_page"] else "")
    oa = w.get("open_access") or {}
    return {
        "title": clean_ws(w.get("title") or w.get("display_name") or ""),
        "authors": [split_name((a.get("author") or {}).get("display_name", "")) for a in w.get("authorships", [])],
        "year": w.get("publication_year"),
        "venue": src.get("display_name") or "",
        "volume": bib.get("volume") or "",
        "issue": bib.get("issue") or "",
        "pages": pages,
        "doi": _norm_doi(w.get("doi")),
        "url": w.get("doi") or loc.get("landing_page_url") or w.get("id"),
        "pdf_url": oa.get("oa_url") or "",
        "abstract": _inverted_abstract(w.get("abstract_inverted_index")),
        "citations": w.get("cited_by_count"),
        "type": {"book": "book", "book-chapter": "chapter", "preprint": "preprint", "dissertation": "thesis",
                 "report": "report"}.get(w.get("type"), "article"),
        "publisher": src.get("host_organization_name") or "",
        "openalex_id": w.get("id") or "",
        "source_id": src.get("id") or "",
        "source_type": src.get("type") or "",
        "issn": src.get("issn") or ([src["issn_l"]] if src.get("issn_l") else []),
        "retracted": bool(w.get("is_retracted")),
        "is_oa": bool(oa.get("is_oa")),
        "source": "openalex",
    }


async def search_openalex(q: str, limit=10, year_from=None, year_to=None, sort=None) -> list[dict]:
    params = {"search": q, "per-page": min(int(limit), 200)}
    if sort:
        params["sort"] = sort
    filters = []
    if year_from:
        filters.append(f"from_publication_date:{year_from}-01-01")
    if year_to:
        filters.append(f"to_publication_date:{year_to}-12-31")
    if filters:
        params["filter"] = ",".join(filters)
    email = get_settings().get("contact_email")
    if email:
        params["mailto"] = email
    async with _client() as c:
        r = await c.get("https://api.openalex.org/works", params=params)
        r.raise_for_status()
        return [_openalex_to_paper(w) for w in r.json().get("results", [])]


# ---------------------------------------------------------------- Semantic Scholar
# Several Semantic Scholar keys can be pasted (comma / space / new line separated). Each key is used at
# most once per ~1.05 s; requests rotate to whichever key is free first, so 2 keys give ~2 requests/s.
_S2_STATE: dict[str, dict] = {}


def _s2_keys() -> list[str]:
    raw = get_settings().get("semantic_scholar_api_key") or ""
    return [k for k in re.split(r"[\s,;]+", raw) if k] or [""]


async def _s2_get(c, url, params):
    keys = _s2_keys()
    r = None
    for attempt in range(2 * len(keys) + 2):
        key = min(keys, key=lambda k: _S2_STATE.setdefault(k, {"lock": asyncio.Lock(), "next": 0.0})["next"])
        st = _S2_STATE[key]
        async with st["lock"]:
            wait = st["next"] - time.time()
            if wait > 0:
                await asyncio.sleep(wait)
            r = await c.get(url, params=params, headers={"x-api-key": key} if key else {})
            st["next"] = time.time() + 1.05
        if r.status_code != 429:
            return r
        st["next"] = time.time() + 3 + attempt  # cool this key down, the next attempt rotates to another one
    return r


async def search_semanticscholar(q: str, limit=10, year_from=None, year_to=None) -> list[dict]:
    from . import db
    ck = f"s2:{q}:{limit}:{year_from}:{year_to}"
    hit = db.cache_get(ck, max_age=7 * 86400)
    if hit is not None:
        return hit
    out = await _s2_fetch(q, limit, year_from, year_to)
    db.cache_set(ck, out)
    return out


async def _s2_fetch(q: str, limit=10, year_from=None, year_to=None) -> list[dict]:
    params = {"query": q, "limit": limit,
              "fields": "title,authors,year,abstract,venue,externalIds,citationCount,openAccessPdf,url,journal,publicationTypes"}
    if year_from or year_to:
        params["year"] = f"{year_from or ''}-{year_to or ''}"
    async with _client() as c:
        r = await _s2_get(c, "https://api.semanticscholar.org/graph/v1/paper/search", params)
        r.raise_for_status()
    out = []
    for p in r.json().get("data", []) or []:
        j = p.get("journal") or {}
        ext = p.get("externalIds") or {}
        doi = ext.get("DOI") or ""
        types = p.get("publicationTypes") or []
        out.append({
            "title": clean_ws(p.get("title") or ""),
            "authors": [split_name(a.get("name", "")) for a in p.get("authors", [])],
            "year": p.get("year"),
            "venue": j.get("name") or p.get("venue") or "",
            "volume": (j.get("volume") or "").strip(),
            "issue": "",
            "pages": clean_ws(j.get("pages") or "").replace("-", "–"),
            "doi": doi,
            "url": f"https://doi.org/{doi}" if doi else p.get("url"),
            "pdf_url": (p.get("openAccessPdf") or {}).get("url") or "",
            "abstract": p.get("abstract") or "",
            "citations": p.get("citationCount"),
            "type": "conference" if "Conference" in types else "article",
            "arxiv": ext.get("ArXiv") or "",
            "source": "semanticscholar",
        })
    return out


# ---------------------------------------------------------------- arXiv
_ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def _arxiv_entries(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    out = []
    for e in root.findall("a:entry", _ATOM):
        aid = (e.findtext("a:id", "", _ATOM) or "").rsplit("/abs/", 1)[-1]
        aid_base = re.sub(r"v\d+$", "", aid)
        doi = e.findtext("arxiv:doi", "", _ATOM) or ""
        jref = e.findtext("arxiv:journal_ref", "", _ATOM) or ""
        out.append({
            "title": clean_ws(e.findtext("a:title", "", _ATOM)),
            "authors": [split_name(a.findtext("a:name", "", _ATOM)) for a in e.findall("a:author", _ATOM)],
            "year": int((e.findtext("a:published", "", _ATOM) or "0000")[:4]) or None,
            "venue": jref or "arXiv",
            "volume": "", "issue": "", "pages": "",
            "doi": doi,
            "url": f"https://arxiv.org/abs/{aid_base}",
            "pdf_url": f"https://arxiv.org/pdf/{aid_base}",
            "abstract": clean_ws(e.findtext("a:summary", "", _ATOM)),
            "citations": None,
            "type": "article" if jref else "preprint",
            "arxiv": aid_base,
            "source": "arxiv",
        })
    return out


async def search_arxiv(q: str, limit=10, year_from=None, year_to=None) -> list[dict]:
    terms = [t for t in re.findall(r"\w+", q) if t.lower() not in STOP][:8]
    query = " AND ".join(f"all:{t}" for t in terms) or f"all:{q}"
    if year_from or year_to:
        query += f" AND submittedDate:[{year_from or 1990}01010000 TO {year_to or 2100}12312359]"
    async with _client() as c:
        r = await c.get("https://export.arxiv.org/api/query",
                        params={"search_query": query, "max_results": limit, "sortBy": "relevance"})
        r.raise_for_status()
    return _arxiv_entries(r.text)


# ---------------------------------------------------------------- Crossref
def _crossref_to_paper(m: dict) -> dict:
    issued = (m.get("issued") or m.get("published") or {}).get("date-parts", [[None]])
    year = issued[0][0] if issued and issued[0] else None
    authors = []
    for a in m.get("author", []) or []:
        if a.get("family"):
            authors.append({"given": a.get("given", ""), "family": a["family"]})
        elif a.get("name"):
            authors.append({"literal": a["name"], "given": "", "family": a["name"]})
    ctype = m.get("type", "")
    ptype = {"journal-article": "article", "book": "book", "monograph": "book", "book-chapter": "chapter",
             "proceedings-article": "conference", "posted-content": "preprint", "dissertation": "thesis",
             "report": "report"}.get(ctype, "article")
    doi = m.get("DOI", "")
    return {
        "title": clean_ws(" ".join(m.get("title") or [])),
        "authors": authors,
        "year": year,
        "venue": " ".join(m.get("container-title") or []),
        "volume": m.get("volume", ""),
        "issue": m.get("issue", ""),
        "pages": (m.get("page") or "").replace("-", "–"),
        "doi": doi,
        "url": f"https://doi.org/{doi}" if doi else m.get("URL", ""),
        "pdf_url": "",
        "abstract": strip_tags(m.get("abstract", "")),
        "citations": m.get("is-referenced-by-count"),
        "type": ptype,
        "publisher": m.get("publisher", ""),
        "edition": m.get("edition-number", ""),
        "issn": m.get("ISSN") or [],
        "source": "crossref",
    }


async def search_crossref(q: str, limit=10, year_from=None, year_to=None) -> list[dict]:
    params = {"query.bibliographic": q, "rows": limit}
    filters = []
    if year_from:
        filters.append(f"from-pub-date:{year_from}")
    if year_to:
        filters.append(f"until-pub-date:{year_to}")
    if filters:
        params["filter"] = ",".join(filters)
    email = get_settings().get("contact_email")
    if email:
        params["mailto"] = email
    async with _client() as c:
        r = await c.get("https://api.crossref.org/works", params=params)
        r.raise_for_status()
    return [_crossref_to_paper(m) for m in r.json().get("message", {}).get("items", [])]


async def lookup_identifier(ident: str) -> dict | None:
    """Resolve a DOI, DOI URL or arXiv id/URL into a paper."""
    ident = ident.strip()
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([\w.\-/]+?)(?:v\d+)?(?:\.pdf)?$", ident) or \
        re.match(r"^(?:arxiv:)?(\d{4}\.\d{4,5})(?:v\d+)?$", ident, re.I)
    async with _client() as c:
        if m:
            r = await c.get("https://export.arxiv.org/api/query", params={"id_list": m.group(1)})
            r.raise_for_status()
            entries = _arxiv_entries(r.text)
            return entries[0] if entries else None
        doi = _norm_doi(ident)
        r = await c.get(f"https://api.crossref.org/works/{doi}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return _crossref_to_paper(r.json()["message"])


# ---------------------------------------------------------------- Europe PMC (PubMed, PMC, preprints)
async def search_europepmc(q: str, limit=10, year_from=None, year_to=None) -> list[dict]:
    query = q
    if year_from or year_to:
        query = f"({q}) AND (PUB_YEAR:[{year_from or 1900} TO {year_to or 2100}])"
    async with _client() as c:
        r = await c.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                        params={"query": query, "format": "json", "resultType": "core", "pageSize": min(int(limit), 100)})
        r.raise_for_status()
    out = []
    for it in r.json().get("resultList", {}).get("result", []):
        ji = it.get("journalInfo") or {}
        j = ji.get("journal") or {}
        authors = [{"given": a.get("firstName", ""), "family": a.get("lastName") or a.get("fullName", "")}
                   for a in (it.get("authorList") or {}).get("author", []) if a.get("lastName") or a.get("fullName")]
        urls = (it.get("fullTextUrlList") or {}).get("fullTextUrl", [])
        pdf = next((u["url"] for u in urls if u.get("documentStyle") == "pdf" and u.get("availabilityCode") in ("OA", "F")), "")
        doi = it.get("doi", "")
        pmcid = it.get("pmcid", "")
        out.append({
            "title": clean_ws(strip_tags(it.get("title", ""))).rstrip("."),
            "authors": authors,
            "year": int(it["pubYear"]) if str(it.get("pubYear", "")).isdigit() else None,
            "venue": j.get("title") or ("Preprint" if it.get("source") == "PPR" else ""),
            "volume": ji.get("volume", ""), "issue": ji.get("issue", ""),
            "pages": (it.get("pageInfo") or "").replace("-", "–"),
            "doi": doi,
            "url": f"https://doi.org/{doi}" if doi else f"https://europepmc.org/article/{it.get('source')}/{it.get('id')}",
            "pdf_url": pdf or (f"https://europepmc.org/articles/{pmcid}/pdf" if pmcid and it.get("isOpenAccess") == "Y" else ""),
            "abstract": clean_ws(strip_tags(it.get("abstractText", ""))),
            "citations": int(it.get("citedByCount") or 0),
            "type": "preprint" if it.get("source") == "PPR" else ("review" if False else "article"),
            "issn": [x for x in (j.get("issn"), j.get("essn")) if x],
            "is_oa": it.get("isOpenAccess") == "Y",
            "source": "europepmc",
        })
    return out


# ---------------------------------------------------------------- Web
def _web_paper(title, url, content, source) -> dict:
    host = urlparse(url).netloc.replace("www.", "")
    return {"title": clean_ws(title), "authors": [], "year": None, "venue": host, "volume": "", "issue": "",
            "pages": "", "doi": "", "url": url, "pdf_url": "", "abstract": clean_ws(content)[:2000],
            "content": content, "citations": None, "type": "webpage", "source": source}


async def search_tavily(q: str, limit=8, year_from=None, year_to=None) -> list[dict]:
    key = get_settings().get("tavily_api_key")
    if not key:
        raise RuntimeError("Tavily API key not set (Settings)")
    async with _client() as c:
        r = await c.post("https://api.tavily.com/search", headers={"Authorization": f"Bearer {key}"},
                         json={"query": q, "max_results": limit, "search_depth": "advanced", "api_key": key})
        r.raise_for_status()
    out = []
    for it in r.json().get("results", []):
        p = _web_paper(it.get("title", ""), it.get("url", ""), it.get("content", ""), "tavily")
        if it.get("published_date"):
            m = re.match(r"(\d{4})", it["published_date"])
            p["year"] = int(m.group(1)) if m else None
        out.append(p)
    return out


async def search_ddg(q: str, limit=8, year_from=None, year_to=None) -> list[dict]:
    async with _client() as c:
        r = await c.post("https://html.duckduckgo.com/html/", data={"q": q},
                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        r.raise_for_status()
    out = []
    blocks = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
                        r'(?:class="result__snippet"[^>]*>(.*?)</a>)?', r.text, re.S)
    for href, title, snippet in blocks[:limit]:
        m = re.search(r"uddg=([^&]+)", href)
        if m:
            from urllib.parse import unquote
            href = unquote(m.group(1))
        if "duckduckgo.com" in href:
            continue
        out.append(_web_paper(strip_tags(title), href, strip_tags(snippet), "ddg"))
    return out


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "nav", "footer", "header", "noscript", "svg", "form", "aside"}

    def __init__(self):
        super().__init__()
        self.parts, self.depth, self.title, self._in_title = [], 0, "", False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.depth += 1
        if tag == "title":
            self._in_title = True
        if tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section", "article"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth:
            self.depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.depth:
            self.parts.append(data)


def pdf_bytes_to_text(data: bytes, max_pages=60) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    texts = []
    for page in reader.pages[:max_pages]:
        try:
            texts.append(page.extract_text() or "")
        except Exception:
            pass
    return "\n".join(texts)


async def fetch_page(url: str, max_chars=20000) -> dict:
    async with _client() as c:
        r = await c.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AFRA"})
        r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        text = pdf_bytes_to_text(r.content)
        title = ""
    else:
        p = _TextExtractor()
        p.feed(r.text)
        text = "".join(p.parts)
        title = clean_ws(p.title)
    text = re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()
    return {"url": url, "title": title, "text": text[:max_chars]}


# ---------------------------------------------------------------- unified search
CONNECTORS = {
    "openalex": search_openalex,
    "semanticscholar": search_semanticscholar,
    "arxiv": search_arxiv,
    "crossref": search_crossref,
    "europepmc": search_europepmc,
    "tavily": search_tavily,
    "ddg": search_ddg,
}


def _dedupe_key(p: dict) -> str:
    if p.get("doi"):
        return "doi:" + p["doi"].lower()
    if p.get("type") == "webpage":
        return "url:" + (p.get("url") or "")
    return "t:" + ascii_slug(p.get("title", ""))[:80]


async def search_all(q: str, sources: list[str], limit=8, year_from=None, year_to=None):
    """Run connectors concurrently; return (deduped results, errors)."""
    sources = [s for s in sources if s in CONNECTORS] or ["openalex"]

    async def run(name):
        try:
            # a slow or blocked site must never hold up the whole search
            return name, await asyncio.wait_for(CONNECTORS[name](q, limit, year_from, year_to), 25), None
        except asyncio.TimeoutError:
            return name, [], f"{SOURCES.get(name, name)}: no answer within 25 s (skipped this time)"
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            hint = ((" (rate-limited: add a free API key in Settings)" if name == "semanticscholar" else
                     " (busy right now: try again in a minute)") if code == 429 else "")
            return name, [], f"{SOURCES.get(name, name)}: HTTP {code}{hint}"
        except Exception as e:
            return name, [], f"{SOURCES.get(name, name)}: {type(e).__name__}: {str(e)[:160]}"

    results = await asyncio.gather(*(run(s) for s in sources))
    seen, merged, errors = {}, [], []
    for name, papers, err in results:
        if err:
            errors.append(err)
        for p in papers:
            if not p.get("title"):
                continue
            k = _dedupe_key(p)
            if k in seen:
                existing = seen[k]
                for field in ("abstract", "pdf_url", "doi", "citations", "venue", "volume", "issue", "pages"):
                    if not existing.get(field) and p.get(field):
                        existing[field] = p[field]
                continue
            seen[k] = p
            merged.append(p)
    return merged, errors


# ---------------------------------------------------------------- snowballing (OpenAlex, no AI tokens)
OA_WORK_FIELDS = ("id,doi,title,display_name,authorships,publication_year,primary_location,biblio,open_access,"
                  "abstract_inverted_index,cited_by_count,type,is_retracted")


async def _openalex_works_by_ids(c, ids: list[str]) -> list[dict]:
    out = []
    for i in range(0, len(ids), 50):
        chunk = "|".join(x.rsplit("/", 1)[-1] for x in ids[i:i + 50])
        r = await c.get("https://api.openalex.org/works", params={"filter": f"openalex:{chunk}", "per-page": 50,
                                                                  "select": OA_WORK_FIELDS})
        r.raise_for_status()
        out += r.json().get("results", [])
    return out


async def snowball(ident: str, mode: str, limit: int = 30) -> dict:
    """mode: references (backward), citing (forward), related."""
    ident = ident.strip()
    wid = ident if re.match(r"^(https://openalex.org/)?W\d+$", ident) else f"doi:{_norm_doi(ident)}"
    async with _client() as c:
        r = await c.get(f"https://api.openalex.org/works/{wid.rsplit('/', 1)[-1] if wid.startswith('http') else wid}",
                        params={"select": "id,display_name,referenced_works,related_works,cited_by_count"})
        if r.status_code == 404:
            raise ValueError("Paper not found in OpenAlex (needs a DOI)")
        r.raise_for_status()
        work = r.json()
        if mode == "citing":
            r = await c.get("https://api.openalex.org/works", params={
                "filter": f"cites:{work['id'].rsplit('/', 1)[-1]}", "sort": "cited_by_count:desc",
                "per-page": limit, "select": OA_WORK_FIELDS})
            r.raise_for_status()
            works = r.json().get("results", [])
        else:
            ids = work.get("referenced_works" if mode == "references" else "related_works", [])[:limit]
            works = await _openalex_works_by_ids(c, ids) if ids else []
    papers = [_openalex_to_paper(w) for w in works]
    papers.sort(key=lambda p: p.get("citations") or 0, reverse=True)
    return {"seed": work.get("display_name"), "total_citing": work.get("cited_by_count"), "results": papers}


# ---------------------------------------------------------------- journal finder (OpenAlex, no AI tokens)
async def journals_for_topic(q: str, years: int = 5, limit: int = 25) -> list[dict]:
    import datetime
    since = datetime.date.today().year - years
    async with _client() as c:
        r = await c.get("https://api.openalex.org/works", params={
            "search": q, "filter": f"type:article,from_publication_date:{since}-01-01,primary_location.source.type:journal",
            "group_by": "primary_location.source.id"})
        r.raise_for_status()
    groups = [g for g in r.json().get("group_by", []) if g.get("key") and "openalex.org/S" in g["key"]]
    return [{"source_id": g["key"], "name": g.get("key_display_name"), "matching_articles": g.get("count")}
            for g in groups[:limit]]
