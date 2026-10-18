"""ResearchStudio (github.com/microsoft/ResearchStudio, MIT, (c) Microsoft) in the Code tab of the Claude desktop app.

ResearchStudio is a set of Claude Code skills for the whole research lifecycle:
  Idea  idea-spark (a research direction -> one reviewer-defensible idea card), paper-search, scoop-check (is it taken?)
  Reel  paper2poster, paper2blog, paper2assets, paper2reel (and paper2video): a finished paper PDF -> poster, blog...

Its own install.sh is bash-only (WSL on Windows), so A.F.R.A prepares the same layout itself, on Windows and Mac:

  ~/AFRA-Studio/
    ResearchStudio-Idea/, ResearchStudio-Reel/   the official code, unmodified
    .claude/skills/<skill>                       links to the skill folders (junctions on Windows), as install.sh does
    .claude/settings.json                        pre-approves the A.F.R.A connector and the folder's Python
    .venv/                                       Python with the skills' requirements
    .env                                         connector keys (OpenReview, OpenAlex, Semantic Scholar); never overwritten
    .mcp.json, CLAUDE.md                         A.F.R.A connection and how to work here
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
from .config import get_settings

ZIP = "https://codeload.github.com/microsoft/ResearchStudio/zip/refs/heads/main"
FOLDER = Path(os.environ.get("AFRA_STUDIO_DIR", Path.home() / "AFRA-Studio"))
BROWSERS = FOLDER / ".browsers"  # Playwright's Chromium for posters and videos, kept in the folder (the skills find it via settings env)
SLIDES_SKILL = Path(os.environ.get("AFRA_SLIDES_DIR", Path.home() / "AFRA-Slides")) / ".claude" / "skills" / "ppt-master"
# the same packages install.sh installs for each bundle
PARTS = {
    "idea": {"dir": "ResearchStudio-Idea", "skills": {"idea_spark": "idea-spark", "paper_search": "paper-search", "scoop_check": "scoop-check"},
             "pip": ["feedparser>=6.0.12", "openreview-py>=2.2.3", "beautifulsoup4>=4.13.0", "pymupdf>=1.26.0",
                     "scholarly>=1.7.11", "bibtexparser<2", "requests>=2.31.0"]},
    "reel": {"dir": "ResearchStudio-Reel", "skills": None,  # every folder in its skills/
             "pip": ["pymupdf", "pillow", "numpy", "python-docx>=1.1.2", "qrcode", "playwright", "imageio-ffmpeg",
                     "edge-tts>=7.2.8", "fonttools>=4.59", "python-pptx>=1.0", "pdf2image>=1.17", "lxml>=5.0", "pyphen>=0.14"]},
}
JOB = {"state": "idle", "part": "", "step": "", "error": "", "started": 0}


def venv_python() -> Path:
    return FOLDER / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _marker(part: str) -> Path:
    return FOLDER / ".venv" / f".afra-{part}-ready"


def _skill_names(part: str) -> dict:
    p = PARTS[part]
    if p["skills"]:
        return p["skills"]
    root = FOLDER / p["dir"] / "skills"
    return {d.name: d.name for d in sorted(root.iterdir()) if (d / "SKILL.md").exists()} if root.exists() else {}


def status() -> dict:
    parts = {}
    for part in PARTS:
        ready = _marker(part).exists() and all((FOLDER / ".claude" / "skills" / n / "SKILL.md").exists()
                                               for n in _skill_names(part).values())
        parts[part] = {"installed": ready, "skills": sorted(_skill_names(part).values()) if ready else []}
    env = FOLDER / ".env"
    return {"folder": str(FOLDER), "env": str(env), "parts": parts,
            "openreview": env.exists() and "your_openreview" not in env.read_text(encoding="utf-8", errors="ignore"), **JOB}


def _link(src: Path, dst: Path):
    """A folder link, so the skills' own `Path(__file__).resolve().parents[...]` still find the rest of their repo."""
    if dst.is_symlink():
        dst.unlink()
    elif os.name == "nt" and dst.exists() and os.lstat(dst).st_file_attributes & 0x400:  # a junction: remove the link only
        os.rmdir(dst)
    elif dst.exists():  # an old copy
        shutil.rmtree(dst)
    if os.name == "nt":  # a junction needs no admin rights or Developer Mode
        p = subprocess.run(["cmd", "/c", "mklink", "/J", str(dst), str(src)], capture_output=True, text=True)
        if p.returncode == 0:
            return
        shutil.copytree(src, dst)
    else:
        os.symlink(src, dst, target_is_directory=True)


