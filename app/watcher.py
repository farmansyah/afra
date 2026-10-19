"""The Downloads watcher: a template the user downloads in their own browser lands in the Designs folder by itself.

It looks at the user's Downloads folder every few seconds and only at files that APPEAR after it started (the files already
there are never touched or swept). A file counts when it is a .pptx or .potx, is complete (not a .crdownload / .tmp / ~$ file, and
its size has stopped changing) and is a real PowerPoint (a zip with ppt/presentation.xml). Anything else is left alone.
A good file is MOVED (default) or COPIED into the Designs folder with a sidecar. On Windows the browser leaves the page the file came from in the
file's Zone.Identifier stream (ReferrerUrl / HostUrl): from it the sidecar gets the site and the credit its free licence asks for
(GreatPPT, Slidesgo, SlidesCarnival...). The page shows a toast and refreshes the Designs list.
"""
import hashlib
import io
import json
import os
import re
import sys
import threading
import time
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from . import designs, template_import as ti
from .config import get_settings

EXTS = (".pptx", ".potx")
STABLE_SECS = 3.0          # the size and time of the file must stay the same this long
POLL_SECS = 4
MAX_BYTES = ti.MAX_PPTX
MAX_EVENTS = 50
POTX_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.template.main+xml"
PPTX_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"


def _now() -> float:
    return time.time()  # tests move this clock


# ---------------------------------------------------------------- settings and folders
def _on(v) -> bool:
    return v if isinstance(v, bool) else str(v).strip().lower() not in ("false", "0", "no", "off", "")


def enabled() -> bool:
    return _on(get_settings().get("downloads_watch", True))


def action() -> str:
    return "copy" if str(get_settings().get("downloads_action") or "move").lower() == "copy" else "move"


def known_downloads() -> Path:
    """The Downloads folder File Explorer shows (it can be moved to another drive or into OneDrive); ~/Downloads elsewhere."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

            fid = GUID(0x374DE290, 0x123F, 0x4565, (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))  # FOLDERID_Downloads
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(out)) == 0 and out.value:
                p = Path(out.value)
                ctypes.windll.ole32.CoTaskMemFree(out)
                return p
        except (OSError, AttributeError):
            pass
    return Path.home() / "Downloads"


def folder() -> Path:
    custom = str(get_settings().get("downloads_dir") or "").strip()
    return Path(custom).expanduser() if custom else known_downloads()


# ---------------------------------------------------------------- where a file came from
def _read_ads(path: Path) -> str:
    """The text of the file's Zone.Identifier alternate data stream (Windows); '' when there is none."""
    if sys.platform != "win32":
        return ""
    try:
        with open(str(path) + ":Zone.Identifier", encoding="utf-8", errors="replace") as f:
            return f.read(8192)
    except OSError:
        return ""


def parse_zone(text: str) -> dict:
    out = {}
    for line in (text or "").splitlines():
        k, _, v = line.partition("=")
        if k.strip() in ("HostUrl", "ReferrerUrl") and v.strip():
            out[k.strip()] = v.strip()
    return out


def source_of(zone: dict) -> dict:
    """{"kind", "site", "url", "attribution"} from the browser's note of where the file was downloaded. kind "" when unknown. Pure."""
    best = ""
    for key in ("ReferrerUrl", "HostUrl"):  # the page the user was on says more than the file server
        u = zone.get(key, "")
        host = (urlparse(u).hostname or "").lower()
        if not host or urlparse(u).scheme not in ("http", "https"):
            continue
        kind = ti.classify(f"https://{host}/")
        if kind not in ("other", "file"):
            return {"kind": kind, "site": ti.SITE_NAME.get(kind, host), "url": u, "attribution": ti.CREDIT.get(kind, "")}
        best = best or u
    if best:
        host = urlparse(best).hostname or ""
        return {"kind": "", "site": host, "url": best, "attribution": ""}
    return {"kind": "", "site": "", "url": "", "attribution": ""}


# ---------------------------------------------------------------- checking a file
def potx_to_pptx(data: bytes) -> bytes:
    """A PowerPoint template (.potx) as a normal presentation: only its content type changes, so PowerPoint opens it as a deck."""
    zin = zipfile.ZipFile(io.BytesIO(data))
    ct = zin.read("[Content_Types].xml").decode("utf-8")
    if POTX_TYPE not in ct:
        return data
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i in zin.infolist():
            z.writestr(i, ct.replace(POTX_TYPE, PPTX_TYPE).encode("utf-8") if i.filename == "[Content_Types].xml" else zin.read(i.filename))
    return buf.getvalue()


