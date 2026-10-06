"""Build a shareable zip:  python tools/package.py   ->  dist/AFRA.zip

Works for Windows and macOS users. The zip stores Unix permissions, so on a Mac the
"Start AFRA.command" launcher stays double-clickable after unzipping.
Your data (data/) and the Python environment (.venv/) are never included."""
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", "data", "dist", "__pycache__", ".git", ".claude"}
EXEC = {"Start AFRA.command", "start.sh"}


def main():
    out = ROOT / "dist" / "AFRA.zip"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(ROOT.rglob("*")):
            rel = f.relative_to(ROOT)
            if f.is_dir() or any(part in SKIP_DIRS for part in rel.parts) or f.suffix == ".pyc":
                continue
            info = zipfile.ZipInfo(f"AFRA/{rel.as_posix()}", time.localtime(f.stat().st_mtime)[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3  # Unix, so the permission bits below are honoured
            info.external_attr = ((0o100755 if f.name in EXEC else 0o100644) << 16)
            data = f.read_bytes()
            if f.name in EXEC:  # Mac/Linux scripts must have Unix line endings or they fail to start
                data = data.replace(b"\r\n", b"\n")
            z.writestr(info, data)
    print(f"Created {out}")


if __name__ == "__main__":
    main()
