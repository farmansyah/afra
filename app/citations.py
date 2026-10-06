"""Citation styles, in-text citation processing and BibTeX import/export.

Drafts cite sources with Pandoc-like markers:
    [@smith2020deep]                 -> (Smith et al., 2020)  /  [1]
    [@smith2020deep, p. 12; @lee2019] -> (Smith et al., 2020, p. 12; Lee, 2019)
    @smith2020deep (narrative)       -> Smith et al. (2020)   /  Smith et al. [1]
Formatted references use *asterisks* for italics (rendered by the DOCX exporter).
"""
import datetime
import re

STYLES = {
    "apa": {"name": "APA 7th", "numeric": False},
    "ieee": {"name": "IEEE", "numeric": True},
    "harvard": {"name": "Harvard", "numeric": False},
    "chicago": {"name": "Chicago (Author-Date)", "numeric": False},
    "vancouver": {"name": "Vancouver", "numeric": True},
    "mla": {"name": "MLA 9th", "numeric": False},
}


# ---------------------------------------------------------------- names
def _initials(given: str, dots=True, space=True) -> str:
    parts = [p for p in re.split(r"[\s.]+", given or "") if p]
    out = []
    for p in parts:
        if "-" in p:
            sub = [s for s in p.split("-") if s]
            out.append("-".join(s[0].upper() + ("." if dots else "") for s in sub))
        else:
            out.append(p[0].upper() + ("." if dots else ""))
    return (" " if space else "").join(out)


def _fam(a):
    return a.get("literal") or a.get("family") or ""


def _is_org(a):
    return bool(a.get("literal")) or not a.get("given")


def _apa_name(a):
    return _fam(a) if _is_org(a) else f"{_fam(a)}, {_initials(a['given'])}"


def _harvard_name(a):
    return _fam(a) if _is_org(a) else f"{_fam(a)}, {_initials(a['given'], space=False)}"


def _ieee_name(a):
    return _fam(a) if _is_org(a) else f"{_initials(a['given'])} {_fam(a)}"


def _vancouver_name(a):
    return _fam(a) if _is_org(a) else f"{_fam(a)} {_initials(a['given'], dots=False, space=False)}"


def _full_name(a, inverted=False):
    if _is_org(a):
        return _fam(a)
    return f"{_fam(a)}, {a['given']}" if inverted else f"{a['given']} {_fam(a)}"


def _join(names, final_sep, two_sep=None, serial=True):
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]}{two_sep if two_sep is not None else final_sep}{names[1]}"
    return ", ".join(names[:-1]) + ("," if serial else "") + final_sep + names[-1]


# ---------------------------------------------------------------- pieces
def _year(p):
    return str(p.get("year")) if p.get("year") else "n.d."


def _doi_url(p):
    if p.get("doi"):
        return f"https://doi.org/{p['doi']}"
    return p.get("url") or ""


def _end(s):
    s = (s or "").strip()
    return s if not s or s[-1] in ".?!" else s + "."


def _title(p):
    return (p.get("title") or "Untitled").strip().rstrip(".")


def _pages(p):
    return (p.get("pages") or "").replace("--", "–").replace("-", "–")


def _accessed(p):
    d = p.get("accessed") or datetime.date.today().isoformat()
    try:
        return datetime.date.fromisoformat(d[:10])
    except ValueError:
        return datetime.date.today()


