"""Academic paper workflow (Strategist -> Composer) and writing tools.

Strategist: 1) venue/style analysis  2) literature synthesis & evidence-based gaps
            3) outline  4) reviewer simulation (7 dimensions x 5 = 35 pts, gate >= 28)
Composer:   5) section drafting with quality checks  6) polish & manuscript review
"""
import json
import re

from . import db

RUBRIC = [
    ("originality", "Originality & novelty of the contribution"),
    ("argumentation", "Logic and coherence of argumentation"),
    ("literature", "Literature coverage and positioning"),
    ("methodology", "Methodological rigor and appropriateness"),
    ("clarity", "Clarity of structure and writing"),
    ("impact", "Significance and potential impact"),
    ("accuracy", "Technical accuracy and evidential support"),
]
GATE = 28
VOICE = ("Write as the author, in the first person plural or impersonal academic voice as appropriate. Output only the requested text: no preambles (e.g. 'Here is...'), no closing remarks, and never mention AI, language models, assistants or how the text was produced.")


def system_prompt(paper: dict) -> str:
    return (f"You are a senior academic author and journal reviewer in {paper.get('field') or 'the relevant field'}. "
            f"Write in {paper.get('language') or 'English'} using formal academic register, precise terminology, "
            "hedged claims where evidence is limited, and no filler. Never fabricate data, results or citations. "
            + VOICE)


def library_context(project_id: str, keys: list[str] | None = None, max_items=60, chars=700, query: str = "") -> str:
    papers = db.list_papers(project_id, with_fulltext=bool(query))
    if keys:
        papers = [p for p in papers if p["key"] in keys] or papers
    if query:
        papers = rank_papers(papers, query)
    lines = []
    for p in papers[:max_items]:
        authors = p.get("authors") or []
        a = (authors[0].get("family", "") + (" et al." if len(authors) > 2 else (" & " + authors[1].get("family", "") if len(authors) == 2 else ""))) if authors else (p.get("venue") or "")
        text = p.get("abstract") or ""
        if query and p.get("fulltext"):
            text = best_passages(p["fulltext"], query, chars) or text
        notes = f"\n  Notes: {p['notes'][:300]}" if p.get("notes") else ""
        lines.append(f"@{p['key']}: {a} ({p.get('year') or 'n.d.'}). {p.get('title')}. {p.get('venue') or ''}\n"
                     f"  {text[:chars]}{notes}")
    return "\n".join(lines) if lines else "(library is empty)"


def _terms(text):
    return {w for w in re.findall(r"[a-z]{4,}", text.lower())}


def rank_papers(papers, query):
    q = _terms(query)
    def score(p):
        t = _terms((p.get("title") or "") * 3 + " " + (p.get("abstract") or "") + " " + (p.get("fulltext") or "")[:20000])
        return len(q & t)
    return sorted(papers, key=score, reverse=True)


