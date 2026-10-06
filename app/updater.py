"""One-click updates from GitHub.

Installed copies compare app/version.py with the version on GitHub. Updating downloads the latest
code, keeps a zip backup of the current program files, writes the new files (never touching data/,
.venv/ or dist/), installs any new requirements, then restarts the server. The user's library,
documents, settings and API keys live in data/ and are never changed.
"""
import asyncio
import io
import os
import re
import subprocess
import sys
import time
import zipfile

import httpx

from .config import BASE_DIR, DATA_DIR
from .version import VERSION

REPO = "farmansyah/afra"
RAW_VERSION = f"https://raw.githubusercontent.com/{REPO}/main/app/version.py"
ZIP_URL = f"https://codeload.github.com/{REPO}/zip/refs/heads/main"
KEEP = ("data/", ".venv/", "dist/", ".git/")
EXEC = ("start.sh", "Start AFRA.command", "AFRA.app/Contents/MacOS/AFRA")


def is_dev_copy() -> bool:
    return (BASE_DIR / ".git").exists()


def _parse(text: str) -> str:
    m = re.search(r'VERSION\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else ""


async def check() -> dict:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        r = await c.get(RAW_VERSION, params={"t": int(time.time())})
        if r.status_code == 404:  # nothing published yet
            return {"current": VERSION, "latest": "", "available": False, "dev_copy": is_dev_copy()}
        r.raise_for_status()
    latest = _parse(r.text)
    return {"current": VERSION, "latest": latest, "available": bool(latest) and latest > VERSION,
            "dev_copy": is_dev_copy()}


def _backup_program() -> str:
    dest = DATA_DIR / "backups"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"program-{VERSION}-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in BASE_DIR.rglob("*"):
            rel = f.relative_to(BASE_DIR).as_posix()
            if f.is_file() and not rel.startswith(KEEP) and "__pycache__" not in rel:
                z.write(f, rel)
    return str(path)


def _install(data: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(data))
    names = [n for n in z.namelist() if not n.endswith("/")]
    root = names[0].split("/", 1)[0] + "/"
    new_version = ""
    for n in names:
        rel = n[len(root):]
        if not rel or rel.startswith(KEEP):
            continue
        target = BASE_DIR / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".new")
        tmp.write_bytes(z.read(n))
        os.replace(tmp, target)  # atomic per file
        if rel in EXEC and os.name != "nt":
            target.chmod(0o755)
        if rel == "app/version.py":
            new_version = _parse(target.read_text(encoding="utf-8"))
    return new_version


async def apply() -> dict:
    if is_dev_copy():
        raise ValueError("This is the developer copy (managed with git). Update it with git pull instead.")
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
        r = await c.get(ZIP_URL)
        r.raise_for_status()
    backup = await asyncio.to_thread(_backup_program)
    new_version = await asyncio.to_thread(_install, r.content)
    pip = await asyncio.to_thread(subprocess.run, [sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check",
                                                   "-r", str(BASE_DIR / "requirements.txt")], capture_output=True, text=True)
    return {"updated_to": new_version, "backup": backup, "components_ok": pip.returncode == 0,
            "pip_error": (pip.stderr or "")[-400:] if pip.returncode else ""}


def restart():
    """Start a fresh server process (it waits for this one to free the port), then exit."""
    args = [a for a in sys.argv if a != "--no-browser"] + ["--no-browser"]
    env = dict(os.environ, AFRA_WAIT_PORT="1")
    kw = {"cwd": str(BASE_DIR), "env": env}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        exe = sys.executable.replace("python.exe", "pythonw.exe") if sys.executable.endswith("python.exe") else sys.executable
    else:
        kw["start_new_session"] = True
        exe = sys.executable
        log = open(DATA_DIR / "server.log", "a")
        kw.update(stdout=log, stderr=log)
    subprocess.Popen([exe] + args, **kw)
    os._exit(0)