# ---------------------------------------------------------------- reference formatters
def ref_apa(p):
    authors = p.get("authors") or []
    names = [_apa_name(a) for a in authors]
    if len(names) > 20:
        names = names[:19] + ["... " + names[-1]]
        auth = ", ".join(names)
    else:
        auth = names[0] if len(names) == 1 else (", ".join(names[:-1]) + ", & " + names[-1] if names else "")
    t = p.get("type", "article")
    title = _title(p)
    yr = f"({_year(p)})."
    link = _doi_url(p)
    if t in ("book", "thesis", "report"):
        body = f"*{title}*."
        if t == "thesis":
            body = f"*{title}* [Doctoral dissertation/Master's thesis]."
        tail = f" {p['publisher']}." if p.get("publisher") else ""
        main = f"{body}{tail}"
    elif t == "webpage":
        main = f"*{title}*." + (f" {p['venue']}." if p.get("venue") and authors else "")
    elif t == "preprint":
        main = f"*{title}* [Preprint]. {p.get('venue') or 'arXiv'}."
    elif t == "chapter":
        main = f"{title}. In *{p.get('venue', '')}*" + (f" (pp. {_pages(p)})" if p.get("pages") else "") + "." + \
               (f" {p['publisher']}." if p.get("publisher") else "")
    elif t == "conference":
        main = f"{title}. In *{p.get('venue', '')}*" + (f" (pp. {_pages(p)})" if p.get("pages") else "") + "."
    else:
        venue = p.get("venue") or ""
        main = f"{title}."
        if venue:
            main += f" *{venue}*"
            if p.get("volume"):
                main += f", *{p['volume']}*"
                if p.get("issue"):
                    main += f"({p['issue']})"
            if p.get("pages"):
                main += f", {_pages(p)}"
            main += "."
    if not authors:
        # Title moves to author position
        return f"{main} {yr} {link}".strip()
    return f"{_end(auth)} {yr} {main} {link}".strip()


def ref_ieee(p, n=None):
    authors = p.get("authors") or []
    names = [_ieee_name(a) for a in authors]
    if len(names) > 6:
        auth = names[0] + " *et al.*"
    else:
        auth = _join(names, " and ", " and ", serial=True)
    t = p.get("type", "article")
    title = _title(p)
    parts = []
    if t in ("book", "thesis", "report"):
        s = f"{auth}, *{title}*." if auth else f"*{title}*."
        if p.get("publisher"):
            s += f" {p['publisher']}, {_year(p)}."
        else:
            s += f" {_year(p)}."
        parts.append(s)
    elif t == "webpage":
        acc = _accessed(p)
        s = (f"{auth}, " if auth else "") + f"\u201c{title},\u201d {p.get('venue', '')}. [Online]. Available: {p.get('url', '')} " \
            f"(accessed {acc.strftime('%b. %d, %Y')})."
        parts.append(s)
    else:
        s = (f"{auth}, " if auth else "") + f"\u201c{title},\u201d "
        venue = p.get("venue") or ("arXiv" if t == "preprint" else "")
        if t == "conference":
            s += f"in *{venue}*, " if venue else ""
        elif venue:
            s += f"*{venue}*, "
        if p.get("volume"):
            s += f"vol. {p['volume']}, "
        if p.get("issue"):
            s += f"no. {p['issue']}, "
        if p.get("pages"):
            s += ("pp. " if "–" in _pages(p) else "p. ") + f"{_pages(p)}, "
        s += f"{_year(p)}"
        s += f", doi: {p['doi']}." if p.get("doi") else (f". [Online]. Available: {p['url']}" if p.get("url") else ".")
        parts.append(s)
    out = " ".join(parts)
    return f"[{n}] {out}" if n is not None else out


def ref_harvard(p):
    authors = p.get("authors") or []
    names = [_harvard_name(a) for a in authors]
    auth = _join(names, " and ", " and ", serial=False)
    t = p.get("type", "article")
    title = _title(p)
    yr = f"({_year(p)})"
    if t in ("book", "thesis", "report"):
        main = f"*{title}*." + (f" {p['publisher']}." if p.get("publisher") else "")
    elif t == "webpage":
        acc = _accessed(p)
        main = f"*{title}*. Available at: {p.get('url', '')} (Accessed: {acc.day} {acc.strftime('%B %Y')})."
        return f"{auth or p.get('venue', '')} {yr} {main}".strip()
    else:
        main = f"\u2018{title}\u2019"
        if p.get("venue"):
            main += f", *{p['venue']}*"
        if p.get("volume"):
            main += f", {p['volume']}" + (f"({p['issue']})" if p.get("issue") else "")
        if p.get("pages"):
            main += f", pp. {_pages(p)}"
        main += "."
        if p.get("doi"):
            main += f" doi: {p['doi']}."
        elif p.get("url"):
            main += f" Available at: {p['url']}."
    return f"{auth} {yr} {main}".strip()


