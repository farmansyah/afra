"""Tugas: real folders on disk for every assignment (default Documents/A.F.R.A Tugas, changeable in Settings).

Every path that comes from the browser is relative to the root and is checked by resolve(): no "..", no absolute
paths or drive letters, nothing that a symlink leads out of the root. A folder's due date, note and linked A.F.R.A
documents/papers live in a hidden .afra.json inside it, so they travel with the folder. Deleting only ever moves to
the Recycle Bin (send2trash).
"""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

from send2trash import send2trash

from . import db
from .config import documents_dir, get_settings

META = ".afra.json"
BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
# files that would run as programs if "opened": listed in the folder, but only revealed, never launched
PROGRAMS = {".exe", ".bat", ".cmd", ".com", ".msi", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".jar", ".scr", ".lnk",
            ".hta", ".reg", ".cpl", ".sh", ".command", ".app", ".appimage", ".dll", ".url"}


class TugasError(ValueError):
    pass


def default_dir() -> Path:
    return documents_dir() / "A.F.R.A Tugas"


def root() -> Path:
    p = Path((get_settings().get("tugas_dir") or "").strip() or default_dir()).expanduser()
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise TugasError(f"A.F.R.A can't use the Tugas folder {p} ({e.strerror or e}). Choose another one in Settings › Tugas folder.")
    return p.resolve()


def resolve(path, base: Path | None = None) -> Path:
    """The real path for a client path, or TugasError when it is not strictly inside the root."""
    base = base or root()
    if path is None:
        path = ""
    if not isinstance(path, str):
        raise TugasError("Bad path.")
    s = path.replace("\\", "/")
    if "\x00" in s or s.startswith("/") or re.match(r"^[A-Za-z]:", s):
        raise TugasError("That path is outside the Tugas folder.")
    parts = [x for x in s.split("/") if x not in ("", ".")]
    if ".." in parts:
        raise TugasError("That path is outside the Tugas folder.")
    p = base.joinpath(*parts).resolve()
    if p != base and base not in p.parents:  # a symlink or junction leading out
        raise TugasError("That path is outside the Tugas folder.")
    return p


def rel(p: Path, base: Path | None = None) -> str:
    base = base or root()
    return "" if p == base else p.relative_to(base).as_posix()


def clean_name(name) -> str:
    n = BAD_CHARS.sub("", str(name or "")).strip().rstrip(". ")
    if not n or n in (".", "..") or n.lower() == META:
        raise TugasError('Give it a name (no \\ / : * ? " < > |).')
    return n[:150]


def unique(folder: Path, name: str) -> Path:
    """folder/name, or "name (2)" etc. when that is taken."""
    if not (folder / name).exists():
        return folder / name
    stem, ext = os.path.splitext(name)
    if not stem:
        stem, ext = name, ""
    i = 2
    while (folder / f"{stem} ({i}){ext}").exists():
        i += 1
    return folder / f"{stem} ({i}){ext}"


