"""Use A.F.R.A from a phone or tablet on the same Wi-Fi.

The normal server only listens on this computer (127.0.0.1). Switching phone access on opens a second door on the
home network (port 8770), protected by a 6-digit PIN. Through that door API keys are hidden and settings, updates,
backups, sharing and the Claude connection cannot be changed.
"""
import hashlib
import os
import secrets
import socket
import threading
import time

from .config import get_settings, save_settings

PORT = 8770
HOST = os.environ.get("AFRA_PHONE_HOST", "0.0.0.0")  # tests use 127.0.0.1
COOKIE = "afra_phone"
_server = {"srv": None, "thread": None}
_fails: dict[str, list[float]] = {}
LOCKED = ("/api/settings", "/api/update", "/api/backups", "/api/claude", "/api/sync/enable", "/api/sync/disable",
          "/api/phone", "/api/quality/sjr", "/api/projects/import", "/api/slides/setup", "/api/handoff")


def lan_addresses() -> list[str]:
    ips = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # picks the Wi-Fi/LAN interface; nothing is sent
            ips.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return ips


def _token(pin: str) -> str:
    return hashlib.sha256(f"afra-phone:{pin}:{get_settings().get('phone_secret', '')}".encode()).hexdigest()


def cookie_ok(value: str | None) -> bool:
    pin = get_settings().get("phone_pin")
    return bool(pin and value and secrets.compare_digest(value, _token(pin)))


def login(pin: str, ip: str) -> str:
    now = time.time()
    tries = [t for t in _fails.get(ip, []) if now - t < 300]
    if len(tries) >= 5:
        raise ValueError("Too many wrong PINs. Wait 5 minutes and try again.")
    real = get_settings().get("phone_pin") or ""
    if not real or not secrets.compare_digest(pin.strip(), real):
        _fails[ip] = tries + [now]
        raise ValueError("Wrong PIN. It is shown on the computer under Settings > Use on your phone.")
    _fails.pop(ip, None)
    return _token(real)


def is_phone(request) -> bool:
    return (request.scope.get("server") or (None, None))[1] == PORT


def locked(path: str, method: str) -> bool:
    if path == "/api/settings" and method == "GET":
        return False  # allowed, but keys are hidden
    return path.startswith(LOCKED)


def redact(settings: dict) -> dict:
    return {k: ("••••••" if v and ("key" in k or k in ("phone_pin", "phone_secret")) else v) for k, v in settings.items()}


def start(app):
    if _server["srv"]:
        return
    import uvicorn
    cfg = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning", lifespan="off")  # background jobs run once, in the main server
    srv = uvicorn.Server(cfg)
    t = threading.Thread(target=srv.run, daemon=True, name="afra-phone")
    t.start()
    _server.update(srv=srv, thread=t)


def stop():
    if _server["srv"]:
        _server["srv"].should_exit = True
        _server["thread"].join(timeout=5)
    _server.update(srv=None, thread=None)


def status() -> dict:
    s = get_settings()
    on = bool(s.get("phone_access"))
    return {"enabled": on, "running": bool(_server["srv"]) and _server["thread"].is_alive(), "pin": s.get("phone_pin") if on else "",
            "urls": [f"http://{ip}:{PORT}" for ip in lan_addresses()] if on else []}


def enable(app, on: bool, new_pin: bool = False) -> dict:
    s = get_settings()
    changes = {"phone_access": on}
    if on and (new_pin or not s.get("phone_pin")):
        changes.update(phone_pin=f"{secrets.randbelow(10 ** 6):06d}", phone_secret=secrets.token_hex(16))
    save_settings(changes)
    (start if on else lambda _a: stop())(app)
    if on:
        time.sleep(0.5)
    return status()
