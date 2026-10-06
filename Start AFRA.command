#!/bin/bash
# AFRA for macOS. Double-click this file in Finder.
# First time only: right-click it > Open > Open (macOS asks once because it was downloaded).
cd "$(dirname "$0")" || exit 1
clear
echo "=============================================="
echo "   A.F.R.A  -  Article Finder & Research Assistant"
echo "=============================================="

find_python() {
  for p in python3.13 python3.12 python3.11 python3.10 python3 /usr/local/bin/python3 /opt/homebrew/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/Current/bin/python3; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      echo "$p"; return 0
    fi
  done
  return 1
}

if [ ! -x ".venv/bin/python" ]; then
  PY="$(find_python)"
  if [ -z "$PY" ]; then
    osascript -e 'display dialog "A.F.R.A needs Python (free).\n\n1. A download page will open — click the yellow \"Download Python\" button.\n2. Open the downloaded file and click Continue until it finishes.\n3. Double-click \"Start AFRA\" again." buttons {"Open download page"} default button 1 with title "One-time setup"' >/dev/null 2>&1
    open "https://www.python.org/downloads/macos/"
    exit 1
  fi
  echo "First-time setup (about 1 minute)..."
  "$PY" -m venv .venv || { osascript -e 'display alert "Setup failed while creating the Python environment."'; exit 1; }
fi

echo "Checking components..."
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt || {
  osascript -e 'display alert "Could not install components. Check your internet connection and try again."'; exit 1; }

echo ""
echo "A.F.R.A is opening in your browser."
echo "Keep this window open while you work. Close it to stop."
echo ""
exec .venv/bin/python run.py "$@"