def ref_chicago(p):
    authors = p.get("authors") or []
    names = [_full_name(a, inverted=(i == 0)) for i, a in enumerate(authors)]
    if len(names) > 10:
        names = names[:7] + ["et al."]
        auth = ", ".join(names)
    else:
        auth = _join(names, " and ", " and ", serial=True)
    t = p.get("type", "article")
    title = _title(p)
    link = _doi_url(p)
    if t in ("book", "thesis", "report"):
        main = f"*{title}*." + (f" {p['publisher']}." if p.get("publisher") else "")
    elif t == "webpage":
        main = f"\u201c{title}.\u201d {p.get('venue', '')}."
    else:
        main = f"\u201c{title}.\u201d"
        if p.get("venue"):
            main += f" *{p['venue']}*"
        if p.get("volume"):
            main += f" {p['volume']}"
        if p.get("issue"):
            main += f" ({p['issue']})"
        if p.get("pages"):
            main += f": {_pages(p)}"
        main += "."
    return f"{_end(auth) if auth else ''} {_year(p)}. {main} {link}".strip()


def ref_vancouver(p, n=None):
    authors = p.get("authors") or []
    names = [_vancouver_name(a) for a in authors]
    if len(names) > 6:
        names = names[:6] + ["et al"]
    auth = ", ".join(names)
    t = p.get("type", "article")
    title = _title(p)
    if t in ("book", "thesis", "report"):
        s = f"{_end(auth)} {title}. " + (f"{p['publisher']}; " if p.get("publisher") else "") + f"{_year(p)}."
    elif t == "webpage":
        acc = _accessed(p)
        s = f"{_end(auth) + ' ' if auth else ''}{title} [Internet]. {p.get('venue', '')}; [cited {acc.isoformat()}]. " \
            f"Available from: {p.get('url', '')}"
    else:
        s = f"{_end(auth) + ' ' if auth else ''}{title}. {p.get('venue') or ''}. {_year(p)}"
        if p.get("volume"):
            s += f";{p['volume']}"
            if p.get("issue"):
                s += f"({p['issue']})"
        if p.get("pages"):
            s += f":{_pages(p).replace('–', '-')}"
        s += "."
        if p.get("doi"):
            s += f" doi:{p['doi']}"
    return f"{n}. {s}" if n is not None else s


def ref_mla(p):
    authors = p.get("authors") or []
    if not authors:
        auth = ""
    elif len(authors) == 1:
        auth = _full_name(authors[0], inverted=True)
    elif len(authors) == 2:
        auth = f"{_full_name(authors[0], True)}, and {_full_name(authors[1])}"
    else:
        auth = f"{_full_name(authors[0], True)}, et al"
    t = p.get("type", "article")
    title = _title(p)
    link = _doi_url(p).replace("https://", "")
    if t in ("book", "thesis", "report"):
        main = f"*{title}*." + (f" {p['publisher']}," if p.get("publisher") else "") + f" {_year(p)}."
    else:
        main = f"\u201c{title}.\u201d"
        if p.get("venue"):
            main += f" *{p['venue']}*,"
        if p.get("volume"):
            main += f" vol. {p['volume']},"
        if p.get("issue"):
            main += f" no. {p['issue']},"
        main += f" {_year(p)}"
        if p.get("pages"):
            main += f", pp. {_pages(p)}"
        main += "."
    return f"{_end(auth) + ' ' if auth else ''}{main} {link + '.' if link else ''}".strip()


FORMATTERS = {"apa": ref_apa, "harvard": ref_harvard, "chicago": ref_chicago, "mla": ref_mla}


def format_reference(p: dict, style: str, n: int | None = None) -> str:
    if style == "ieee":
        return ref_ieee(p, n)
    if style == "vancouver":
        return ref_vancouver(p, n)
    return FORMATTERS.get(style, ref_apa)(p)


# ---------------------------------------------------------------- in-text
def _short_authors(p, style):
    authors = p.get("authors") or []
    if not authors:
        t = _title(p)
        return f"\u201c{t[:40]}{'…' if len(t) > 40 else ''}\u201d"
    fams = [_fam(a) for a in authors]
    amp = " & " if style == "apa" else " and "
    if len(fams) == 1:
        return fams[0]
    if len(fams) == 2:
        return f"{fams[0]}{amp}{fams[1]}"
    if style == "chicago" and len(fams) == 3:
        return f"{fams[0]}, {fams[1]}, and {fams[2]}"
    return f"{fams[0]} et al."