def best_passages(fulltext, query, chars):
    q = _terms(query)
    paras = [p.strip() for p in re.split(r"\n\s*\n|(?<=\.)\s{2,}", fulltext) if len(p.strip()) > 200]
    if not paras:
        paras = [fulltext[i:i + 800] for i in range(0, min(len(fulltext), 40000), 800)]
    ranked = sorted(paras, key=lambda p: len(q & _terms(p)), reverse=True)
    out = ""
    for p in ranked[:3]:
        out += " … " + p[:chars // 2]
        if len(out) > chars:
            break
    return out.strip()


def paper_brief(paper: dict) -> str:
    keys = ["title", "research_question", "field", "paper_type", "venue", "contribution", "method", "data",
            "findings", "word_target", "language"]
    return "\n".join(f"{k.replace('_', ' ').title()}: {paper[k]}" for k in keys if paper.get(k))


def outline_text(outline: list[dict]) -> str:
    out = []
    for i, s in enumerate(outline or [], 1):
        out.append(f"{i}. {s.get('title')} (~{s.get('words', '?')} words)")
        for pt in s.get("points", []):
            out.append(f"   - {pt}")
        if s.get("citations"):
            out.append(f"   citations: {', '.join(s['citations'])}")
    return "\n".join(out)


# ---------------------------------------------------------------- strategist prompts
def p_style_guide(paper, samples: str):
    return (f"{paper_brief(paper)}\n\nTarget venue/platform: {paper.get('venue') or 'unspecified'}\n\n"
            + (f"Sample papers / author guidelines from the venue:\n<samples>\n{samples[:30000]}\n</samples>\n\n" if samples else "")
            + "PHASE 1 – Platform analysis. Derive a concise, actionable writing style guide for this manuscript. "
              "If samples are provided, extract conventions from them; otherwise use well-known conventions for this "
              "venue type and field. Cover in Markdown: 1) Typical structure & section names, 2) Length per section, "
              "3) Tone, voice (active/passive, I/we), tense per section, 4) How contributions and gaps are framed, "
              "5) Citation density & style, 6) Figures/tables expectations, 7) Abstract format (structured or not, "
              "word limit), 8) Common reviewer expectations / desk-rejection risks for this venue.")


def p_literature(paper, lib: str):
    return (f"{paper_brief(paper)}\n\nLibrary (cite ONLY these keys):\n<library>\n{lib}\n</library>\n\n"
            "PHASE 2 – Theoretical framework & literature analysis. In Markdown produce:\n"
            "## Thematic synthesis – group the literature into 3-6 themes; for each, synthesize findings, "
            "agreements and contradictions with [@key] citations.\n"
            "## Theoretical framework – the theories/models that underpin the study and how they connect.\n"
            "## Research gaps – a numbered list. EVERY gap must be supported by 3-5 citations, formatted as:\n"
            "### Gap 1: <short name>\n<explanation> [@key1; @key2; @key3]\n"
            "## Positioning – how this paper addresses the gaps and its expected contribution.\n"
            "Use only keys from the library. If the library is insufficient for a gap, say so explicitly and "
            "suggest search queries to fill it.")


def p_outline(paper, lib: str):
    return (f"{paper_brief(paper)}\n\nStyle guide:\n{paper.get('style_guide', '')[:6000]}\n\n"
            f"Literature analysis & gaps:\n{paper.get('literature', '')[:10000]}\n\n"
            f"Library keys available:\n{lib}\n\n"
            "PHASE 3 – Create a detailed outline for the full manuscript following the style guide. Each section "
            "has a title (no numbering), a target word count (sum ≈ the word target), 3-7 key points/arguments in "
            "order, and the library keys to cite. Include Introduction, the core sections, Discussion and "
            "Conclusion as appropriate for the paper type; do NOT include Abstract or References.\n"
            'Return JSON: {"sections":[{"title":"...","words":800,"points":["..."],"citations":["key"]}]}')


def p_review(paper, target="outline"):
    rubric = "\n".join(f"- {k}: {d} (1-5)" for k, d in RUBRIC)
    if target == "outline":
        material = f"Outline:\n{outline_text(paper.get('outline', []))}\n\nGaps & positioning:\n{paper.get('literature', '')[:6000]}"
    else:
        material = f"Full manuscript:\n{assemble(paper, include_refs=False)[:60000]}"
    return (f"{paper_brief(paper)}\n\n{material}\n\n"
            f"Act as a demanding peer reviewer for {paper.get('venue') or 'a top journal in the field'}. "
            f"Score the {target} on 7 dimensions (1 = poor, 5 = excellent):\n{rubric}\n"
            f"Total is out of 35; {GATE}+ is required to proceed. Be strict and specific.\n"
            'Return JSON: {"scores":{"originality":{"score":4,"comment":"..."},...},'
            '"strengths":["..."],"weaknesses":["..."],"revisions":["concrete actionable revision"],'
            '"verdict":"accept|minor revision|major revision|reject"}')


def p_revise_outline(paper):
    review = paper.get("outline_review") or {}
    return (f"{paper_brief(paper)}\n\nCurrent outline:\n{json.dumps(paper.get('outline', []), ensure_ascii=False)}\n\n"
            f"Reviewer feedback:\n{json.dumps(review, ensure_ascii=False)[:8000]}\n\n"
            "Revise the outline to address every weakness and revision request while keeping the target length. "
            'Return JSON: {"sections":[{"title":"...","words":800,"points":["..."],"citations":["key"]}]}')


# ---------------------------------------------------------------- composer prompts
def p_section(paper, idx: int, lib: str, instruction: str = ""):
    """Returns (stable, task). `stable` is identical for every section of the paper, so it goes in the
    system prompt and is cached by Claude models; `task` holds only what changes per call."""
    sections = paper.get("sections", [])
    sec = sections[idx]
    stable = (f"{paper_brief(paper)}\n\nStyle guide:\n{paper.get('style_guide', '')[:4000]}\n\n"
              f"Full outline:\n{outline_text(paper.get('outline', []))}\n\n"
              f"Library (cite ONLY these keys):\n<library>\n{lib}\n</library>\n\n"
              "Rules: Markdown body only — do NOT repeat the section title as a heading; use '### ' for "
              "subsections. Cite claims with [@key] / [@key1; @key2] / narrative @key. Never invent keys, data or "
              "results; where the author must supply results/data, insert a clearly marked placeholder like "
              "[TODO: insert result of X]. Use Markdown tables where appropriate. Follow the style guide's tense "
              "and voice. Smooth transitions from the previous section.")
    prev = next((s_ for s_ in reversed(sections[:idx]) if s_.get("content")), None)
    existing = sec.get("content", "")
    if instruction and existing:
        job = (f"Revise the section '{sec['title']}' according to this instruction: {instruction}\n\n"
               f"<existing>\n{existing}\n</existing>")
    else:
        job = f"Write the section '{sec['title']}' (~{sec.get('words', 600)} words)."
    task = (f"Section plan — points: {json.dumps(sec.get('points', []), ensure_ascii=False)}; "
            f"suggested citations: {', '.join(sec.get('citations', [])) or 'any relevant'}\n\n"
            + (f"End of the previous section ('{prev['title']}'), for continuity:\n<previous>\n"
               f"{prev['content'][-1200:]}\n</previous>\n\n" if prev else "")
            + job)
    return stable, task


def p_abstract(paper):
    return (f"{paper_brief(paper)}\n\nStyle guide:\n{paper.get('style_guide', '')[:3000]}\n\n"
            f"Manuscript:\n{assemble(paper, include_refs=False)[:50000]}\n\n"
            "Write: 5 alternative titles (concise, informative), an abstract following the venue's conventions "
            "(structured if typical; default 200-250 words; no citations), and 4-6 keywords.\n"
            'Return JSON: {"titles":["..."],"abstract":"...","keywords":["..."]}')


# ---------------------------------------------------------------- tools
TOOLS = {
    "polish": ("Polish (academic)", "Improve this text for a top academic journal: fix grammar, improve flow, "
               "concision and precision, keep meaning and all citations ([@key]) intact. Return only the revised text."),
    "paraphrase": ("Paraphrase", "Paraphrase this text substantially (new sentence structures and wording) while "
                   "preserving meaning, technical terms and all [@key] citations. Return only the paraphrase."),
    "shorten": ("Shorten", "Reduce the length of this text by ~40% without losing key arguments or citations. Return only the text."),
    "expand": ("Expand", "Expand this text with deeper explanation, transitions and nuance (no invented facts or "
               "citations; add [TODO: cite] where support is needed). Return only the text."),
    "translate_en": ("Translate → English", "Translate into formal academic English. Keep [@key] citations. Return only the translation."),
    "translate_id": ("Translate → Indonesian", "Terjemahkan ke Bahasa Indonesia akademik baku (PUEBI/EYD). "
                     "Pertahankan sitasi [@key]. Kembalikan hanya terjemahannya."),
    "claims": ("Find unsupported claims", "List every claim in this text that needs a citation or evidence but lacks "
               "one. For each, quote the sentence, explain why, and suggest what kind of source would support it. Markdown."),
    "critique": ("Reviewer critique", "Act as a critical peer reviewer. Give a structured critique (Markdown): "
                 "major issues, minor issues, logic gaps, clarity problems, and concrete suggestions."),
    "outline_from_text": ("Reverse outline", "Produce a reverse outline of this text (one line per paragraph: main "
                          "point), then evaluate the logical flow and suggest reordering. Markdown."),
    "title": ("Suggest titles", "Suggest 8 strong academic titles for this text, varied in style (descriptive, "
              "question, colon-subtitle). Markdown list."),
    "abstract": ("Generate abstract", "Write a structured academic abstract (Background, Objective, Methods, Results, "
                 "Conclusion; 200-250 words) plus 5 keywords, based solely on this text."),
    "response_letter": ("Reviewer response letter", "The text contains reviewer comments (and optionally the "
                        "author's notes). Draft a polite, point-by-point response letter, quoting each comment, "
                        "responding and stating changes made (use [TODO] where the author must fill details)."),
    "methods_check": ("Methodology check", "Evaluate the research methodology described: design, sampling, validity, "
                      "reliability, ethics, statistical approach, threats to validity, and missing reporting items "
                      "(e.g., per CONSORT/PRISMA/STROBE where relevant). Markdown."),
    "simplify": ("Plain-language summary", "Write a plain-language summary (for non-specialists, ~150 words) of this text."),
}


def p_tool(tool: str, text: str, extra: str = "", language: str = "") -> str:
    name, instr = TOOLS[tool]
    lang = f"\nWrite the output in {language}." if language and not tool.startswith("translate") else ""
    return f"{instr}{lang}\n{('Additional instruction: ' + extra) if extra else ''}\n\n<text>\n{text}\n</text>"


def p_ask_library(question: str, lib: str) -> str:
    return (f"Answer the research question using ONLY the library excerpts below. Cite with [@key]. If the "
            f"library does not contain the answer, say so and suggest what to search for.\n\n"
            f"<library>\n{lib}\n</library>\n\nQuestion: {question}")


def p_suggest_citations(text: str, lib: str) -> str:
    return (f"For the text below, find sentences that should be supported by sources from the library. Only "
            f"suggest keys whose content genuinely supports the sentence.\n\n<library>\n{lib}\n</library>\n\n"
            f"<text>\n{text}\n</text>\n\n"
            'Return JSON: {"suggestions":[{"sentence":"exact sentence","keys":["key"],"reason":"..."}]}')


# ---------------------------------------------------------------- assembly & checks
def assemble(paper: dict, include_refs=True) -> str:
    parts = []
    for s in paper.get("sections", []):
        if s.get("content"):
            parts.append(f"# {s['title']}\n\n{s['content'].strip()}")
    return "\n\n".join(parts)


def quality_check(text: str, library_keys: set[str], target_words: int | None = None,
                  min_cites_per_gap: int = 3) -> dict:
    """Deterministic checks (the 'Python verification scripts' of the workflow)."""
    plain = re.sub(r"\[[^\]]*@[^\]]*\]", "", text)
    words = len(re.findall(r"\b\w+\b", plain))
    cites = re.findall(r"@([A-Za-z][\w:\-]*[A-Za-z0-9])", text)
    unique = set(cites)
    unknown = sorted(k for k in unique if k not in library_keys)
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip() and not p.strip().startswith(("#", "|"))]
    uncited_paras = sum(1 for p in paras if "@" not in p and len(p.split()) > 60)
    sentences = re.split(r"(?<=[.!?])\s+", plain)
    long_sent = [s for s in sentences if len(s.split()) > 40]
    todos = len(re.findall(r"\[TODO", text))
    warnings = []
    if target_words and words < 0.75 * target_words:
        warnings.append(f"Too short: {words} words vs target {target_words}.")
    if target_words and words > 1.3 * target_words:
        warnings.append(f"Too long: {words} words vs target {target_words}.")
    if unknown:
        warnings.append(f"Unknown citation keys (not in library): {', '.join(unknown)}")
    if uncited_paras:
        warnings.append(f"{uncited_paras} substantial paragraph(s) without any citation.")
    if long_sent:
        warnings.append(f"{len(long_sent)} sentence(s) longer than 40 words — consider splitting.")
    if todos:
        warnings.append(f"{todos} [TODO] placeholder(s) need author input.")
    # gap evidence gate
    gaps = []
    for m in re.finditer(r"###\s*Gap[^\n]*\n(.*?)(?=\n###|\n## |\Z)", text, re.S):
        n = len(set(re.findall(r"@([A-Za-z][\w:\-]*[A-Za-z0-9])", m.group(1))))
        title = m.group(0).split("\n", 1)[0].replace("###", "").strip()
        gaps.append({"gap": title, "citations": n, "ok": n >= min_cites_per_gap})
        if n < min_cites_per_gap:
            warnings.append(f"'{title}' has only {n} supporting citation(s); {min_cites_per_gap}-5 required.")
    sents = [x for x in sentences if re.search(r"[A-Za-z]", x)]
    syll = sum(_syllables(w) for w in re.findall(r"[A-Za-z]+", plain))
    flesch = round(206.835 - 1.015 * (words / max(1, len(sents))) - 84.6 * (syll / max(1, words)), 1) if words else 0
    return {"words": words, "citations": len(cites), "unique_sources": len(unique),
            "readability": flesch, "avg_sentence_words": round(words / max(1, len(sents)), 1),
            "citations_per_100_words": round(len(cites) * 100 / words, 2) if words else 0,
            "paragraphs": len(paras), "unknown_keys": unknown, "long_sentences": len(long_sent),
            "todos": todos, "gaps": gaps, "warnings": warnings}


