"""A.F.R.A chat relay: passes end-to-end encrypted chat messages between paired A.F.R.A apps, instantly.

It never sees a message, name, photo or voice note: apps encrypt everything with a key made from their pairing code
before sending. The relay only knows a random room id, scrambled data, and which devices are online.

- WebSocket /v1/ws?room=&token=&device=   live delivery, catch-up after being offline, typing/online status
- PUT/GET /v1/blob/{room}/{id}            encrypted attachments (photos, files, voice notes)
- Web Push to the phone app (a contentless "new message" ping; iPhone: add the app to the Home Screen).
- Serves the chat app itself (WEB_DIR), so phones open https://<this relay>/ and install it.
Stored data is deleted after RETENTION_DAYS. The apps also keep a copy in their shared folder, so a lost relay loses
nothing. (c) 2026 danafarmansyah.
"""
import asyncio
import hashlib
import hmac
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import base64
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response

DATA = Path(os.environ.get("RELAY_DATA", "/data"))
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / "relay.db"
BLOBS = DATA / "blobs"
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "60"))
MAX_BLOB = int(os.environ.get("MAX_BLOB_MB", "30")) * 1024 * 1024
MAX_FRAME = 64 * 1024
WEB_DIR = Path(os.environ.get("WEB_DIR", Path(__file__).resolve().parent / "web"))
CONTACT = os.environ.get("PUSH_CONTACT", "mailto:afra@localhost")

app = FastAPI(title="A.F.R.A chat relay", docs_url=None, redoc_url=None)
ROOMS: dict[str, dict[str, WebSocket]] = {}  # room -> device -> socket


def db():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


with closing(db()) as c:
    c.executescript("""
    CREATE TABLE IF NOT EXISTS rooms (room TEXT PRIMARY KEY, token_hash TEXT, created REAL);
    CREATE TABLE IF NOT EXISTS envs (seq INTEGER PRIMARY KEY AUTOINCREMENT, room TEXT, sender TEXT, data TEXT, ts REAL);
    CREATE INDEX IF NOT EXISTS envs_room ON envs(room, seq);
    CREATE TABLE IF NOT EXISTS webpush (room TEXT, device TEXT, sub TEXT, PRIMARY KEY (room, device));
    """)
    c.commit()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def check(room: str, token: str) -> bool:
    """The first app to use a room registers its token; afterwards only the same token works."""
    if not (16 <= len(room) <= 64 and room.isalnum() and 32 <= len(token) <= 128):
        return False
    with closing(db()) as c:
        r = c.execute("SELECT token_hash FROM rooms WHERE room=?", (room,)).fetchone()
        if not r:
            c.execute("INSERT INTO rooms VALUES (?,?,?)", (room, _hash(token), time.time()))
            c.commit()
            return True
        return hmac.compare_digest(r["token_hash"], _hash(token))


async def broadcast(room: str, msg: dict, skip: str = ""):
    for dev, ws in list(ROOMS.get(room, {}).items()):
        if dev != skip:
            try:
                await ws.send_text(json.dumps(msg))
            except Exception:
                ROOMS.get(room, {}).pop(dev, None)


async def presence(room: str):
    await broadcast(room, {"t": "presence", "online": sorted(ROOMS.get(room, {}))})


# ---------------------------------------------------------------- Web Push (VAPID, no payload)
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _vapid_key() -> ec.EllipticCurvePrivateKey:
    f = DATA / "vapid.pem"
    if not f.exists():
        k = ec.generate_private_key(ec.SECP256R1())
        f.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        f.chmod(0o600)
    return serialization.load_pem_private_key(f.read_bytes(), None)


VAPID = _vapid_key()
VAPID_PUBLIC = _b64(VAPID.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))


def _vapid_header(endpoint: str) -> str:
    u = urlparse(endpoint)
    head = _b64(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    body = _b64(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": CONTACT}).encode())
    r, s_ = decode_dss_signature(VAPID.sign(f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256())))
    return f"vapid t={head}.{body}.{_b64(r.to_bytes(32, 'big') + s_.to_bytes(32, 'big'))}, k={VAPID_PUBLIC}"


async def push(room: str, sender: str):
    """A contentless push: the phone's app shows "New message", the message itself stays end-to-end encrypted."""
    with closing(db()) as c:
        subs = [(r["device"], r["sub"]) for r in c.execute("SELECT device, sub FROM webpush WHERE room=? AND device<>?", (room, sender))]
    async with httpx.AsyncClient(timeout=15) as h:
        for device, sub in subs:
            if device in ROOMS.get(room, {}):
                continue  # that app is open right now and shows the message itself
            try:
                endpoint = json.loads(sub)["endpoint"]
                r = await h.post(endpoint, headers={"Authorization": _vapid_header(endpoint), "TTL": "86400", "Urgency": "high"})
                if r.status_code in (404, 410):  # the phone unsubscribed or the app was removed
                    with closing(db()) as c:
                        c.execute("DELETE FROM webpush WHERE room=? AND device=?", (room, device))
                        c.commit()
            except Exception:
                pass


@app.get("/v1/vapid")
def vapid():
    return {"key": VAPID_PUBLIC}


@app.get("/v1/health")
def health():
    return {"app": "A.F.R.A chat relay", "ok": True}


@app.get("/")
def home():
    if (WEB_DIR / "index.html").exists():
        return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})
    return {"app": "A.F.R.A chat relay", "ok": True}


