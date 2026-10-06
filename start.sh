#!/usr/bin/env sh
# macOS users: double-click "Start AFRA.command" instead. Linux: ./start.sh
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt
exec .venv/bin/python run.py "$@"