def _paren_item(p, style, locator):
    a = _short_authors(p, style)
    if style == "mla":
        return f"{a} {locator}".strip() if locator else a
    sep = " " if style == "chicago" else ", "
    s = f"{a}{sep}{_year(p)}"
    if locator:
        s += (", " if style != "chicago" else ", ") + locator
    return s


_CITE_GROUP = re.compile(r"\[((?:\s*-?@[^\];]+;?)+)\]")
_CITE_ITEM = re.compile(r"^\s*-?@([\w:.\-/+]+?)\s*(?:,\s*(.+?))?\s*$")
_NARRATIVE = re.compile(r"(?<![\w@\[\]/.])@([A-Za-z][\w:\-]*[A-Za-z0-9])")
_ANY_CITE = re.compile(_CITE_GROUP.pattern + "|" + _NARRATIVE.pattern)


def process_citations(md: str, library: dict, style: str):
    """Replace citation markers. Returns (text, ordered list of cited papers, missing keys)."""
    numeric = STYLES.get(style, STYLES["apa"])["numeric"]
    order: list[str] = []
    missing: list[str] = []

    def num(key):
        if key not in order:
            order.append(key)
        return order.index(key) + 1

    def group(m):
        items = []
        for raw in m.group(1).split(";"):
            im = _CITE_ITEM.match(raw)
            if not im:
                continue
            key, loc = im.group(1), (im.group(2) or "").strip()
            if key not in library:
                if key not in missing:
                    missing.append(key)
                items.append(("?", key, loc))
                continue
            items.append(("ok", key, loc))
        if not items:
            return m.group(0)
        if numeric:
            parts = []
            for st, key, loc in items:
                if st == "?":
                    parts.append(f"?{key}")
                else:
                    parts.append(f"{num(key)}" + (f", {loc}" if loc else ""))
            if style == "ieee":
                return ", ".join(f"[{x}]" for x in parts)
            return "[" + ",".join(parts) + "]"
        parts = []
        for st, key, loc in items:
            if st == "?":
                parts.append(f"?{key}")
            else:
                if key not in order:
                    order.append(key)
                parts.append(_paren_item(library[key], style, loc))
        return "(" + "; ".join(parts) + ")"

    def narrative(m):
        key = m.group(2)
        if key not in library:
            return m.group(0)
        p = library[key]
        a = _short_authors(p, style).replace(" & ", " and ")
        if numeric:
            n = num(key)
            return f"{a} [{n}]"
        if key not in order:
            order.append(key)
        if style == "mla":
            return a
        return f"{a} ({_year(p)})"

    # single pass so numeric styles number sources in order of first appearance
    text = _ANY_CITE.sub(lambda m: group(m) if m.group(1) is not None else narrative(m), md)
    return text, [library[k] for k in order], missing


def bibliography(papers: list[dict], style: str) -> list[str]:
    if STYLES.get(style, STYLES["apa"])["numeric"]:
        return [format_reference(p, style, i + 1) for i, p in enumerate(papers)]

    def sort_key(p):
        a = p.get("authors") or []
        return ((_fam(a[0]) if a else p.get("title", "")).lower(), str(p.get("year") or ""))

    return [format_reference(p, style) for p in sorted(papers, key=sort_key)]


# ---------------------------------------------------------------- BibTeX
def _bib_value(s: str, i: int):
    """Parse a BibTeX value starting at s[i]; returns (value, new_index)."""
    while i < len(s) and s[i] in " \t\r\n":
        i += 1
    if i >= len(s):
        return "", i
    if s[i] == "{":
        depth, j = 0, i
        while j < len(s):
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
                if depth == 0:
                    return s[i + 1:j], j + 1
            j += 1
        return s[i + 1:], len(s)
    if s[i] == '"':
        j = i + 1
        depth = 0
        while j < len(s):
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
            elif s[j] == '"' and depth == 0:
                return s[i + 1:j], j + 1
            j += 1
        return s[i + 1:], len(s)
    m = re.match(r"[^,}\s]+", s[i:])
    return (m.group(0), i + m.end()) if m else ("", i)


def _bib_clean(v: str) -> str:
    v = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", v)
    v = v.replace("{", "").replace("}", "").replace("\\&", "&").replace("--", "–")
    return re.sub(r"\s+", " ", v).strip()


