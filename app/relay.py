"""PC relay: a server that YouTube blocks borrows the owner's own PC.

YouTube asks cloud servers to "prove they aren't a robot", while the same video opens fine on a home PC. So the server
(settings: relay_token, no relay_url) puts a request in a small queue, and A.F.R.A on the owner's PC (settings: relay_url = the
server, the same relay_token) keeps asking the server "anything for me?". The PC is the one that calls out, so it needs no open
port and no router change. When there is a request, the PC fetches the captions (small JSON) or downloads the video and
sends it up, and the server carries on exactly as if it had fetched them itself.

Both ends share one secret (relay_token) that is sent in the x-relay-token header. If the PC is off, asleep or also blocked,
ask() fails with a plain message and the page offers the manual way (open the video, paste its transcript).
"""
import secrets
import shutil
import threading
import time
from pathlib import Path

import httpx

from .config import DATA_DIR, get_settings

DIR = DATA_DIR / "relay"
ONLINE_SECS = 45        # the PC counts as connected this long after its last "anything for me?"
POLL_WAIT = 20          # seconds the server holds one such question open
PICKUP_SECS = 60        # a request nobody picked up in this time fails
ASK_WAIT = {"captions": 240, "video": 25 * 60}
MAX_BYTES = 650_000_000

OFFLINE = "Your PC isn't connected to this server right now (is A.F.R.A running on it, and awake?)."
NOT_SET = "The PC relay isn't set up on this server."

REQS: dict[str, dict] = {}
_LOCK = threading.RLock()
_SEEN = [0.0]


def _now() -> float:
    return time.time()  # tests move this clock


# ---------------------------------------------------------------- the server side
def enabled() -> bool:
    """This copy of A.F.R.A is a server that can borrow a PC (it has the secret and is not itself a PC pointing at a server)."""
    s = get_settings()
    return bool((s.get("relay_token") or "").strip()) and not (s.get("relay_url") or "").strip()


def token_ok(tok) -> bool:
    want = (get_settings().get("relay_token") or "").strip()
    return bool(want) and secrets.compare_digest(str(tok or "").encode("utf-8"), want.encode("utf-8"))


def seen():
    _SEEN[0] = _now()


def online() -> bool:
    return _now() - _SEEN[0] < ONLINE_SECS


def status() -> dict:
    return {"enabled": enabled(), "online": enabled() and online()}


def claim() -> dict | None:
    """The oldest request nobody has taken, now marked as taken."""
    with _LOCK:
        for r in sorted(REQS.values(), key=lambda r: r["created"]):
            if r["state"] == "pending":
                r.update(state="claimed", claimed=_now())
                return {"id": r["id"], "kind": r["kind"], "video_id": r["video_id"]}
    return None


def get(rid: str) -> dict:
    r = REQS.get(rid)
    if not r:
        raise ValueError("That relay request no longer exists.")
    return r


def progress(rid: str, step: str):
    with _LOCK:
        get(rid)["step"] = str(step)[:200]


def finish(rid: str, data=None, error: str = ""):
    with _LOCK:
        r = get(rid)
        if error:
            r.update(state="error", error=str(error)[:400])
        else:
            r.update(state="done", data=data)


def receive_path(rid: str, ext: str) -> Path:
    """Where the PC's upload of the video is written."""
    get(rid)
    ext = "".join(c for c in (ext or "mp4").lower() if c.isalnum())[:5] or "mp4"
    folder = DIR / rid
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"video.{ext}"