def usable_name(name: str) -> bool:
    n = name.lower()
    return n.endswith(EXTS) and not n.startswith(("~$", "."))


class Watcher:
    def __init__(self):
        self.active, self.folder, self.started = False, None, 0.0
        self.baseline: set[str] = set()
        self.seen: dict[str, tuple] = {}
        self.done: set[str] = set()
        self.events: list[dict] = []
        self.lock = threading.RLock()

    # --- bookkeeping
    def _begin(self, folder: Path):
        self.folder, self.started, self.active = folder, _now(), True
        self.seen.clear()
        self.done.clear()
        try:
            self.baseline = {e.path for e in os.scandir(folder) if e.is_file() and usable_name(e.name)}  # whatever is there now is old
        except OSError:
            self.baseline = set()

    def _new(self, st: os.stat_result) -> bool:
        born = getattr(st, "st_birthtime", None) or (st.st_ctime if sys.platform == "win32" else 0)
        return max(st.st_mtime, born) >= self.started - 2

    # --- one look at the folder
    def tick(self) -> list[dict]:
        with self.lock:
            if not enabled():
                self.active = False
                return []
            f = folder()
            try:
                if f.resolve() == designs.folder().resolve():
                    return []  # the Designs folder is not a download folder
            except OSError:
                return []
            if not f.is_dir():
                return []
            if not self.active or f != self.folder:
                self._begin(f)
                return []
            added, now = [], _now()
            try:
                entries = [e for e in os.scandir(f) if e.is_file() and usable_name(e.name)]
            except OSError:
                return []
            gone = set(self.baseline) - {e.path for e in entries}
            self.baseline -= gone  # a name that was deleted and comes back is a new file
            for e in entries:
                if e.path in self.baseline or e.path in self.done:
                    continue
                if os.path.exists(e.path + ".crdownload") or os.path.exists(e.path + ".part"):
                    continue
                try:
                    st = e.stat()
                except OSError:
                    continue
                if not self._new(st):
                    self.done.add(e.path)
                    continue
                key = (st.st_size, st.st_mtime_ns)
                prev = self.seen.get(e.path)
                if prev is None or prev[0] != key:
                    self.seen[e.path] = (key, now)
                    continue
                if now - prev[1] < STABLE_SECS:
                    continue
                ev = self._take(Path(e.path))
                if ev:
                    added.append(ev)
            return added

    # --- one finished file
    def _take(self, path: Path) -> dict | None:
        try:
            if path.stat().st_size > MAX_BYTES:
                self.done.add(str(path))
                return None
            data = path.read_bytes()
        except OSError:
            return None  # the browser still holds it: try again next time
        self.done.add(str(path))
        if not ti.is_pptx(data):
            return None  # not a real PowerPoint: left exactly where it is
        try:
            if path.suffix.lower() == ".potx":
                data = potx_to_pptx(data)
            designs.pptx_theme(data)
        except Exception:  # an odd file: leave it alone
            return None
        src = source_of(parse_zone(_read_ads(path)))
        act = action()
        saved = designs.save_to_folder(data, path.stem + ".pptx")
        side = {"source_url": src["url"], "site": src["site"], "attribution": src["attribution"], "method": "watcher",
                "imported": time.strftime("%Y-%m-%d %H:%M"), "original_name": path.name, "action": act}
        if path.suffix.lower() == ".potx":
            side["note"] = "A PowerPoint template (.potx), saved as a normal .pptx."
        saved.with_name(saved.name + ".source.json").write_text(json.dumps(side, ensure_ascii=False, indent=1), encoding="utf-8")
        if act == "move":
            try:
                if saved.stat().st_size == len(data):  # the copy is complete: only then is the original removed
                    path.unlink()
            except OSError:
                pass  # could not remove the original: the copy is safe
        name = saved.stem
        msg = f"Added '{name}' from {src['site']} to your designs" if src["site"] else f"Added '{name}' to your designs"
        ev = {"id": hashlib.sha1(f"{saved}{time.time()}".encode()).hexdigest()[:10], "ts": time.time(), "kind": "design_added", "name": name,
              "site": src["site"], "message": msg, "path": str(saved)}
        self.events.append(ev)
        del self.events[:-MAX_EVENTS]
        return ev

    def events_since(self, ts: float) -> list[dict]:
        return [e for e in self.events if e["ts"] > ts]

    def status(self) -> dict:
        f = folder()
        return {"enabled": enabled(), "folder": str(f), "exists": f.is_dir(), "action": action(), "started": self.started, "active": self.active}


WATCHER = Watcher()