def parse_bibtex(text: str) -> list[dict]:
    from .search import split_name
    out = []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
        etype = m.group(1).lower()
        if etype in ("comment", "string", "preamble"):
            continue
        key = m.group(2)
        i = m.end()
        fields = {}
        while i < len(text):
            fm = re.match(r"\s*([\w\-]+)\s*=\s*", text[i:])
            if not fm:
                break
            name = fm.group(1).lower()
            val, i = _bib_value(text, i + fm.end())
            fields[name] = _bib_clean(val)
            sm = re.match(r"\s*,?", text[i:])
            i += sm.end()
            if i < len(text) and text[i] == "}":
                break
        authors = [split_name(a) for a in re.split(r"\s+and\s+", fields.get("author", "")) if a.strip()]
        ptype = {"article": "article", "book": "book", "inproceedings": "conference", "conference": "conference",
                 "incollection": "chapter", "inbook": "chapter", "phdthesis": "thesis", "mastersthesis": "thesis",
                 "techreport": "report", "misc": "webpage" if fields.get("url") and not fields.get("doi") else "article",
                 "online": "webpage"}.get(etype, "article")
        year = re.search(r"\d{4}", fields.get("year", "") or fields.get("date", ""))
        out.append({
            "key": key, "title": fields.get("title", ""), "authors": authors,
            "year": int(year.group(0)) if year else None,
            "venue": fields.get("journal") or fields.get("booktitle") or fields.get("howpublished") or "",
            "volume": fields.get("volume", ""), "issue": fields.get("number", ""),
            "pages": fields.get("pages", ""), "doi": fields.get("doi", ""), "url": fields.get("url", ""),
            "publisher": fields.get("publisher") or fields.get("school") or fields.get("institution") or "",
            "abstract": fields.get("abstract", ""), "type": ptype, "source": "bibtex",
        })
    return out


def to_bibtex(papers: list[dict]) -> str:
    entries = []
    for p in papers:
        etype = {"article": "article", "book": "book", "conference": "inproceedings", "chapter": "incollection",
                 "thesis": "phdthesis", "report": "techreport", "webpage": "misc", "preprint": "misc"}.get(
            p.get("type", "article"), "article")
        venue_field = {"article": "journal", "inproceedings": "booktitle", "incollection": "booktitle"}.get(etype, "howpublished")
        fields = [
            ("author", " and ".join(
                (a.get("literal") and "{" + a["literal"] + "}") or f"{a.get('family', '')}, {a.get('given', '')}".strip(", ")
                for a in p.get("authors") or [])),
            ("title", "{" + (p.get("title") or "") + "}"),
            (venue_field, p.get("venue")), ("year", p.get("year")), ("volume", p.get("volume")),
            ("number", p.get("issue")), ("pages", (p.get("pages") or "").replace("–", "--")),
            ("publisher", p.get("publisher")), ("doi", p.get("doi")), ("url", p.get("url")),
        ]
        body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields if v)
        entries.append(f"@{etype}{{{p.get('key', 'ref')},\n{body}\n}}")
    return "\n\n".join(entries) + "\n"


def to_ris(papers: list[dict]) -> str:
    """RIS export for EndNote / Mendeley / Zotero."""
    ty = {"article": "JOUR", "book": "BOOK", "chapter": "CHAP", "conference": "CPAPER", "thesis": "THES",
          "report": "RPRT", "webpage": "ELEC", "preprint": "UNPB"}
    out = []
    for p in papers:
        lines = [f"TY  - {ty.get(p.get('type', 'article'), 'GEN')}"]
        for a in p.get("authors") or []:
            lines.append("AU  - " + (a.get("literal") or f"{a.get('family', '')}, {a.get('given', '')}".strip(", ")))
        pages = (p.get("pages") or "").replace("–", "-").split("-")
        for tag, val in (("TI", p.get("title")), ("T2", p.get("venue")), ("PY", p.get("year")), ("VL", p.get("volume")),
                         ("IS", p.get("issue")), ("SP", pages[0] if pages[0] else ""), ("EP", pages[1] if len(pages) > 1 else ""),
                         ("PB", p.get("publisher")), ("DO", p.get("doi")), ("UR", p.get("url")), ("AB", p.get("abstract")),
                         ("ID", p.get("key"))):
            if val:
                lines.append(f"{tag}  - {val}")
        lines.append("ER  - ")
        out.append("\n".join(lines))
    return "\n\n".join(out) + "\n"