def _unpack(content: bytes, part: str):
    z = zipfile.ZipFile(io.BytesIO(content))
    top = z.namelist()[0].split("/")[0] + "/"
    want = top + PARTS[part]["dir"] + "/"
    tmp = FOLDER / (".new-" + PARTS[part]["dir"])
    shutil.rmtree(tmp, ignore_errors=True)
    for info in z.infolist():
        name = info.filename
        if info.is_dir() or not (name.startswith(want) or name in (top + "LICENSE", top + "README.md")):
            continue
        target = (tmp / name[len(want):]) if name.startswith(want) else FOLDER / name[len(top):]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(z.read(info))
        if os.name != "nt" and (info.external_attr >> 16) & 0o111:
            target.chmod(0o755)
    final = FOLDER / PARTS[part]["dir"]
    for _ in range(5):  # OneDrive and antivirus hold new folders for a moment
        shutil.rmtree(final, ignore_errors=True)
        try:
            tmp.rename(final)
            return
        except OSError:
            time.sleep(1)
    shutil.copytree(tmp, final, dirs_exist_ok=True)
    shutil.rmtree(tmp, ignore_errors=True)


def _write_env():
    env = FOLDER / ".env"
    if env.exists():
        return
    text = (FOLDER / "ResearchStudio-Idea" / ".env.template").read_text(encoding="utf-8")
    key = (get_settings().get("semantic_scholar_api_key") or "").replace(";", ",").split(",")[0].strip()
    if key:  # the user's own key, already in A.F.R.A
        text = text.replace("SEMANTICSCHOLAR_API_KEY=your_semanticscholar_key", "SEMANTICSCHOLAR_API_KEY=" + key)
    env.write_text(text, encoding="utf-8", newline="\n")