def ask(kind: str, video_id: str, folder: Path | None = None, step=lambda s: None):
    """Have the owner's PC fetch "captions" (returns the dict get_captions returns) or the "video" (moved into folder, returns its path).
    ValueError with a plain message when it can't."""
    if not enabled():
        raise ValueError(NOT_SET)
    if not online():
        raise ValueError(OFFLINE)
    rid = secrets.token_hex(6)
    r = {"id": rid, "kind": kind, "video_id": video_id, "state": "pending", "step": "", "created": _now(), "claimed": 0.0, "error": "", "data": None}
    with _LOCK:
        REQS[rid] = r
    step("Asking your PC to fetch it…")
    deadline, shown = _now() + ASK_WAIT[kind], ""
    try:
        while r["state"] not in ("done", "error"):
            if _now() > deadline:
                raise ValueError("Your PC took too long to answer.")
            if r["state"] == "pending" and _now() - r["created"] > PICKUP_SECS:
                raise ValueError(OFFLINE)
            if r["step"] and r["step"] != shown:
                shown = r["step"]
                step("Your PC: " + shown)
            time.sleep(0.5 if _now() - r["created"] < 5 else 1)
        if r["state"] == "error":
            raise ValueError(r["error"] or "Your PC couldn't fetch it.")
        if kind == "captions":
            return r["data"]
        src = next(iter(sorted((DIR / rid).glob("video.*"))), None)
        if not src:
            raise ValueError("Your PC didn't send the video.")
        folder.mkdir(parents=True, exist_ok=True)
        dst = folder / src.name
        shutil.move(str(src), str(dst))
        return dst
    finally:
        with _LOCK:
            REQS.pop(rid, None)
        shutil.rmtree(DIR / rid, ignore_errors=True)


# ---------------------------------------------------------------- the PC side
WORKER = {"state": "off", "error": "", "last": 0.0, "done": 0}


def worker_status() -> dict:
    s = get_settings()
    return {**WORKER, "configured": bool((s.get("relay_url") or "").strip() and (s.get("relay_token") or "").strip())}


def _chunks(path: Path, size: int = 1 << 20):
    with open(path, "rb") as fh:
        while True:
            b = fh.read(size)
            if not b:
                return
            yield b


def handle(c: httpx.Client, url: str, job: dict):
    from . import video
    rid, vid = job["id"], job["video_id"]
    last = [0.0]

    def prog(s):
        if _now() - last[0] < 2:
            return
        last[0] = _now()
        try:
            c.post(f"{url}/api/relay/{rid}/progress", json={"step": s}, timeout=10)
        except httpx.HTTPError:
            pass
    tmp = DIR / f"_out-{rid}"
    try:
        if job["kind"] == "captions":
            data = video.local_captions(vid, prog)
            c.post(f"{url}/api/relay/{rid}/result", json={"data": data}, timeout=60).raise_for_status()
        else:
            f = video.local_download(vid, tmp, prog)
            last[0] = 0
            prog("Sending the video to the server…")
            c.put(f"{url}/api/relay/{rid}/file", params={"ext": f.suffix.lstrip(".")}, content=_chunks(f),
                  timeout=httpx.Timeout(30, read=1800, write=1800)).raise_for_status()
        WORKER["done"] += 1
    except Exception as e:  # tell the server why, so the person sees it
        msg = str(e) if isinstance(e, ValueError) else f"The PC couldn't fetch it ({e.__class__.__name__})."
        try:
            c.post(f"{url}/api/relay/{rid}/result", json={"error": msg}, timeout=30)
        except httpx.HTTPError:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def worker_loop():
    """Runs for ever on the PC: while relay_url and relay_token are set, answer the server's requests."""
    while True:
        s = get_settings()
        url, tok = (s.get("relay_url") or "").strip().rstrip("/"), (s.get("relay_token") or "").strip()
        if not url or not tok:
            WORKER.update(state="off", error="")
            time.sleep(10)
            continue
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            with httpx.Client(timeout=POLL_WAIT + 20, headers={"x-relay-token": tok}) as c:
                while True:
                    cur = get_settings()
                    if (cur.get("relay_url") or "").strip().rstrip("/") != (s.get("relay_url") or "").strip().rstrip("/") or (cur.get("relay_token") or "").strip() != tok:
                        break  # settings changed: start again with the new ones
                    r = c.get(url + "/api/relay/next", params={"wait": POLL_WAIT})
                    if r.status_code in (403, 404):
                        WORKER.update(state="refused", error="The server doesn't accept this token (or has no relay switched on).")
                        time.sleep(30)
                        break
                    r.raise_for_status()
                    WORKER.update(state="connected", error="", last=_now())
                    job = r.json()
                    if job.get("id"):
                        handle(c, url, job)
        except Exception as e:
            WORKER.update(state="offline", error=f"{e.__class__.__name__}: {str(e)[:160]}")
            time.sleep(10)


def start_worker():
    threading.Thread(target=worker_loop, daemon=True, name="afra-relay-worker").start()
