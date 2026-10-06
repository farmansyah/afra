"""Comprehensive source discovery and argument evidence finding.

Discover (title -> every relevant paper):
  1 fast-model call splits the title into concepts with synonyms (monetite = DCPA = dicalcium phosphate
  anhydrous ...). Then, with no further AI tokens: OpenAlex boolean queries (synonyms OR'ed per concept,
  concepts AND'ed, relaxed step by step), short facet queries on several databases, and one round of
  citation snowballing from the strongest seeds. Results are ranked by concept coverage, agreement across
  queries, citations and journal quality.

Evidence (argument -> papers that back or challenge it):
  1 fast call plans queries, searches run without AI, a lexical pre-rank keeps the best 24 abstracts, and
  1 fast call labels each as supports / partially / contradicts with a verbatim quote that is then verified.
"""
import asyncio
import math
import re

from . import quality
from .llm import complete_json
from .search import STOP, _dedupe_key, search_all, search_openalex, snowball

SYS = "You are an expert research librarian who builds precise scholarly search strategies."


def _norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (s or "").lower())).strip()


GENERIC = {"effect", "effects", "impact", "influence", "study", "studies", "analysis", "evaluation", "role", "based",
           "using", "development", "investigation", "properties", "time", "approach", "review", "novel", "case", "toward"}


def _fallback_concepts(text):
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", text)
             if w.lower() not in STOP and w.lower() not in GENERIC and len(w) > 3]
    seen, out = set(), []
    for w in words:
        if w.lower() not in seen:
            seen.add(w.lower())
            out.append({"name": w, "synonyms": [w]})
    return out[:6]


async def expand(title: str, extra: str = "") -> dict:
    prompt = (f"Research title / topic:\n<title>{title}</title>\n{('Context: ' + extra) if extra else ''}\n\n"
              "Build a literature search strategy.\n"
              "1) Split it into 3-6 core CONCEPTS (materials, interventions, properties, outcomes, methods, population). "
              "For each give the main term plus synonyms, abbreviations, chemical/technical names and spelling variants "
              "used in papers (e.g. monetite -> DCPA, dicalcium phosphate anhydrous, CaHPO4). Mark each concept essential "
              "true/false (false = nice to have).\n"
              "2) Write 8 short keyword queries (3-6 words) covering the topic from different angles, including broader "
              "background and closely related systems.\n"
              'Return JSON: {"concepts":[{"name":"","synonyms":[""],"essential":true}],"queries":[""]}')
    try:
        data = await complete_json(prompt, system=SYS, fast=True, task="discover-expand", max_tokens=1200)
        concepts = [c for c in data.get("concepts", []) if c.get("name")]
        for c in concepts:
            c["synonyms"] = list(dict.fromkeys([c["name"]] + [x for x in c.get("synonyms", []) if x]))[:8]
        return {"concepts": concepts or _fallback_concepts(title), "queries": data.get("queries", [])[:10], "ai": True}
    except Exception:
        return {"concepts": _fallback_concepts(title), "queries": [], "ai": False}


def _bool_query(concepts):
    def term(t):
        t = t.replace('"', "")
        return f'"{t}"' if re.search(r"[\s\-/]", t) else t  # phrases and hyphenated terms must stay together
    return " AND ".join("(" + " OR ".join(term(t) for t in c["synonyms"]) + ")" for c in concepts)


def _coverage(p, concepts):
    text = _norm(" ".join([p.get("title") or "", p.get("abstract") or ""]))
    hit = [c["name"] for c in concepts if any(_norm(t) and _norm(t) in text for t in c["synonyms"])]
    return hit


