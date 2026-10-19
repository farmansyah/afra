"""Connect A.F.R.A to the Claude desktop app (adds A.F.R.A as an MCP server in Claude's settings file).

Run by the Mac installer (python -m app.claude_connect) and by Settings > Connect to Claude.
Other servers already in Claude's settings are kept, and the old file is saved next to it as .bak.
"""
import json
import os
import shutil
import sys
from pathlib import Path

from .config import BASE_DIR

NAME = "afra"


def config_files() -> list[Path]:
    """Claude's settings file(s). The Microsoft Store version keeps its own copy inside its package folder."""
    if sys.platform == "darwin":
        return [Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"]
    if os.name == "nt":
        out = [Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Claude" / "claude_desktop_config.json"]
        pkgs = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Packages"
        out += [p / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json" for p in pkgs.glob("Claude_*")]
        return out
    return [Path.home() / ".config" / "Claude" / "claude_desktop_config.json"]


def entry() -> dict:
    py = Path(sys.executable)
    if py.name.lower() == "pythonw.exe" and (py.parent / "python.exe").exists():  # the background server runs windowless
        py = py.parent / "python.exe"
    return {"command": str(py), "args": [str(BASE_DIR / "app" / "mcp_server.py")]}


def _read(f: Path) -> dict:
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except ValueError:
        return {}


def status() -> dict:
    files = config_files()
    installed = [f for f in files if f.parent.exists()]
    connected = any((_read(f).get("mcpServers") or {}).get(NAME) == entry() for f in installed)
    return {"claude_installed": bool(installed), "connected": connected, "entry": entry(),
            "config_file": str((installed or files)[-1])}  # the Store app reads its own copy, listed last


def snippet() -> str:
    """What to paste in Claude's settings when A.F.R.A can't write the file itself: the A.F.R.A entry alone
    if the file already has "mcpServers", otherwise the whole section, pasted right after the first {."""
    cfg = next((c for c in map(_read, config_files()) if c), {})
    if "mcpServers" in cfg:
        return json.dumps({NAME: entry()}, indent=2)[1:-1].strip("\n")
    return json.dumps({"mcpServers": {NAME: entry()}}, indent=2)[1:-1].strip("\n") + ","


def connect() -> dict:
    files = [f for f in config_files() if f.parent.exists()]
    if not files:
        raise ValueError("The Claude desktop app is not installed on this computer (claude.com/download). "
                         "Install it, sign in once, then click Connect again.")
    written = 0
    for f in files:
        cfg = _read(f)
        cfg.setdefault("mcpServers", {})[NAME] = entry()
        try:
            if f.exists():
                try:
                    shutil.copy2(f, f.with_suffix(".json.bak"))
                except OSError:
                    pass  # no backup is better than no connection
            tmp = f.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            os.replace(tmp, f)
            written += 1
        except OSError:
            continue  # Windows can refuse writes to Claude's folder (e.g. when A.F.R.A was started from inside Claude)
    out = status()
    if not written:
        out.update(blocked=True, snippet=snippet())
    return out


if __name__ == "__main__":
    try:
        s = connect()
        if s.get("blocked"):
            print("Windows didn't let A.F.R.A change Claude's settings. In Claude open Settings > Developer > Edit Config, "
                  'and add these lines inside "mcpServers":\n' + s["snippet"])
        else:
            print("Connected A.F.R.A to the Claude app. Quit and reopen Claude to see it.")
    except ValueError as e:
        print(e)