# ---------------------------------------------------------------- folder metadata
def read_meta(folder: Path) -> dict:
    try:
        d = json.loads((folder / META).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    return {"due": d.get("due") or "", "note": d.get("note") or "", "docs": list(d.get("docs") or []),
            "papers": list(d.get("papers") or [])}


def write_meta(folder: Path, meta: dict):
    meta = {k: meta[k] for k in ("due", "note", "docs", "papers") if meta.get(k)}
    f = folder / META
    if not meta:
        f.unlink(missing_ok=True)
        return
    tmp = folder / (META + ".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, f)


def set_meta(path, due=None, note=None, docs=None, papers=None) -> dict:
    p = resolve(path)
    if not p.is_dir():
        raise TugasError("Pick a folder.")
    m = read_meta(p)
    if due is not None:
        due = str(due).strip()
        if due:
            try:
                date.fromisoformat(due)
            except ValueError:
                raise TugasError("The due date should look like 2026-11-30.")
        m["due"] = due
    if note is not None:
        m["note"] = str(note).strip()[:2000]
    if docs is not None:
        m["docs"] = [str(x) for x in dict.fromkeys(docs)]
    if papers is not None:
        m["papers"] = [str(x) for x in dict.fromkeys(papers)]
    write_meta(p, m)
    return read_meta(p)


def links(meta: dict) -> list[dict]:
    """The linked documents and papers with their titles; ones deleted from A.F.R.A are skipped."""
    out = []
    for i in meta["docs"]:
        d = db.get_document(i)
        if d:
            out.append({"type": "doc", "id": i, "title": d["title"], "project_id": d["project_id"]})
    for i in meta["papers"]:
        p = db.get_paper(i, with_fulltext=False)
        if p:
            out.append({"type": "paper", "id": i, "title": p.get("title") or "Untitled", "project_id": p["project_id"]})
    return out


# ---------------------------------------------------------------- reading
def list_dir(path="") -> dict:
    base = root()
    p = resolve(path, base)
    if not p.is_dir():
        raise TugasError("That folder doesn't exist.")
    here = rel(p, base)
    items = []
    with os.scandir(p) as it:
        for e in it:
            if e.name.startswith("."):
                continue
            try:
                st = e.stat()
                is_dir = e.is_dir()
            except OSError:
                continue
            item = {"name": e.name, "path": (here + "/" + e.name).lstrip("/"), "kind": "folder" if is_dir else "file",
                    "size": 0 if is_dir else st.st_size, "modified": st.st_mtime}
            if is_dir:
                m = read_meta(Path(e.path))
                item.update(due=m["due"], note=m["note"], linked=len(m["docs"]) + len(m["papers"]))
            else:
                item["ext"] = os.path.splitext(e.name)[1].lower().lstrip(".")
            items.append(item)
    items.sort(key=lambda x: (x["kind"] != "folder", x["name"].lower()))
    meta = read_meta(p)
    parts = here.split("/") if here else []
    return {"path": here, "root": str(base), "items": items, "meta": meta, "links": links(meta),
            "crumbs": [{"name": c, "path": "/".join(parts[:i + 1])} for i, c in enumerate(parts)]}


def tree(limit=3000) -> dict:
    """All folders (no files), nested, for the left-hand tree."""
    base = root()
    count = [0]

    def walk(p: Path, here: str) -> list:
        out = []
        try:
            entries = sorted((e for e in os.scandir(p) if not e.name.startswith(".") and e.is_dir()), key=lambda e: e.name.lower())
        except OSError:
            return out
        for e in entries:
            if count[0] >= limit:
                break
            count[0] += 1
            path = (here + "/" + e.name).lstrip("/")
            out.append({"name": e.name, "path": path, "children": walk(Path(e.path), path)})
        return out
    return {"root": str(base), "children": walk(base, "")}


def upcoming() -> list[dict]:
    """Folders with a due date, soonest first (overdue ones too)."""
    base = root()
    out = []
    for dirpath, dirs, _ in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        p = Path(dirpath)
        m = read_meta(p)
        if m["due"] and p != base:
            out.append({"name": p.name, "path": rel(p.resolve(), base), "due": m["due"], "note": m["note"]})
    out.sort(key=lambda x: x["due"])
    return out


def search(q: str, limit=200) -> list[dict]:
    q = (q or "").strip().lower()
    if not q:
        return []
    base = root()
    out = []
    for dirpath, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name, kind in [(d, "folder") for d in dirs] + [(f, "file") for f in files]:
            if name.startswith(".") or q not in name.lower():
                continue
            out.append({"name": name, "path": rel(Path(dirpath, name).resolve(), base), "kind": kind,
                        "parent": rel(Path(dirpath).resolve(), base),
                        "ext": os.path.splitext(name)[1].lower().lstrip(".") if kind == "file" else ""})
            if len(out) >= limit:
                return out
    return out


# ---------------------------------------------------------------- changing
def mkdir(path, name) -> dict:
    parent = resolve(path)
    if not parent.is_dir():
        raise TugasError("That folder doesn't exist.")
    p = parent / clean_name(name)
    if p.exists():
        raise TugasError("There is already something with that name here.")
    p.mkdir()
    return {"path": rel(p.resolve())}


def rename(path, name) -> dict:
    p = resolve(path)
    if p == root() or not p.exists():
        raise TugasError("Pick something to rename.")
    new = p.parent / clean_name(name)
    if new.exists() and new.name.lower() != p.name.lower():
        raise TugasError("There is already something with that name here.")
    os.replace(p, new)
    return {"path": rel(new.resolve())}


def move(paths, to) -> list[str]:
    dest = resolve(to)
    if not dest.is_dir():
        raise TugasError("Move it into a folder.")
    out = []
    for path in paths:
        src = resolve(path)
        if src == root() or not src.exists():
            raise TugasError("Pick something to move.")
        if src == dest or src in dest.parents:
            raise TugasError("A folder can't go inside itself.")
        if src.parent == dest:
            out.append(rel(src))
            continue
        target = unique(dest, src.name)
        shutil.move(str(src), str(target))
        out.append(rel(target.resolve()))
    return out


def delete(paths) -> int:
    """To the Recycle Bin, never for good."""
    n = 0
    for path in paths:
        p = resolve(path)
        if p == root():
            raise TugasError("The Tugas folder itself can't be deleted.")
        if not p.exists():
            continue
        send2trash(str(p))
        n += 1
    return n


def save_upload(path, name, fileobj) -> str:
    folder = resolve(path)
    if not folder.is_dir():
        raise TugasError("That folder doesn't exist.")
    target = unique(folder, clean_name(os.path.basename(str(name).replace("\\", "/"))))
    with open(target, "wb") as f:
        shutil.copyfileobj(fileobj, f)
    return rel(target.resolve())


def save_bytes(path, name, data: bytes) -> str:
    """Write a file made by A.F.R.A into a folder ("name (2)" when taken); returns its path relative to the root."""
    folder = resolve(path)
    if not folder.is_dir():
        raise TugasError("That folder doesn't exist.")
    target = unique(folder, clean_name(name))
    target.write_bytes(data)
    return rel(target.resolve())


def open_item(path) -> None:
    p = resolve(path)
    if not p.exists():
        raise TugasError("That doesn't exist any more.")
    if p.is_file() and p.suffix.lower() in PROGRAMS:
        raise TugasError("A.F.R.A won't launch programs. Use Show in File Explorer instead.")
    launch(p)


def launch(p: Path) -> None:
    """Open a file or folder in the computer's default app (callers have checked the path)."""
    if sys.platform == "win32":
        os.startfile(str(p))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p)])


def reveal(path) -> None:
    p = resolve(path)
    if not p.exists():
        raise TugasError("That doesn't exist any more.")
    if sys.platform == "win32":
        subprocess.Popen(["explorer", f"/select,{p}"] if p != root() else ["explorer", str(p)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p if p.is_dir() else p.parent)])
