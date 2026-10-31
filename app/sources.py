"""Several sources at once: A.F.R.A documents, library papers, uploaded files and files in Tugas folders, mixed.

Used by the Create into PPT wizard (several-source decks) and by the cheatsheet maker.
  refs        what the page sends: {"type": "doc"|"paper"|"file"|"folder"|"upload", "id" | "path"}
  collect()   refs -> items [{"tag": "S1", "kind", "label", "key", "text"}] (a folder becomes its files, with a cap and a note)
  digest()    items -> one text for the AI; when the set is long, each long source is summarised first (one AI call each), then merged
Text of .pptx lecture slides is read from the slide XML directly (no PowerPoint library needed); pdf/docx/md/txt go through
the same reader as manuscripts.
"""
import io
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from . import db, tugas
from .llm import complete

SUPPORTED = (".pptx", ".pdf", ".docx", ".md", ".markdown", ".txt")
MAX_FILES = 40          # files taken from folders in one go
MAX_BYTES = 40_000_000  # a bigger file is skipped with a note
CHAR_CAP = 120_000      # text kept per source
BUDGET = 36_000         # characters the AI reads together before per-source summaries kick in
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
KINDS = ("doc", "paper", "file", "folder", "upload")


# ---------------------------------------------------------------- reading files
def pptx_text(data: bytes) -> str:
    """Lecture slides as text: '## Slide N', then every paragraph of every shape, in slide order."""
    z = zipfile.ZipFile(io.BytesIO(data))
    names = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), key=lambda n: int(re.findall(r"\d+", n)[-1]))
    out = []
    for n, name in enumerate(names, 1):
        paras = []
        for p in ET.fromstring(z.read(name)).iter(A + "p"):
            t = "".join(x.text or "" for x in p.iter(A + "t")).strip()
            if t:
                paras.append(t)
        if paras:
            out.append(f"## Slide {n}\n" + "\n".join(paras))
    return "\n\n".join(out)


def extract_text(name: str, data: bytes) -> str:
    """Text of a .pptx, .pdf, .docx, .md or .txt file; raises ValueError for anything else."""
    low = (name or "").lower()
    if not low.endswith(SUPPORTED):
        raise ValueError(f"{name}: A.F.R.A reads .pptx, .pdf, .docx, .md and .txt files.")
    if low.endswith(".pptx"):
        try:
            return pptx_text(data).strip()
        except (zipfile.BadZipFile, ET.ParseError, KeyError):
            raise ValueError(f"{name} isn't a PowerPoint file A.F.R.A can read.")
    from .gaps import manuscript_text
    try:
        return manuscript_text(name, data).strip()
    except Exception:  # a damaged pdf or docx: say so instead of failing the whole set
        raise ValueError(f"{name} couldn't be read (is it damaged or password-protected?).")


def clean_refs(refs) -> list[dict]:
    """The picks from the page, validated and without repeats."""
    if not isinstance(refs, list):
        raise ValueError("Pick the sources as a list.")
    out, seen = [], set()
    for r in refs:
        if not isinstance(r, dict) or r.get("type") not in KINDS:
            raise ValueError("One of the picked sources isn't something A.F.R.A knows.")
        key = "path" if r["type"] in ("file", "folder") else "id"
        v = r.get(key)
        if not isinstance(v, str) or (not v and r["type"] != "folder"):
            raise ValueError("One of the picked sources is missing its name.")
        if (r["type"], v) not in seen:
            seen.add((r["type"], v))
            out.append({"type": r["type"], key: v})
    return out[:200]


# ---------------------------------------------------------------- Tugas files
def tugas_files(path: str = "", cap: int = MAX_FILES) -> tuple[list[str], bool]:
    """Supported files under a Tugas folder (recursive, hidden ones skipped, sorted), and whether the cap cut the list."""
    base = tugas.root()
    top = tugas.resolve(path, base)
    if not top.is_dir():
        raise tugas.TugasError("That folder doesn't exist.")
    found, cut = [], False
    for dirpath, dirs, files in os.walk(top):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for f in sorted(files):
            if f.startswith((".", "~$")) or not f.lower().endswith(SUPPORTED):
                continue
            if len(found) >= cap:
                cut = True
                break
            found.append(tugas.rel(Path(dirpath, f).resolve(), base))
        if cut:
            break
    return found, cut


def candidates(limit: int = 600) -> dict:
    """Everything the picker can offer from Tugas: supported files and the folders that hold some."""
    files, folders = [], {}
    base = tugas.root()
    for dirpath, dirs, fs in os.walk(base):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        here = tugas.rel(Path(dirpath).resolve(), base)
        n = 0
        for f in sorted(fs):
            if f.startswith((".", "~$")) or not f.lower().endswith(SUPPORTED):
                continue
            n += 1
            if len(files) < limit:
                files.append({"path": (here + "/" + f).lstrip("/"), "name": f, "folder": here})
        if n and here:
            folders[here] = n
    return {"files": files, "folders": [{"path": p, "count": c} for p, c in sorted(folders.items())], "root": str(base)}


def expand(refs: list[dict], cap: int = MAX_FILES) -> tuple[list[dict], list[str]]:
    """Folders become the files inside them; (refs, notes). Files named twice are read once."""
    out, notes, seen = [], [], set()
    for r in refs:
        if r["type"] == "folder":
            files, cut = tugas_files(r["path"], cap)
            name = r["path"] or "All Tugas"
            notes.append(f"Folder {name}: {len(files)} file{'s' if len(files) != 1 else ''}" + (f" (only the first {cap} were taken)" if cut else "") if files
                         else f"Folder {name} has no .pptx, .pdf, .docx, .md or .txt files.")
            for f in files:
                if ("file", f) not in seen:
                    seen.add(("file", f))
                    out.append({"type": "file", "path": f})
        elif (r["type"], r.get("path") or r.get("id")) not in seen:
            seen.add((r["type"], r.get("path") or r.get("id")))
            out.append(r)
    if sum(1 for r in out if r["type"] == "file") > cap:
        keep = 0
        trimmed = []
        for r in out:
            if r["type"] == "file":
                keep += 1
                if keep > cap:
                    continue
            trimmed.append(r)
        notes.append(f"Only the first {cap} files were used.")
        out = trimmed
    return out, notes


