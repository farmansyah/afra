"""Shared library: two or more A.F.R.A installations share everything through a Google Drive or iCloud Drive folder.

The database never lives in the cloud folder (cloud sync corrupts SQLite). Instead every change becomes one small
JSON file per record in <shared folder>/records/<table>/<id>.json, plus the PDFs in <shared folder>/pdfs/. Each
copy of A.F.R.A writes its own changes there and reads the others' every few seconds.

- Changes are captured by SQLite triggers, so every write path is covered (library, documents, merges, imports...).
- Newest change wins per record. For documents, if both people edited the same one before seeing each other's
  version, the local version is kept as a "conflict copy" document instead of being lost.
- Deletions travel as tombstone records. A backup is made before the first sync.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

from . import db
from .config import DATA_DIR, get_settings, save_settings
from .version import VERSION

TABLES = {"projects": "id", "papers": "id", "documents": "id", "runs": "id", "collections": "id", "quotes": "id",
          "paper_collections": ("paper_id", "collection_id"), "messages": "id"}  # messages: notes between the two of you
MARKER = "afra-shared.json"
DEFAULT_FOLDER = "AFRA Shared Library"
STATUS = {"last_sync": 0, "last_error": "", "imported": 0, "exported": 0, "imported_from": ""}

SETUP = """
CREATE TABLE IF NOT EXISTS sync_log (seq INTEGER PRIMARY KEY AUTOINCREMENT, tbl TEXT, rid TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS sync_state (tbl TEXT, rid TEXT, ts REAL, device TEXT, PRIMARY KEY (tbl, rid));
CREATE TABLE IF NOT EXISTS sync_files (path TEXT PRIMARY KEY, mtime REAL, size INTEGER);
CREATE TABLE IF NOT EXISTS sync_kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS sync_flag (applying INTEGER);
"""
NOW = "(julianday('now') - 2440587.5) * 86400.0"


def _rid(tbl, ref):
    pk = TABLES[tbl]
    return f"{ref}.{pk}" if isinstance(pk, str) else f"{ref}.{pk[0]} || '~' || {ref}.{pk[1]}"


# ---------------------------------------------------------------- device identity
def device() -> dict:
    f = DATA_DIR / "device.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    name = socket.gethostname().split(".")[0]
    if sys.platform == "darwin":
        try:
            name = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True, timeout=5).stdout.strip() or name
        except Exception:
            pass
    d = {"id": uuid.uuid4().hex[:12], "name": name}
    f.write_text(json.dumps(d), encoding="utf-8")
    return d


# ---------------------------------------------------------------- where the cloud folders are
def provider_roots() -> dict:
    home = Path.home()
    out = {"gdrive": [], "icloud": []}
    if sys.platform == "darwin":
        out["icloud"] = [home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"]
        cs = home / "Library" / "CloudStorage"
        if cs.exists():
            out["gdrive"] = [d / "My Drive" for d in sorted(cs.glob("GoogleDrive-*"))]
    elif os.name == "nt":
        out["icloud"] = [home / "iCloudDrive"]
        out["gdrive"] = [Path(f"{l}:/My Drive") for l in "DEFGHIJKLMNOPQRSTUVWXYZ"] + [home / "My Drive", home / "Google Drive"]
    out = {k: [str(p) for p in v if p.is_dir()] for k, v in out.items()}
    return out


def folders(provider: str) -> list[dict]:
    """Folders at the top of the user's drive; shared A.F.R.A libraries first."""
    res = []
    for root in provider_roots().get(provider, []):
        try:
            for d in sorted(Path(root).iterdir(), key=lambda p: p.name.lower()):
                if d.is_dir() and not d.name.startswith("."):
                    res.append({"path": str(d), "name": d.name, "afra": (d / MARKER).exists() or (d / f".{MARKER}.icloud").exists()})
        except OSError as e:
            raise ValueError(_permission_help(e))
    return sorted(res, key=lambda f: not f["afra"])


def _permission_help(e: Exception) -> str:
    if isinstance(e, PermissionError) and sys.platform == "darwin":
        return ("macOS did not let A.F.R.A open this folder. Open System Settings > Privacy & Security > Full Disk Access, "
                "click +, press Cmd+Shift+G, type ~/AFRA/.venv/bin/ and add python3. Then try again.")
    return str(e)


# ---------------------------------------------------------------- switching on and off
def _triggers(c, on: bool):
    for tbl in TABLES:
        for op in ("INSERT", "UPDATE", "DELETE"):
            c.execute(f"DROP TRIGGER IF EXISTS sync_{tbl}_{op.lower()}")
            if on:
                ref = "OLD" if op == "DELETE" else "NEW"
                c.execute(f"""CREATE TRIGGER sync_{tbl}_{op.lower()} AFTER {op} ON {tbl}
                    WHEN (SELECT applying FROM sync_flag) = 0
                    BEGIN INSERT INTO sync_log (tbl, rid, ts) VALUES ('{tbl}', {_rid(tbl, ref)}, {NOW}); END""")


def init():
    with db.conn() as c:
        c.executescript(SETUP)
        if not c.execute("SELECT 1 FROM sync_flag").fetchone():
            c.execute("INSERT INTO sync_flag VALUES (0)")
        c.execute("UPDATE sync_flag SET applying=0")  # a crash mid-import must not silence the triggers
        _triggers(c, enabled())


def enabled() -> bool:
    return bool(get_settings().get("sync_path"))


def enable(provider: str, path: str = "", name: str = "") -> dict:
    if not path:
        roots = provider_roots().get(provider) or []
        if not roots:
            raise ValueError({"gdrive": "Google Drive for desktop is not installed or not signed in on this computer.",
                              "icloud": "iCloud Drive is not set up on this computer."}.get(provider, "Unknown provider"))
        path = str(Path(roots[0]) / (name.strip() or DEFAULT_FOLDER))
    folder = Path(path)
    try:
        for sub in ("records", "pdfs", "devices"):
            (folder / sub).mkdir(parents=True, exist_ok=True)
        if not (folder / MARKER).exists():
            (folder / MARKER).write_text(json.dumps({"app": "A.F.R.A", "created": time.time()}), encoding="utf-8")
    except OSError as e:
        raise ValueError(_permission_help(e))
    db.backup("before-sync")
    joining = any((folder / "records" / "projects").glob("*.json"))
    with db.conn() as c:
        if joining:  # don't add this computer's untouched starter project to someone else's library
            c.execute("""DELETE FROM projects WHERE name='My First Project'
                         AND id NOT IN (SELECT project_id FROM papers UNION SELECT project_id FROM documents
                                        UNION SELECT project_id FROM runs)""")
        _triggers(c, True)
        c.execute("DELETE FROM sync_files")  # read everything that is already in the folder
        for tbl in TABLES:  # share everything that exists now
            c.execute(f"INSERT INTO sync_log (tbl, rid, ts) SELECT '{tbl}', {_rid(tbl, tbl)}, {NOW} FROM {tbl}")
    save_settings({"sync_provider": provider, "sync_path": str(folder)})
    return run_once()


def disable():
    with db.conn() as c:
        _triggers(c, False)
        c.execute("DELETE FROM sync_log")
    save_settings({"sync_provider": "", "sync_path": ""})
    return status()


# ---------------------------------------------------------------- one round: send ours, then fetch theirs
def _atomic_write(path: Path, data: bytes):
    tmp = path.with_name(f".~{path.name}.{uuid.uuid4().hex[:6]}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _row(c, tbl, rid):
    pk = TABLES[tbl]
    if isinstance(pk, str):
        r = c.execute(f"SELECT * FROM {tbl} WHERE {pk}=?", (rid,)).fetchone()
    else:
        r = c.execute(f"SELECT * FROM {tbl} WHERE {pk[0]}=? AND {pk[1]}=?", tuple(rid.split("~", 1))).fetchone()
    return dict(r) if r else None


def _export(folder: Path, me: dict) -> int:
    with db.conn() as c:
        last = int((c.execute("SELECT v FROM sync_kv WHERE k='seq'").fetchone() or ["0"])[0])
        rows = c.execute("SELECT seq, tbl, rid, ts FROM sync_log WHERE seq>? ORDER BY seq", (last,)).fetchall()
        if not rows:
            return 0
        latest = {}
        for r in rows:
            latest[(r["tbl"], r["rid"])] = r["ts"]
        for (tbl, rid), ts in latest.items():
            st = c.execute("SELECT ts FROM sync_state WHERE tbl=? AND rid=?", (tbl, rid)).fetchone()
            row = _row(c, tbl, rid)
            rec = {"table": tbl, "id": rid, "ts": ts, "prev": st["ts"] if st else None, "device": me["id"],
                   "device_name": me["name"], "deleted": row is None, "row": row}
            (folder / "records" / tbl).mkdir(parents=True, exist_ok=True)
            _atomic_write(folder / "records" / tbl / f"{rid}.json", json.dumps(rec, ensure_ascii=False).encode("utf-8"))
            c.execute("INSERT OR REPLACE INTO sync_state VALUES (?,?,?,?)", (tbl, rid, ts, me["id"]))
        c.execute("INSERT OR REPLACE INTO sync_kv VALUES ('seq', ?)", (str(rows[-1]["seq"]),))
    return len(latest)


def _columns(c, tbl):
    return [r[1] for r in c.execute(f"PRAGMA table_info({tbl})")]


def _apply(c, rec: dict, me: dict) -> bool:
    tbl, rid = rec["table"], rec["id"]
    if tbl not in TABLES:
        return False
    st = c.execute("SELECT ts, device FROM sync_state WHERE tbl=? AND rid=?", (tbl, rid)).fetchone()
    if st and rec["ts"] <= st["ts"]:
        return False
    local = _row(c, tbl, rid)
    if (tbl == "documents" and local and not rec["deleted"] and st and st["device"] == me["id"]
            and rec.get("prev") != st["ts"] and (local["content"] or "") != (rec["row"].get("content") or "")):
        # both edited it: keep ours as a separate document (created normally, so it is shared too)
        c.execute("UPDATE sync_flag SET applying=0")
        stamp = time.strftime("%d %b %H:%M")
        c.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)",
                  (db.new_id(), local["project_id"], local["kind"], f"{local['title']} (conflict copy, {me['name']}, {stamp})",
                   local["content"], local["meta"], time.time(), time.time()))
        c.execute("UPDATE sync_flag SET applying=1")
    pk = TABLES[tbl]
    if rec["deleted"]:
        if isinstance(pk, str):
            c.execute(f"DELETE FROM {tbl} WHERE {pk}=?", (rid,))
        else:
            c.execute(f"DELETE FROM {tbl} WHERE {pk[0]}=? AND {pk[1]}=?", tuple(rid.split("~", 1)))
    else:
        cols = [k for k in _columns(c, tbl) if k in rec["row"]]
        c.execute(f"INSERT OR REPLACE INTO {tbl} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                  [rec["row"][k] for k in cols])
    c.execute("INSERT OR REPLACE INTO sync_state VALUES (?,?,?,?)", (tbl, rid, rec["ts"], rec["device"]))
    return True


def _import(folder: Path, me: dict) -> int:
    records = folder / "records"
    placeholders, changed = [], []
    with db.conn() as c:
        seen = {r["path"]: (r["mtime"], r["size"]) for r in c.execute("SELECT * FROM sync_files")}
    for tbl_dir in (records.iterdir() if records.exists() else []):
        if not tbl_dir.is_dir():
            continue
        for e in os.scandir(tbl_dir):
            if e.name.endswith(".icloud"):  # iCloud has not downloaded this one yet
                placeholders.append(tbl_dir)
                continue
            if not e.name.endswith(".json") or e.name.startswith("."):
                continue
            stt = e.stat()
            rel = f"{tbl_dir.name}/{e.name}"
            if seen.get(rel) != (stt.st_mtime, stt.st_size):
                changed.append((rel, Path(e.path), stt))
    if placeholders and sys.platform == "darwin":
        for d in set(placeholders):
            subprocess.run(["brctl", "download", str(d)], capture_output=True, timeout=60)
    applied, names = 0, set()
    order = list(TABLES)  # parents (projects) before children
    changed.sort(key=lambda x: order.index(x[0].split("/")[0]) if x[0].split("/")[0] in order else 99)
    with db.conn() as c:
        c.execute("UPDATE sync_flag SET applying=1")
        try:
            for rel, path, stt in changed:
                try:
                    rec = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue  # still being written by the cloud app: next round
                if rec.get("device") != me["id"] and _apply(c, rec, me):
                    applied += 1
                    names.add(rec.get("device_name") or "the other computer")
                c.execute("INSERT OR REPLACE INTO sync_files VALUES (?,?,?)", (rel, stt.st_mtime, stt.st_size))
        finally:
            c.execute("UPDATE sync_flag SET applying=0")
    if applied:
        STATUS["imported"] += applied
        STATUS["imported_from"] = ", ".join(sorted(names))
    return applied


def _pdfs(folder: Path):
    shared, local = folder / "pdfs", db.PDF_DIR
    shared.mkdir(parents=True, exist_ok=True)
    local.mkdir(parents=True, exist_ok=True)
    theirs = {e.name: e.stat().st_mtime for e in os.scandir(shared) if e.name.endswith(".pdf") and not e.name.startswith(".")}
    ours = {e.name: e.stat().st_mtime for e in os.scandir(local) if e.name.endswith(".pdf")}
    for name, mtime in ours.items():  # new or replaced here
        if name not in theirs or mtime > theirs[name] + 2:
            tmp = shared / f".~{name}"
            shutil.copy2(local / name, tmp)  # copy2 keeps the time, so the other side doesn't send it back
            os.replace(tmp, shared / name)
    for name, mtime in theirs.items():  # new or replaced there
        if name not in ours or mtime > ours[name] + 2:
            tmp = local / f"{name}.tmp"
            shutil.copy2(shared / name, tmp)
            os.replace(tmp, local / name)


def _heartbeat(folder: Path, me: dict):
    beat = folder / "devices" / f"{me['id']}.json"
    try:
        if time.time() - beat.stat().st_mtime < 300:
            return
    except OSError:
        pass
    beat.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(beat, json.dumps({"name": me["name"], "seen": time.time(), "version": VERSION}).encode())


def run_once() -> dict:
    path = get_settings().get("sync_path")
    if not path:
        return status()
    folder, me = Path(path), device()
    try:
        if not (folder / MARKER).exists() and not (folder / f".{MARKER}.icloud").exists():
            raise ValueError(f"The shared folder is missing or not synced yet: {folder}")
        STATUS["exported"] += _export(folder, me)
        _import(folder, me)
        _pdfs(folder)
        _heartbeat(folder, me)
        STATUS.update(last_sync=time.time(), last_error="")
    except Exception as e:
        STATUS["last_error"] = _permission_help(e)
    return status()


def status() -> dict:
    s = get_settings()
    path = s.get("sync_path") or ""
    others = []
    if path:
        me = device()["id"]
        try:
            for f in (Path(path) / "devices").glob("*.json"):
                if f.stem != me:
                    d = json.loads(f.read_text(encoding="utf-8"))
                    others.append({"name": d.get("name"), "seen": d.get("seen"), "version": d.get("version")})
        except (OSError, ValueError):
            pass
    with db.conn() as c:
        last = int((c.execute("SELECT v FROM sync_kv WHERE k='seq'").fetchone() or ["0"])[0])
        pending = c.execute("SELECT COUNT(DISTINCT tbl || rid) FROM sync_log WHERE seq>?", (last,)).fetchone()[0]
    return {"enabled": bool(path), "path": path, "provider": s.get("sync_provider") or "", "device": device(),
            "others": others, "pending": pending, "roots": provider_roots(), **STATUS}
