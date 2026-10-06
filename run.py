"""Start AFRA:  python run.py  [--port 8765] [--no-browser]"""
import argparse
import os
import sys
import threading
import webbrowser

# pythonw.exe (background tasks, e.g. the Domain Manager) has no console: log to a file instead
if sys.stdout is None or sys.stderr is None:
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(log_dir, exist_ok=True)
    sys.stdout = sys.stderr = open(os.path.join(log_dir, "server.log"), "a", encoding="utf-8", buffering=1)

import uvicorn

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    url = f"http://{args.host}:{args.port}"
    if os.environ.pop("AFRA_WAIT_PORT", None):  # restarted after an update: wait until the old server has exited
        import socket
        import time
        for _ in range(60):
            with socket.socket() as sk:
                if sk.connect_ex((args.host, args.port)) != 0:
                    break
            time.sleep(0.5)
    print(f"\n  A.F.R.A running at {url}\n  (c) 2026 danafarmansyah. Crafted with love.\n  Press Ctrl+C to stop.\n")
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="warning")