# ---------------------------------------------------------------- collecting
def paper_text(p: dict) -> str:
    authors = ", ".join(" ".join(x for x in (a.get("given"), a.get("family")) if x) for a in (p.get("authors") or [])[:6])
    parts = [f"{p.get('title', '')}", f"{authors} ({p.get('year') or 'n.d.'}), {p.get('venue') or ''}", "Abstract: " + (p.get("abstract") or "")]
    if p.get("notes"):
        parts.append("My notes: " + p["notes"])
    if p.get("fulltext"):
        parts.append("Full text:\n" + p["fulltext"])
    return "\n".join(parts)


def collect(refs: list[dict], materials: list[dict] | None = None) -> tuple[list[dict], list[str]]:
    """(items, notes). A source that can't be read is skipped with a note; nothing readable at all raises ValueError."""
    refs, notes = expand(refs)
    mats = {m["id"]: m for m in materials or []}
    items = []

    def add(kind, label, text, key=""):
        text = (text or "").strip()
        if text:
            items.append({"tag": f"S{len(items) + 1}", "kind": kind, "label": label[:150], "key": key, "text": text[:CHAR_CAP]})
        else:
            notes.append(f"{label}: no text found (a scanned PDF has no text to read).")

    for r in refs:
        t = r["type"]
        if t == "doc":
            d = db.get_document(r["id"])
            if d:
                add("doc", d["title"], d.get("content"))
            else:
                notes.append("A document you picked no longer exists.")
        elif t == "paper":
            p = db.get_paper(r["id"], with_fulltext=True)
            if p:
                add("paper", (p.get("title") or "Untitled")[:150], paper_text(p), p.get("key") or "")
            else:
                notes.append("A paper you picked is no longer in the library.")
        elif t == "upload":
            m = mats.get(r["id"])
            if m:
                add("upload", m["name"], m["text"])
        elif t == "file":
            try:
                p = tugas.resolve(r["path"])
                if not p.is_file():
                    raise tugas.TugasError("it no longer exists")
                if p.stat().st_size > MAX_BYTES:
                    raise ValueError("it is bigger than 40 MB")
                add("file", p.name, extract_text(p.name, p.read_bytes()))
            except (tugas.TugasError, ValueError, OSError) as e:
                notes.append(f"{os.path.basename(r['path'])} was skipped: {e}")
    for m in materials or []:  # uploads that belong to the draft but were not listed as refs
        if not any(r["type"] == "upload" and r["id"] == m["id"] for r in refs):
            add("upload", m["name"], m["text"])
    if not items:
        raise ValueError("None of the picked sources has text A.F.R.A can read." + (" " + " ".join(notes[:3]) if notes else ""))
    return items, notes


def source_text(it: dict) -> str:
    """What a slide or cheatsheet item records as its source: [@key] for a library paper (so it is cited), else the name."""
    return f"[@{it['key']}]" if it["kind"] == "paper" and it.get("key") else it["label"]


def map_tags(ids, items: list[dict]) -> list[dict]:
    by = {i["tag"]: i for i in items}
    return [by[t] for t in dict.fromkeys(str(x).strip().upper() for x in (ids if isinstance(ids, list) else [ids])) if t in by]


# ---------------------------------------------------------------- reading them together
async def digest(items: list[dict], budget: int = BUDGET, language: str = "") -> str:
    """One text with a tagged block per source. When the set is longer than `budget` characters, each long source is
    summarised on its own first (numbers, definitions, names and dates kept), then the blocks are merged."""
    total = sum(len(i["text"]) for i in items)
    share = max(1500, budget // max(1, len(items)))
    blocks = []
    for it in items:
        text = it["text"]
        if total > budget and len(text) > share:
            text = (await complete(
                f"""Summarise this source for someone who will build slides or a study sheet from it.
Keep every definition, formula, number, date, name, step and example that matters; drop filler. Plain text, no preamble.
Write at most {share // 6} words{(' in ' + language) if language else ''}.

<source name="{it['label']}">
{text[:60000]}
</source>""", "You condense study material faithfully. Never add facts that are not in the source.", task="sources-summary")).strip()[:int(share * 1.3)] or text[:share]
        blocks.append(f"### [{it['tag']}] {it['label']}  ({'library paper, cite as ' + source_text(it) if it['kind'] == 'paper' and it.get('key') else it['kind']})\n{text[:max(share, budget) if total <= budget else int(share * 1.3)]}")
    out = "\n\n".join(blocks)
    return out[:int(budget * 1.4) + 400 * len(items)]


def label(r: dict) -> dict:
    """The chip for one picked source: what it is called and whether it still exists."""
    t = r["type"]
    if t == "doc":
        d = db.get_document(r["id"])
        name = d["title"] if d else ""
    elif t == "paper":
        p = db.get_paper(r["id"], with_fulltext=False)
        name = (p.get("title") or "Untitled") if p else ""
    elif t in ("file", "folder"):
        name = (r["path"].rsplit("/", 1)[-1] if r["path"] else "All Tugas") + ("/" if t == "folder" else "")
    else:
        name = ""
    return {**r, "name": name or "(no longer available)", "ok": bool(name)}
