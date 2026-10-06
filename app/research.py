"""Deep Research engine: recursive breadth x depth search -> learnings -> cited report.

Inspired by deep-research-web-ui: queries form a tree, every learning is tied to the
sources it came from, and the final report cites sources with [@key] markers that are
resolved against the project library at export time.
"""
import asyncio
import json
import math
import re
import time

from . import db, quality
from .writer import VOICE
from .llm import complete_json, stream_chat
from .search import fetch_page, make_key, search_all

SYSTEM = ("You are an expert academic researcher. Today is {today}. Be precise, evidence-driven and "
          "skeptical; prefer peer-reviewed sources; mark speculation clearly. Write in {language}. " + VOICE)

RUNS: dict[str, "ResearchRun"] = {}


def _src_brief(s: dict, content_chars=1200) -> str:
    authors = s.get("authors") or []
    a = (authors[0].get("family", "") + (" et al." if len(authors) > 1 else "")) if authors else s.get("venue", "")
    body = (s.get("content") or s.get("abstract") or "")[:content_chars]
    return f"[{s['sid']}] {s.get('title')} — {a} ({s.get('year') or 'n.d.'}), {s.get('venue') or ''}\n{body}"


class ResearchRun:
    def __init__(self, project_id: str, query: str, params: dict):
        self.id = db.new_id()
        self.project_id = project_id
        self.query = query
        self.params = params
        self.status = "running"
        self.created = time.time()
        self.events: list[dict] = []
        self.cond = asyncio.Condition()
        self.tree: list[dict] = []
        self.learnings: list[dict] = []
        self.sources: list[dict] = []
        self._src_index: dict[str, int] = {}
        self.report = ""
        self.sem = asyncio.Semaphore(int(params.get("concurrency", 3)))
        self._last_save = 0.0
        self.system = SYSTEM.format(today=time.strftime("%Y-%m-%d"), language=params.get("language", "English"))

    # ------------------------------------------------------------ events
    async def emit(self, type_: str, data: dict):
        async with self.cond:
            self.events.append({"type": type_, **data})
            self.cond.notify_all()

    def save(self, force=False):
        """Persist progress so a crash/restart never loses a run (throttled to every 3 s)."""
        if force or time.time() - self._last_save > 3:
            self._last_save = time.time()
            try:
                db.save_run(self.snapshot())
            except Exception:
                pass

    def snapshot(self) -> dict:
        return {"id": self.id, "project_id": self.project_id, "query": self.query, "params": self.params,
                "status": self.status, "tree": self.tree, "learnings": self.learnings,
                "sources": self.sources, "report": self.report, "created": self.created}

    # ------------------------------------------------------------ steps
    def _register(self, src: dict) -> dict:
        k = (src.get("doi") or "").lower() or src.get("url") or src.get("title", "").lower()
        if k in self._src_index:
            return self.sources[self._src_index[k]]
        src = dict(src)
        src["sid"] = len(self.sources) + 1
        self._src_index[k] = len(self.sources)
        self.sources.append(src)
        return src

    async def gen_queries(self, query: str, n: int, learnings: list[str]) -> list[dict]:
        prior = ""
        if learnings:
            prior = "\n\nLearnings so far (avoid duplicating them, dig deeper):\n" + "\n".join(f"- {l}" for l in learnings[-30:])
        prompt = (f"Generate up to {n} distinct search queries to research the topic below. Queries go to "
                  f"scholarly search engines (OpenAlex, Semantic Scholar, arXiv) and the web, so keep each "
                  f"query short (3-8 keywords, in English unless the topic requires otherwise). Each query must "
                  f"explore a different angle. For each, state the research goal and how to go deeper.\n\n"
                  f"<topic>\n{query}\n</topic>{prior}\n\n"
                  'Return JSON: {"queries":[{"query":"...","goal":"..."}]}')
        data = await complete_json(prompt, system=self.system, fast=True, task="research-plan")
        qs = data.get("queries", data) if isinstance(data, dict) else data
        return [q for q in qs if isinstance(q, dict) and q.get("query")][:n]

    async def analyze(self, query: str, goal: str, results: list[dict]) -> dict:
        if not results:
            return {"learnings": [], "followUpQuestions": []}
        ctx = "\n\n".join(_src_brief(s) for s in results)
        prompt = (f"Search query: <query>{query}</query>\nResearch goal: {goal}\n\n"
                  f"Search results (numbered sources):\n<results>\n{ctx}\n</results>\n\n"
                  "Extract up to 5 unique, information-dense learnings relevant to the goal. Include concrete "
                  "entities, methods, numbers, effect sizes, datasets and dates where available. Each learning "
                  "must list the source numbers it is based on. Ignore irrelevant results. Also propose up to 3 "
                  "follow-up questions that would deepen the research.\n\n"
                  'Return JSON: {"learnings":[{"text":"...","sources":[1,2]}],"followUpQuestions":["..."]}')
        data = await complete_json(prompt, system=self.system, fast=True, task="research-extract")
        valid = {s["sid"] for s in results}
        out = []
        for l in data.get("learnings", []):
            if isinstance(l, str):
                l = {"text": l, "sources": []}
            l["sources"] = [int(x) for x in l.get("sources", []) if str(x).isdigit() and int(x) in valid]
            out.append(l)
        return {"learnings": out, "followUpQuestions": data.get("followUpQuestions", [])[:3]}

    async def explore(self, item: dict, depth: int, breadth: int, parent: str | None):
        node = {"id": db.new_id(), "parent": parent, "query": item["query"], "goal": item.get("goal", ""),
                "status": "searching", "results": [], "learnings": [], "depth": depth}
        self.tree.append(node)
        await self.emit("node", {"node": node})
        p = self.params
        async with self.sem:
            try:
                results, errors = await search_all(item["query"], p.get("sources") or ["openalex"],
                                                   int(p.get("per_query", 6)), p.get("year_from"), p.get("year_to"))
                maxq = p.get("max_quartile") or None
                if maxq:
                    await quality.enrich(results)
                before = len(results)
                results = [r for r in results if quality.passes(r, maxq, p.get("allow_unranked", False),
                                                                p.get("allow_preprints", True))]
                if before != len(results):
                    errors = errors + [f"{before - len(results)} result(s) removed by the quality filter / retractions"]
                results = [self._register(r) for r in results]
                if p.get("read_full") and results:
                    await self._read_full(results)
                node["results"] = [{"sid": r["sid"], "title": r.get("title"), "url": r.get("url"),
                                    "year": r.get("year"), "type": r.get("type")} for r in results]
                node["errors"] = errors
                node["status"] = "analyzing"
                await self.emit("node", {"node": node})
                analysis = await self.analyze(item["query"], item.get("goal", ""), results)
            except Exception as e:
                node["status"] = "error"
                node["error"] = str(e)[:300]
                await self.emit("node", {"node": node})
                return
        node["learnings"] = analysis["learnings"]
        node["followups"] = analysis["followUpQuestions"]
        node["status"] = "done"
        self.learnings.extend(analysis["learnings"])
        await self.emit("node", {"node": node})
        self.save(force=True)
        if depth > 1 and analysis["followUpQuestions"]:
            next_query = (f"Previous research goal: {item.get('goal', '')}\nFollow-up directions:\n" +
                          "\n".join(f"- {q}" for q in analysis["followUpQuestions"]))
            await self.level(next_query, max(1, math.ceil(breadth / 2)), depth - 1, node["id"])

    async def _read_full(self, results: list[dict]):
        """Fetch full text for web pages / open-access PDFs to get verbatim evidence."""
        targets = [r for r in results if not r.get("content") and (r.get("type") == "webpage" or r.get("pdf_url"))][:2]

        async def one(r):
            try:
                page = await fetch_page(r.get("pdf_url") or r["url"], max_chars=8000)
                r["content"] = page["text"]
            except Exception:
                pass
        await asyncio.gather(*(one(r) for r in targets))

    async def level(self, query: str, breadth: int, depth: int, parent: str | None):
        await self.emit("status", {"message": f"Planning {breadth} queries (depth {depth})"})
        queries = await self.gen_queries(query, breadth, [l["text"] for l in self.learnings])
        await asyncio.gather(*(self.explore(q, depth, breadth, parent) for q in queries))

    # ------------------------------------------------------------ report
    def _assign_keys(self, cited_sids: set[int]):
        used = set()
        lib = {p["key"]: p for p in db.list_papers(self.project_id)}
        for s in self.sources:
            if s["sid"] not in cited_sids:
                continue
            existing = db.find_existing(self.project_id, s)
            if existing:
                s["key"] = existing["key"]
            else:
                base = make_key(s)
                k = base
                i = 0
                while k in used or k in lib:
                    i += 1
                    k = base + "abcdefghijklmnopqrstuvwxyz"[i % 26] + (str(i // 26) if i >= 26 else "")
                s["key"] = k
            used.add(s["key"])

    async def write_report(self):
        cited = {sid for l in self.learnings for sid in l.get("sources", [])}
        self._assign_keys(cited)
        by_sid = {s["sid"]: s for s in self.sources}
        learn_txt = "\n".join(
            f"- {l['text']} " + " ".join(f"[@{by_sid[x]['key']}]" for x in l.get("sources", []) if x in by_sid)
            for l in self.learnings)
        src_txt = "\n".join(
            f"- @{s['key']}: {s.get('title')} ({s.get('year') or 'n.d.'}; {s.get('venue') or s.get('type')})"
            for s in self.sources if s.get("key"))
        rtype = self.params.get("report_type", "Literature review")
        extra = self.params.get("report_instructions", "")
        prompt = (f"Using the research learnings below, write a {rtype} answering:\n<question>\n{self.full_query}\n</question>\n\n"
                  f"<learnings>\n{learn_txt}\n</learnings>\n\n<sources>\n{src_txt}\n</sources>\n\n"
                  "Requirements:\n"
                  "- Academic register, Markdown. Start with a '# ' title, then '## Abstract' (150-250 words), "
                  "'## Introduction', several thematic '## ' sections (use '### ' subsections when useful), "
                  "'## Discussion' (contradictions, research gaps, limitations), '## Conclusion'.\n"
                  "- Cite EVERY factual claim with Pandoc markers using ONLY the source keys above, e.g. [@key] or "
                  "[@key1; @key2]. Never invent keys, never use numeric citations.\n"
                  "- Synthesize across sources (compare, contrast, group by theme); do not list sources one by one.\n"
                  "- Use a Markdown table where a comparison helps.\n"
                  "- Do NOT write a References section (it is generated automatically).\n"
                  f"- Be thorough: aim for {self.params.get('report_words', 2000)} words.\n{extra}")
        self.report = ""
        async for piece in stream_chat([{"role": "user", "content": prompt}], system=self.system, task="research-report"):
            self.report += piece
            await self.emit("report", {"delta": piece})
            self.save()

    # ------------------------------------------------------------ main
    async def run(self):
        p = self.params
        self.full_query = self.query
        if p.get("clarifications"):
            self.full_query += "\n\nClarifications from the researcher:\n" + "\n".join(
                f"Q: {c.get('q')}\nA: {c.get('a')}" for c in p["clarifications"] if c.get("a"))
        try:
            await self.level(self.full_query, int(p.get("breadth", 3)), int(p.get("depth", 2)), None)
            await self.emit("status", {"message": f"Writing report from {len(self.learnings)} learnings"})
            await self.write_report()
            if p.get("auto_add_sources", True):
                cited_keys = set(re.findall(r"@([\w:\-]+)", self.report))
                for s in self.sources:
                    if s.get("key") in cited_keys:
                        stored = db.add_paper(self.project_id, s, tags="deep-research")
                        if stored["key"] != s["key"]:
                            self.report = self.report.replace("@" + s["key"], "@" + stored["key"])
                            s["key"] = stored["key"]
            self.status = "done"
        except Exception as e:
            self.status = "error"
            await self.emit("error", {"message": f"{type(e).__name__}: {e}"})
        db.save_run(self.snapshot())
        await self.emit("done", {"status": self.status, "report": self.report})


async def clarify(query: str, language="English") -> list[str]:
    prompt = (f"A researcher wants to investigate:\n<query>{query}</query>\n\n"
              "Ask up to 4 short clarifying questions that would most improve the research direction "
              "(scope, population/context, time period, methods of interest, intended output). "
              'Return JSON: {"questions":["..."]}')
    data = await complete_json(prompt, system=SYSTEM.format(today=time.strftime("%Y-%m-%d"), language=language),
                               fast=True, task="research-clarify")
    return data.get("questions", [])[:4]


def start(project_id: str, query: str, params: dict) -> ResearchRun:
    run = ResearchRun(project_id, query, params)
    RUNS[run.id] = run
    run.save(force=True)
    asyncio.create_task(run.run())
    return run


async def event_stream(run_id: str):
    run = RUNS.get(run_id)
    if not run:
        saved = db.get_run(run_id)
        yield f"data: {json.dumps({'type': 'snapshot', 'run': saved})}\n\n"
        return
    yield f"data: {json.dumps({'type': 'snapshot', 'run': run.snapshot()})}\n\n"
    idx = len(run.events)
    while True:
        async with run.cond:
            if idx >= len(run.events):
                try:
                    await asyncio.wait_for(run.cond.wait(), timeout=15)
                except asyncio.TimeoutError:
                    pass
        if idx >= len(run.events):
            yield ": keepalive\n\n"
            continue
        while idx < len(run.events):
            ev = run.events[idx]
            idx += 1
            yield f"data: {json.dumps(ev)}\n\n"
            if ev["type"] == "done":
                return