def review_total(review: dict) -> int:
    total = 0
    for k, _ in RUBRIC:
        v = (review.get("scores") or {}).get(k)
        if isinstance(v, dict):
            v = v.get("score")
        try:
            total += max(0, min(5, int(round(float(v)))))
        except (TypeError, ValueError):
            pass
    return total


def _syllables(word: str) -> int:
    w = word.lower()
    n = len(re.findall(r"[aeiouy]+", w))
    if w.endswith("e") and n > 1:
        n -= 1
    return max(1, n)


def similarity(text: str, papers: list[dict], n: int = 7) -> dict:
    """Overlap check (no AI tokens): 7-word shingles of the text vs. library abstracts, notes and full texts.
    Flags sentences that reuse source wording — paraphrase or quote+cite them."""
    def words(s):
        return re.findall(r"[a-z0-9]+", s.lower())

    def shingles(ws):
        return {" ".join(ws[i:i + n]) for i in range(len(ws) - n + 1)}

    index: dict[str, str] = {}
    for p in papers:
        src = " ".join([p.get("abstract") or "", (p.get("fulltext") or "")[:300000]])
        for sh in shingles(words(src)):
            index.setdefault(sh, p["key"])
    plain = re.sub(r"\[[^\]]*@[^\]]*\]", "", text)
    total = matched = 0
    flagged = []
    for sent in re.split(r"(?<=[.!?])\s+", plain):
        sh = shingles(words(sent))
        if not sh:
            continue
        hits = [index[x] for x in sh if x in index]
        total += len(sh)
        matched += len(hits)
        if hits and len(hits) / len(sh) >= 0.3:
            top = max(set(hits), key=hits.count)
            flagged.append({"sentence": sent.strip()[:400], "source": top, "pct": round(100 * len(hits) / len(sh))})
    flagged.sort(key=lambda f: -f["pct"])
    return {"overall_pct": round(100 * matched / total, 1) if total else 0, "sources_checked": len(papers),
            "flagged": flagged[:40]}
