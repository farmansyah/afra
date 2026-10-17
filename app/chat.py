"""Private chat between the two of you: A.F.R.A on the computers, the chat app on the iPhones.

Delivery, two ways at once:
- Live through the relay on your VPS (relay/relay.py): instant, typing/online status, phone push notifications.
  Everything is end-to-end encrypted with a key made from the pairing code; the relay only sees scrambled data.
- A copy through the shared library folder (sync.py syncs the `messages` table and the attachments), so if the
  relay is down, messages between the computers still arrive (more slowly) and nothing is lost.

The same message row travels both ways; whichever copy arrives later just updates (newest `updated` wins), so
nothing shows twice. The protocol matches static/chat/chat.js exactly (key derivation, AES-GCM, row format).
"""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path

from . import db, notify
from .config import DATA_DIR, get_settings, save_settings

SALT = b"afra-chat-v1"
FILES = DATA_DIR / "chat"  # attachments (photos, files, voice notes), also copied to the shared folder
STATE = {"relay": "off", "error": "", "online": [], "typing": {}, "connected_at": 0}
SUBSCRIBERS: set[asyncio.Queue] = set()  # open chat windows (Server-Sent Events)
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
COLS = ("id", "author", "device", "kind", "text", "reply_to", "reactions", "edited", "deleted", "attachment", "created", "updated", "read")


# ---------------------------------------------------------------- keys and encryption (same as chat.js)
def new_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(16))
    return "-".join(raw[i:i + 4] for i in range(0, 16, 4))


