"""Slides with PPT Master (github.com/hugohe3/ppt-master, MIT, (c) Hugo He) in the Code tab of the Claude desktop app.

PPT Master is a Claude Code skill: the AI designs every slide as SVG and PPT Master's scripts turn them into an
editable PowerPoint. It needs a terminal, which the Code tab has and the chat does not. One click here prepares a
folder the Code tab can open, so it runs on the user's Claude plan:

  ~/AFRA-Slides/
    .claude/skills/ppt-master/   the official skill, unmodified (its integrity check requires that)
    .claude/settings.json        pre-approves the A.F.R.A connector and the skill's Python
    .venv/                       Python with PPT Master's core requirements
    .mcp.json                    connects A.F.R.A (documents, real references)
    CLAUDE.md                    how to work in this folder
"""
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import httpx

from .claude_connect import entry as afra_entry

ZIP = "https://codeload.github.com/hugohe3/ppt-master/zip/refs/heads/main"
FOLDER = Path(os.environ.get("AFRA_SLIDES_DIR", Path.home() / "AFRA-Slides"))
SKILL = FOLDER / ".claude" / "skills" / "ppt-master"
# what the slide pipeline needs (PyMuPDF is left out: AGPL, and A.F.R.A hands over text, not PDFs)
REQUIREMENTS = ["python-pptx>=0.6.21", "XlsxWriter>=3.0.0", "skia-pathops>=0.9.2", "uharfbuzz>=0.50.0", "PyYAML>=6.0",
                "Pillow>=9.0.0", "numpy>=1.20.0", "mammoth>=1.6.0", "markdownify>=0.11.6", "beautifulsoup4>=4.12.0",
                "requests>=2.31.0", "openpyxl>=3.1.0"]
JOB = {"state": "idle", "step": "", "error": "", "started": 0}


def venv_python() -> Path:
    return FOLDER / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _skill_version() -> str:
    try:
        for line in (SKILL / "SKILL.md").read_text(encoding="utf-8").splitlines()[:20]:
            if line.strip().startswith("version:"):
                return line.split(":", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


def status() -> dict:
    ready = (SKILL / "SKILL.md").exists() and venv_python().exists() and (FOLDER / ".mcp.json").exists()
    return {"installed": ready, "folder": str(FOLDER), "version": _skill_version(), **JOB}


def _write_folder_files():
    py = venv_python()
    (FOLDER / ".mcp.json").write_text(json.dumps({"mcpServers": {"afra": afra_entry()}}, indent=2), encoding="utf-8")
    settings = {"enabledMcpjsonServers": ["afra"],
                "permissions": {"allow": ["mcp__afra", f"Bash({py}:*)", f'Bash("{py}":*)']}}
    (FOLDER / ".claude" / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
    (FOLDER / "CLAUDE.md").write_text(f"""# A.F.R.A Slides

This folder turns the user's A.F.R.A research documents into editable PowerPoint decks with the PPT Master skill
(`.claude/skills/ppt-master`, by Hugo He, MIT). The user is a student, not a programmer: keep questions few and
explain choices briefly in their language (Indonesian or English).

- **Python**: run every PPT Master command with `"{py}"` instead of `python3` or `python`. That interpreter has the
  skill's dependencies. Do not pip install anything else without asking.
- **Content comes from A.F.R.A** (the `afra` tools): `list_documents`, `read_document`, and `slide_material`, which
  returns the document with citations already formatted plus the reference list. Never invent sources, numbers or
  quotes; every reference must come from A.F.R.A. Use `search_papers` + `add_papers` if the user wants more sources.
- End the deck with a References slide built from `slide_material`'s list (same citation style as the document).
- Keep decks in `projects/` here. When finished, give the user the full path of the .pptx so they can open it.
""", encoding="utf-8")
    (FOLDER / "projects").mkdir(exist_ok=True)


def _run():
    try:
        JOB.update(state="working", step="Downloading PPT Master (about 60 MB)…", error="", started=time.time())
        with httpx.Client(timeout=300, follow_redirects=True) as c:
            r = c.get(ZIP)
            r.raise_for_status()
        JOB["step"] = "Unpacking…"
        z = zipfile.ZipFile(io.BytesIO(r.content))
        prefix = next(n for n in z.namelist() if n.endswith("/skills/ppt-master/SKILL.md"))[:-len("SKILL.md")]
        tmp = FOLDER / ".claude" / "skills" / ".ppt-master-new"
        shutil.rmtree(tmp, ignore_errors=True)
        for info in z.infolist():
            if info.filename.startswith(prefix) and not info.is_dir():
                target = tmp / info.filename[len(prefix):]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(info))
                if os.name != "nt" and (info.external_attr >> 16) & 0o111:
                    target.chmod(0o755)
        shutil.rmtree(SKILL, ignore_errors=True)
        tmp.rename(SKILL)
        if not venv_python().exists():
            JOB["step"] = "Preparing Python for the slides…"
            base = getattr(sys, "_base_executable", sys.executable)
            subprocess.run([base, "-m", "venv", str(FOLDER / ".venv")], check=True, capture_output=True, timeout=300)
        JOB["step"] = "Installing slide components (a few minutes)…"
        p = subprocess.run([str(venv_python()), "-m", "pip", "install", "-q", "--disable-pip-version-check", *REQUIREMENTS],
                           capture_output=True, text=True, timeout=1800)
        if p.returncode:
            raise RuntimeError("Could not install the slide components: " + (p.stderr or p.stdout)[-400:])
        JOB["step"] = "Checking PPT Master…"
        g = subprocess.run([str(venv_python()), str(SKILL / "scripts" / "attribution_guard.py")], capture_output=True, text=True, timeout=120)
        if g.returncode:
            raise RuntimeError("PPT Master's own integrity check failed: " + (g.stdout + g.stderr)[-300:])
        _write_folder_files()
        JOB.update(state="done", step="Ready")
    except Exception as e:
        JOB.update(state="error", error=str(e) or e.__class__.__name__, step="")


def setup() -> dict:
    if JOB["state"] != "working":
        FOLDER.mkdir(parents=True, exist_ok=True)
        (FOLDER / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
        threading.Thread(target=_run, daemon=True, name="afra-slides-setup").start()
        time.sleep(0.2)
    return status()