@app.get("/{name}")
def web_file(name: str):
    """The chat app's files (index, script, styles, icons, manifest, service worker)."""
    f = (WEB_DIR / name).resolve()
    if f.parent != WEB_DIR.resolve() or not f.is_file():
        raise HTTPException(404)
    types = {".webmanifest": "application/manifest+json", ".js": "text/javascript", ".json": "application/json"}
    return FileResponse(f, media_type=types.get(f.suffix), headers={"Cache-Control": "no-cache"})


@app.websocket("/v1/ws")
async def ws(sock: WebSocket, room: str, token: str, device: str):
    if not check(room, token) or not (1 <= len(device) <= 40):
        await sock.close(code=4401)
        return
    await sock.accept()
    old = ROOMS.setdefault(room, {}).get(device)
    if old:
        try:
            await old.close(code=4000)
        except Exception:
            pass
    ROOMS[room][device] = sock
    await presence(room)
    try:
        while True:
            raw = await sock.receive_text()
            if len(raw) > MAX_FRAME:
                continue
            m = json.loads(raw)
            t = m.get("t")
            if t == "since":  # catch up on everything sent while this device was away
                with closing(db()) as c:
                    for r in c.execute("SELECT seq, sender, data FROM envs WHERE room=? AND seq>? ORDER BY seq LIMIT 2000",
                                       (room, int(m.get("seq") or 0))):
                        await sock.send_text(json.dumps({"t": "env", "seq": r["seq"], "from": r["sender"], "data": r["data"]}))
                await sock.send_text(json.dumps({"t": "caught_up"}))
            elif t == "env":  # an encrypted message/event: store, deliver, ping phones
                with closing(db()) as c:
                    cur = c.execute("INSERT INTO envs (room, sender, data, ts) VALUES (?,?,?,?)", (room, device, str(m["data"]), time.time()))
                    c.commit()
                    seq = cur.lastrowid
                await sock.send_text(json.dumps({"t": "stored", "ref": m.get("ref"), "seq": seq}))
                await broadcast(room, {"t": "env", "seq": seq, "from": device, "data": m["data"]}, skip=device)
                if m.get("notify"):
                    asyncio.create_task(push(room, device))
            elif t == "eph":  # typing… and similar: delivered live, never stored
                await broadcast(room, {"t": "eph", "from": device, "data": m.get("data")}, skip=device)
            elif t == "webpush":  # this device's push subscription (phone app), or none to stop
                with closing(db()) as c:
                    if m.get("sub"):
                        c.execute("INSERT OR REPLACE INTO webpush VALUES (?,?,?)", (room, device, json.dumps(m["sub"])[:4000]))
                    else:
                        c.execute("DELETE FROM webpush WHERE room=? AND device=?", (room, device))
                    c.commit()
            elif t == "ping":
                await sock.send_text('{"t":"pong"}')
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        if ROOMS.get(room, {}).get(device) is sock:
            ROOMS[room].pop(device, None)
            await presence(room)


def _blob_path(room: str, bid: str) -> Path:
    if not (room.isalnum() and bid.replace("-", "").isalnum() and len(bid) <= 64):
        raise HTTPException(400, "bad id")
    return BLOBS / room / bid


@app.put("/v1/blob/{room}/{bid}")
async def put_blob(room: str, bid: str, request: Request):
    if not check(room, request.headers.get("x-token", "")):
        raise HTTPException(401)
    data = await request.body()
    if len(data) > MAX_BLOB:
        raise HTTPException(413, "too large")
    p = _blob_path(room, bid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return {"ok": True}


@app.get("/v1/blob/{room}/{bid}")
def get_blob(room: str, bid: str, request: Request):
    if not check(room, request.headers.get("x-token", "")):
        raise HTTPException(401)
    p = _blob_path(room, bid)
    if not p.exists():
        raise HTTPException(404)
    return Response(p.read_bytes(), media_type="application/octet-stream")


async def _cleanup():
    while True:
        cutoff = time.time() - RETENTION_DAYS * 86400
        with closing(db()) as c:
            c.execute("DELETE FROM envs WHERE ts<?", (cutoff,))
            c.commit()
        for f in BLOBS.rglob("*") if BLOBS.exists() else []:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
        await asyncio.sleep(6 * 3600)


@app.on_event("startup")
async def _start():
    asyncio.create_task(_cleanup())