def _write_folder_files():
    py = venv_python()
    (FOLDER / ".mcp.json").write_text(json.dumps({"mcpServers": {"afra": afra_entry()}}, indent=2), encoding="utf-8")
    settings = {"enabledMcpjsonServers": ["afra"], "env": {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",  # emoji output on Windows
                                                          "PLAYWRIGHT_BROWSERS_PATH": str(BROWSERS)},
                "permissions": {"allow": ["mcp__afra", f"Bash({py}:*)", f'Bash("{py}":*)', "WebFetch", "WebSearch"]}}
    (FOLDER / ".claude" / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
    (FOLDER / "CLAUDE.md").write_text(f"""# A.F.R.A Studio

This folder runs Microsoft's ResearchStudio skills (`.claude/skills`, MIT) for an A.F.R.A user. The user is a student
or researcher, not a programmer: keep questions few, explain choices briefly in their language.

- **Python**: wherever a skill says `python`, `python3` or `$PY`, use `"{py}"`. It has the skills' requirements.
  Do not pip install anything else without asking.
- **Idea** (`idea-spark`, `paper-search`, `scoop-check`): turn a research direction into one defensible idea card,
  or check whether an idea is already published. Keys for the literature connectors are in `.env` here
  (`{FOLDER / '.env'}`); if OpenReview is not filled in, say so once and carry on with the other sources.
- **Reel** (`paper2poster`, `paper2blog`, `paper2assets`, `paper2reel`, `paper2video`): needs the finished paper as a
  PDF. Ask for its path. A.F.R.A documents can be exported with `export_word`; the user saves that as PDF in Word.
  Poppler, LibreOffice and ffmpeg may be missing on this computer: when a step needs one, use the skill's fallback
  or tell the user which one to install. `paper2video` also needs the `pptx2video` skill, which is not set up here.
- **A.F.R.A** (the `afra` tools) is the user's library: `library`, `read_paper`, `list_documents`, `read_document`.
  Use `search_papers` + `add_papers` to save papers the skills find into the user's project when they want that.
  Never invent sources, numbers or quotes.
- Keep each run's outputs in `runs/` here and give the user the full path of what you made.
""", encoding="utf-8")
    (FOLDER / "runs").mkdir(exist_ok=True)


def _pip(pkgs: list) -> subprocess.CompletedProcess:
    return subprocess.run([str(venv_python()), "-m", "pip", "install", "-q", "--disable-pip-version-check", *pkgs],
                          capture_output=True, text=True, timeout=1800)


def _editdistance_fallback():
    """openreview-py imports `editdistance` (a C extension) at load time. Where pip has no ready-made build for this
    Python and no compiler is around, put in a pure-Python one with the same `eval`, registered so pip counts it."""
    site = Path(subprocess.run([str(venv_python()), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                               capture_output=True, text=True, check=True).stdout.strip())
    (site / "editdistance").mkdir(exist_ok=True)
    (site / "editdistance" / "__init__.py").write_text('''"""Pure-Python stand-in for the editdistance package (put in by A.F.R.A)."""


def eval(a, b):
    """Levenshtein distance between two sequences (strings or lists)."""
    a, b = list(a), list(b)
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


distance = eval
''', encoding="utf-8")
    info = site / "editdistance-0.8.1.dist-info"
    info.mkdir(exist_ok=True)
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: editdistance\nVersion: 0.8.1\n", encoding="utf-8")
    (info / "INSTALLER").write_text("afra\n", encoding="utf-8")
    (info / "RECORD").write_text("", encoding="utf-8")


def _run(part: str):
    try:
        JOB.update(state="working", part=part, step="Downloading ResearchStudio (about 8 MB)…", error="", started=time.time())
        (FOLDER / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
        with httpx.Client(timeout=300, follow_redirects=True) as c:
            r = c.get(ZIP)
            r.raise_for_status()
        JOB["step"] = "Unpacking…"
        _unpack(r.content, part)
        if not venv_python().exists():
            JOB["step"] = "Preparing Python…"
            base = getattr(sys, "_base_executable", sys.executable)
            subprocess.run([base, "-m", "venv", str(FOLDER / ".venv")], check=True, capture_output=True, timeout=300)
        JOB["step"] = "Installing components (a few minutes)…"
        p = _pip(PARTS[part]["pip"])
        if p.returncode and "editdistance" in p.stdout + p.stderr:  # no ready-made build for this Python
            _editdistance_fallback()
            p = _pip(PARTS[part]["pip"])
        if p.returncode:
            raise RuntimeError("Could not install the components: " + (p.stderr or p.stdout)[-400:])
        if part == "reel":
            JOB["step"] = "Getting the browser that renders posters (about 150 MB)…"
            p = subprocess.run([str(venv_python()), "-m", "playwright", "install", "chromium"], capture_output=True, text=True,
                               timeout=1800, env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(BROWSERS)})
            if p.returncode:
                raise RuntimeError("Could not get the poster browser: " + (p.stderr or p.stdout)[-400:])
        JOB["step"] = "Linking the skills…"
        skills = FOLDER / ".claude" / "skills"
        for src, name in _skill_names(part).items():
            _link(FOLDER / PARTS[part]["dir"] / "skills" / src, skills / name)
        if part == "reel" and (SLIDES_SKILL / "SKILL.md").exists():  # paper2video builds its deck with PPT Master
            _link(SLIDES_SKILL, skills / "ppt-master")
        if part == "idea":
            _write_env()
        _write_folder_files()
        _marker(part).write_text(time.strftime("%Y-%m-%d %H:%M"), encoding="utf-8")
        JOB.update(state="done", step="Ready")
    except Exception as e:
        JOB.update(state="error", error=str(e) or e.__class__.__name__, step="")


def setup(part: str) -> dict:
    if part not in PARTS:
        raise ValueError("Choose idea or reel.")
    if JOB["state"] != "working":
        (FOLDER / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
        threading.Thread(target=_run, args=(part,), daemon=True, name="afra-studio-setup").start()
        time.sleep(0.2)
    return status()


def open_env() -> dict:
    """Open the connector keys file in the computer's text editor (the user fills in OpenReview there)."""
    env = FOLDER / ".env"
    if not env.exists():
        raise ValueError("Set up Idea first.")
    if os.name == "nt":
        subprocess.Popen(["notepad.exe", str(env)])
    else:
        subprocess.Popen(["open", "-e", str(env)] if sys.platform == "darwin" else ["xdg-open", str(env)])
    return {"ok": True}