def norm_code(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


_KEYS: dict[str, dict] = {}


def keys(code: str) -> dict:
    c = norm_code(code)
    if c not in _KEYS:
        master = hashlib.pbkdf2_hmac("sha256", c.encode(), SALT, 200_000, 32)
        _KEYS[c] = {"room": hashlib.sha256(master + b"room").hexdigest()[:32],
                    "token": hmac.new(master, b"token", hashlib.sha256).hexdigest(),
                    "enc": hmac.new(master, b"enc", hashlib.sha256).digest()}
    return _KEYS[c]


def seal(k: dict, data: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    return nonce + AESGCM(k["enc"]).encrypt(nonce, data, k["room"].encode())


def unseal(k: dict, data: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(k["enc"]).decrypt(data[:12], data[12:], k["room"].encode())


def configured() -> dict | None:
    s = get_settings()
    if s.get("chat_code") and s.get("chat_relay"):
        return {"relay": s["chat_relay"].rstrip("/"), **keys(s["chat_code"])}
    return None


def me() -> str:
    from . import sync
    return (get_settings().get("chat_name") or "").strip() or sync.device()["name"]


# ---------------------------------------------------------------- the local store (the `messages` table, synced via the folder)
def init():
    with db.conn() as c:
        have = {r[1] for r in c.execute("PRAGMA table_info(messages)")}
        for col, decl in (("kind", "TEXT DEFAULT 'text'"), ("reply_to", "TEXT DEFAULT ''"), ("reactions", "TEXT DEFAULT '{}'"),
                          ("edited", "REAL DEFAULT 0"), ("deleted", "INTEGER DEFAULT 0"), ("attachment", "TEXT DEFAULT ''"),
                          ("updated", "REAL DEFAULT 0")):
            if col not in have:
                c.execute(f"ALTER TABLE messages ADD COLUMN {col} {decl}")
        c.execute("UPDATE messages SET updated=created WHERE updated IS NULL OR updated=0")
        c.execute("CREATE TABLE IF NOT EXISTS chat_outbox (n INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT, notify INTEGER, created REAL)")
    FILES.mkdir(parents=True, exist_ok=True)


def _row(r) -> dict:
    d = {k: r[k] for k in COLS}
    for k, empty in (("reactions", {}), ("attachment", None)):
        try:
            d[k] = json.loads(d[k]) if d[k] else empty
        except ValueError:
            d[k] = empty
    d["deleted"] = bool(d["deleted"])
    return d


def messages(limit=400) -> list[dict]:
    with db.conn() as c:
        rows = c.execute("SELECT * FROM messages ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
    return [_row(r) for r in rows][::-1]


def get(mid: str) -> dict | None:
    with db.conn() as c:
        r = c.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    return _row(r) if r else None


def _write(c, row: dict):
    vals = dict(row, reactions=json.dumps(row.get("reactions") or {}), attachment=json.dumps(row["attachment"]) if row.get("attachment") else "",
                deleted=1 if row.get("deleted") else 0)
    c.execute(f"INSERT OR REPLACE INTO messages ({', '.join(COLS)}) VALUES ({', '.join('?' * len(COLS))})", [vals.get(k) for k in COLS])


def upsert(row: dict) -> bool:
    """Apply a row from the relay: newest `updated` wins, so the folder copy and the relay copy never clash."""
    if not row.get("id") or not isinstance(row.get("created"), (int, float)):
        return False
    row = {k: row.get(k) for k in COLS}
    row["updated"] = float(row.get("updated") or row["created"])
    with db.conn() as c:
        cur = c.execute("SELECT updated FROM messages WHERE id=?", (row["id"],)).fetchone()
        if cur and (cur["updated"] or 0) >= row["updated"]:
            return False
        c.execute("UPDATE sync_flag SET applying=1")  # the sender already put it in the shared folder
        try:
            _write(c, row)
        finally:
            c.execute("UPDATE sync_flag SET applying=0")
    return True


def _save_and_send(row: dict, notify_partner=False) -> dict:
    with db.conn() as c:
        _write(c, row)
        if configured():
            c.execute("INSERT INTO chat_outbox (data, notify, created) VALUES (?,?,?)", (json.dumps({"row": row}), int(notify_partner), time.time()))
    publish("change")
    return row


def send(text="", kind="text", reply_to="", attachment=None) -> dict:
    from . import sync
    text = (text or "").strip()[:8000]
    if not text and not attachment:
        raise ValueError("Write something first.")
    now = time.time()
    return _save_and_send({"id": db.new_id() + db.new_id()[:4], "author": me(), "device": sync.device()["id"], "kind": kind, "text": text,
                           "reply_to": reply_to or "", "reactions": {}, "edited": 0, "deleted": False, "attachment": attachment,
                           "created": now, "updated": now, "read": 0}, notify_partner=True)


def change(mid: str, action: str, value: str = "") -> dict:
    row = get(mid)
    if not row:
        raise ValueError("That message is gone.")
    mine = row["author"] == me()
    if action == "react":
        r = dict(row["reactions"] or {})
        if r.get(me()) == value or not value:
            r.pop(me(), None)
        else:
            r[me()] = value[:8]
        row["reactions"] = r
    elif action == "edit":
        if not mine or row["deleted"]:
            raise ValueError("You can only edit your own messages.")
        row.update(text=(value or "").strip()[:8000], edited=time.time())
    elif action == "delete":
        if not mine:
            raise ValueError("You can only delete your own messages.")
        row.update(text="", attachment=None, deleted=True, reactions={})
    else:
        raise ValueError("Unknown action")
    row["updated"] = time.time()
    return _save_and_send(row)


def mark_read() -> int:
    who = me()
    with db.conn() as c:
        rows = [_row(r) for r in c.execute("SELECT * FROM messages WHERE author<>? AND read=0 AND deleted=0", (who,))]
    now = time.time()
    for row in rows:
        row.update(read=now, updated=now)
        _save_and_send(row)
    return len(rows)


def unread() -> int:
    with db.conn() as c:
        return c.execute("SELECT COUNT(*) FROM messages WHERE author<>? AND read=0 AND deleted=0", (me(),)).fetchone()[0]


# ---------------------------------------------------------------- attachments
def save_attachment(name: str, mime: str, data: bytes) -> dict:
    if len(data) > 25 * 1024 * 1024:
        raise ValueError("That file is larger than 25 MB.")
    blob = secrets.token_hex(12)
    (FILES / blob).write_bytes(data)
    att = {"name": os.path.basename(name or "file")[:120], "mime": (mime or "application/octet-stream")[:80], "size": len(data), "blob": blob}
    asyncio.get_event_loop().create_task(_upload(blob))
    return att


async def _upload(blob: str):
    cfg = configured()
    if not cfg:
        return
    import httpx
    try:
        async with httpx.AsyncClient(timeout=120) as h:
            await h.put(f"{cfg['relay']}/v1/blob/{cfg['room']}/{blob}", content=seal(cfg, (FILES / blob).read_bytes()),
                        headers={"x-token": cfg["token"]})
    except Exception:
        pass  # the shared folder still carries it between the computers


async def fetch_blob(blob: str) -> Path | None:
    p = FILES / blob
    if p.exists():
        return p
    cfg = configured()
    if not cfg or not re.fullmatch(r"[0-9a-f]{8,64}", blob):
        return None
    import httpx
    try:
        async with httpx.AsyncClient(timeout=120) as h:
            r = await h.get(f"{cfg['relay']}/v1/blob/{cfg['room']}/{blob}", headers={"x-token": cfg["token"]})
            r.raise_for_status()
        p.write_bytes(unseal(cfg, r.content))
        return p
    except Exception:
        return None


# ---------------------------------------------------------------- live updates for open chat windows
def publish(kind: str, **data):
    for q in list(SUBSCRIBERS):
        try:
            q.put_nowait({"type": kind, **data})
        except asyncio.QueueFull:
            pass


def status() -> dict:
    s = get_settings()
    now = time.time()
    typing = [n for n, t in STATE["typing"].items() if now - t < 6]
    return {"me": me(), "paired": bool(s.get("chat_code")), "relay_url": s.get("chat_relay", ""), "relay": STATE["relay"],
            "error": STATE["error"], "partner_online": bool(STATE["online"]), "typing": typing, "unread": unread(),
            "backup": bool(s.get("sync_path"))}


# ---------------------------------------------------------------- the relay connection
_TYPING_OUT: asyncio.Queue = asyncio.Queue(maxsize=5)


def typing():
    try:
        _TYPING_OUT.put_nowait(me())
    except asyncio.QueueFull:
        pass


def _seq(value=None):
    with db.conn() as c:
        if value is None:
            r = c.execute("SELECT v FROM sync_kv WHERE k='chat_seq'").fetchone()
            return int(r[0]) if r else 0
        c.execute("INSERT OR REPLACE INTO sync_kv VALUES ('chat_seq', ?)", (str(value),))


async def relay_loop():
    import websockets
    from . import sync
    backoff = 2
    while True:
        cfg = configured()
        if not cfg:
            STATE.update(relay="off", online=[])
            await asyncio.sleep(5)
            continue
        url = re.sub(r"^http", "ws", cfg["relay"]) + f"/v1/ws?room={cfg['room']}&token={cfg['token']}&device={sync.device()['id']}"
        STATE["relay"] = "connecting"
        try:
            async with websockets.connect(url, open_timeout=15, ping_interval=25, max_size=2 ** 20) as ws:
                STATE.update(relay="live", error="", connected_at=time.time())
                publish("status")
                backoff = 2
                await ws.send(json.dumps({"t": "since", "seq": _seq()}))
                sender = asyncio.create_task(_relay_send(ws, cfg))
                try:
                    async for raw in ws:
                        await _relay_receive(json.loads(raw), cfg)
                finally:
                    sender.cancel()
        except Exception as e:
            STATE.update(relay="down", error=str(e)[:200], online=[])
            publish("status")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


async def _relay_send(ws, cfg):
    while True:
        with db.conn() as c:
            c.execute("DELETE FROM chat_outbox WHERE created<?", (time.time() - 7 * 86400,))  # the folder delivered those
            rows = c.execute("SELECT n, data, notify FROM chat_outbox ORDER BY n LIMIT 50").fetchall()
        for r in rows:
            data = base64.b64encode(seal(cfg, r["data"].encode())).decode()
            await ws.send(json.dumps({"t": "env", "ref": r["n"], "data": data, "notify": bool(r["notify"])}))
        try:
            name = await asyncio.wait_for(_TYPING_OUT.get(), timeout=1.0)
            await ws.send(json.dumps({"t": "eph", "data": base64.b64encode(seal(cfg, json.dumps({"typing": name}).encode())).decode()}))
        except asyncio.TimeoutError:
            pass
        if rows:
            await asyncio.sleep(0.5)


async def _relay_receive(m: dict, cfg):
    t = m.get("t")
    if t == "stored":
        with db.conn() as c:
            c.execute("DELETE FROM chat_outbox WHERE n=?", (m.get("ref"),))
    elif t == "env":
        try:
            body = json.loads(unseal(cfg, base64.b64decode(m["data"])))
        except Exception:
            body = {}
        row = body.get("row")
        if row and upsert(row):
            publish("change")
            if row.get("author") != me() and not row.get("deleted") and row.get("updated") == row.get("created"):
                notify_new(row)
            att = row.get("attachment")
            if att and att.get("blob"):
                asyncio.create_task(fetch_blob(att["blob"]))
        if m.get("seq"):
            _seq(max(_seq(), int(m["seq"])))
    elif t == "eph":
        try:
            body = json.loads(unseal(cfg, base64.b64decode(m["data"])))
            if body.get("typing") and body["typing"] != me():
                STATE["typing"][body["typing"]] = time.time()
                publish("status")
        except Exception:
            pass
    elif t == "presence":
        from . import sync
        STATE["online"] = [d for d in m.get("online", []) if d != sync.device()["id"]]
        publish("status")


# ---------------------------------------------------------------- notifications (also for messages that came via the folder)
NOTIFIED = {"ts": time.time()}


def notify_new(row: dict):
    NOTIFIED["ts"] = max(NOTIFIED["ts"], row["created"])
    if SUBSCRIBERS:
        return  # a chat window is open: it shows the message itself
    body = row.get("text") or {"image": "📷 Photo", "voice": "🎤 Voice note", "file": "📎 File"}.get(row.get("kind"), "New message")
    notify.show(f"💌 {row.get('author') or 'New message'}", body[:180])


async def folder_watch():
    """Messages that arrive through the shared folder (relay down) get a notification too."""
    while True:
        await asyncio.sleep(3)
        try:
            with db.conn() as c:
                rows = [_row(r) for r in c.execute("SELECT * FROM messages WHERE author<>? AND created>? AND deleted=0 ORDER BY created",
                                                   (me(), NOTIFIED["ts"]))]
            for row in rows:
                notify_new(row)
                publish("change")
        except Exception:
            pass


def pair(code: str = "", relay: str = "", name: str = "") -> dict:
    changes = {}
    if relay:
        relay = relay.strip().rstrip("/")
        if not re.match(r"^https?://", relay):
            relay = "https://" + relay
        changes["chat_relay"] = relay
    if code is not None and code != "":
        if len(norm_code(code)) != 16:
            raise ValueError("A pairing code has 16 letters and numbers, like ABCD-EFGH-JKLM-NPQR.")
        changes["chat_code"] = "-".join(norm_code(code)[i:i + 4] for i in range(0, 16, 4))
    if name:
        changes["chat_name"] = name.strip()[:40]
    save_settings(changes)
    if "chat_code" in changes:
        _seq(0)
    publish("status")
    return status()