async def discover(title: str, sources: list[str], extra: str = "", year_from=None, year_to=None,
                   snowball_seeds: int = 5, max_results: int = 200) -> dict:
    plan = await expand(title, extra)
    concepts = plan["concepts"]
    essential = [c for c in concepts if c.get("essential", True)] or concepts
    hits: dict[str, dict] = {}
    found_by: dict[str, set] = {}
    errors = []

    def add(papers, how):
        for p in papers:
            if not p.get("title"):
                continue
            k = _dedupe_key(p)
            if k not in hits:
                hits[k] = p
                found_by[k] = set()
            else:
                for f in ("abstract", "doi", "pdf_url", "citations", "openalex_id", "source_id", "issn"):
                    if not hits[k].get(f) and p.get(f):
                        hits[k][f] = p[f]
            found_by[k].add(how)

    # 1) OpenAlex boolean strategy, relaxed step by step (all concepts -> essential -> pairs)
    bool_qs = [("all concepts", _bool_query(concepts))]
    if len(essential) < len(concepts):
        bool_qs.append(("essential concepts", _bool_query(essential)))
    if len(essential) >= 3:
        for i in range(len(essential)):
            for j in range(i + 1, len(essential)):
                bool_qs.append((f"{essential[i]['name']} + {essential[j]['name']}", _bool_query([essential[i], essential[j]])))
    bool_qs = bool_qs[:8]

    async def oa(label, q):
        try:
            add(await search_openalex(q, 50, year_from, year_to), "OpenAlex: " + label)
        except Exception as e:
            errors.append(f"OpenAlex ({label}): {str(e)[:120]}")

    # 2) facet queries on the selected databases
    async def facet(q):
        res, errs = await search_all(q, sources, 15, year_from, year_to)
        errors.extend(errs)
        add(res, "query: " + q)

    facet_qs = plan["queries"] or [" ".join(c["name"] for c in essential)]
    await asyncio.gather(*[oa(l, q) for l, q in bool_qs], *[facet(q) for q in facet_qs[:8]])

    def score(k):
        p = hits[k]
        cov = _coverage(p, concepts)
        p["_coverage"] = cov
        ess = sum(1 for c in essential if c["name"] in cov) / len(essential)
        return 10 * ess + 3 * len(cov) / len(concepts) + 1.5 * min(len(found_by[k]), 4) + 0.6 * math.log1p(p.get("citations") or 0)

    ranked = sorted(hits, key=score, reverse=True)

    # 3) snowball from the strongest seeds (references + citing papers), keep only on-topic ones
    seeds = [hits[k] for k in ranked[:snowball_seeds] if hits[k].get("openalex_id") or hits[k].get("doi")]

    async def snow(p, mode):
        try:
            r = await snowball(p.get("openalex_id") or p["doi"], mode, 25)
            on_topic = [x for x in r["results"] if len([c for c in essential if c["name"] in _coverage(x, concepts)]) >= max(1, len(essential) // 2)]
            add(on_topic, f"{'cited by' if mode == 'references' else 'cites'} {p['title'][:50]}")
        except Exception:
            pass
    await asyncio.gather(*[snow(p, m) for p in seeds for m in ("references", "citing")])

    ranked = sorted(hits, key=score, reverse=True)[:max_results]
    out = []
    for k in ranked:
        p = hits[k]
        p["found_by"] = sorted(found_by[k])[:6]
        p["relevance"] = round(score(k), 1)
        out.append(p)
    await quality.enrich(out)
    return {"plan": plan, "boolean_query": bool_qs[0][1], "results": out, "total_found": len(hits),
            "errors": list(dict.fromkeys(errors))[:8]}


# ---------------------------------------------------------------- evidence for an argument
async def evidence(claim: str, sources: list[str], library: list[dict] | None = None,
                   year_from=None, year_to=None, max_judge: int = 24) -> dict:
    plan = await complete_json(
        f"Argument / claim:\n<claim>{claim}</claim>\n\nWrite 5 short scholarly search queries (3-6 keywords) that would "
        "find empirical studies, reviews or data that SUPPORT this claim, and 1 query that would find studies that "
        "CONTRADICT or qualify it. Also list 6-10 key terms (with synonyms) that a relevant abstract would contain.\n"
        'Return JSON: {"queries":[""],"counter_query":"","terms":[""]}',
        system=SYS, fast=True, task="evidence-plan", max_tokens=600)
    queries = [q for q in plan.get("queries", []) if q][:5] + ([plan["counter_query"]] if plan.get("counter_query") else [])
    terms = [_norm(t) for t in plan.get("terms", []) if t] or [_norm(w) for w in re.findall(r"[A-Za-z]{4,}", claim)]

    cands: dict[str, dict] = {}
    errors = []

    async def run(q):
        res, errs = await search_all(q, sources, 12, year_from, year_to)
        errors.extend(errs)
        for p in res:
            if p.get("abstract") and p.get("title"):
                cands.setdefault(_dedupe_key(p), p)
    await asyncio.gather(*(run(q) for q in queries))
    for p in library or []:  # papers already in the library are candidates too
        if p.get("abstract"):
            cands.setdefault(_dedupe_key(p), {**p, "in_library": True})

    def lex(p):
        text = _norm((p.get("title") or "") + " " + (p.get("abstract") or ""))
        return sum(1 for t in terms if t and t in text) + (0.5 if p.get("in_library") else 0) + 0.2 * math.log1p(p.get("citations") or 0)

    pool = sorted(cands.values(), key=lex, reverse=True)[:max_judge]
    if not pool:
        return {"claim": claim, "queries": queries, "results": [], "errors": errors}
    listing = "\n\n".join(f"[{i + 1}] {p['title']} ({p.get('year') or 'n.d.'})\n{p['abstract'][:700]}" for i, p in enumerate(pool))
    judged = await complete_json(
        f"Claim:\n<claim>{claim}</claim>\n\nCandidate papers (title + abstract):\n{listing}\n\n"
        "For EACH candidate decide its stance toward the claim using only its abstract: supports | partially | "
        "contradicts | irrelevant. For non-irrelevant ones copy the single most relevant sentence VERBATIM from the "
        "abstract (max 45 words) and give a one-line reason. Be strict: topical similarity alone is not support.\n"
        'Return JSON: {"items":[{"id":1,"stance":"supports","quote":"","reason":""}]}',
        system="You are a rigorous, skeptical systematic reviewer.", fast=True, task="evidence-judge", max_tokens=3000)
    out = []
    for it in judged.get("items", []):
        try:
            p = pool[int(it["id"]) - 1]
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        stance = (it.get("stance") or "").lower()
        if stance not in ("supports", "partially", "contradicts"):
            continue
        quote = (it.get("quote") or "").strip()
        verified = bool(quote) and _norm(quote)[:120] in _norm(p.get("abstract"))
        out.append({**p, "stance": stance, "quote": quote, "quote_verified": verified, "reason": it.get("reason", "")})
    order = {"supports": 0, "partially": 1, "contradicts": 2}
    out.sort(key=lambda p: (order[p["stance"]], not p["quote_verified"], -(p.get("citations") or 0)))
    await quality.enrich(out)
    return {"claim": claim, "queries": queries, "results": out, "screened": len(pool), "found": len(cands),
            "errors": list(dict.fromkeys(errors))[:6]}
