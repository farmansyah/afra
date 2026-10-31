"""Video summary: paste YouTube links (and add documents), get study notes with screenshots, review them, export them.

For your own study notes. A video is downloaded only temporarily to take screenshots, then deleted; only the frame images stay.

A draft (one JSON file in data/video/, with its frames in data/video/<id>/frames/) carries everything between visits:
  options    detail (short / normal / detailed), language, frames mode (none / ai / scenes / interval), max frames, timestamp links, ask
  materials  what is summarised: YouTube videos and A.F.R.A documents / uploaded files, each with its own status row
  questions  the (0-4) questions the AI wanted answered before the long steps, and answers (one round per run)
  summary    tldr, takeaways [{text, t, m}], sections [{id, title, start, m, refs, notes, frame_at, frames: [{id, t, m, file, keep}]}]
Several materials give ONE combined resume (sections grouped by theme, every point tagged with its source) or one resume per material
(then each material is its own draft).

The pipeline is one background job that can be started again at any point: it skips what the draft already holds.
  1. details + captions with yt-dlp for each video (no cookies, no login; a private, members-only, age-restricted or caption-less
     video is marked failed with a plain reason and the others go on)
  2. clarify: one cheap AI step may ask up to 4 questions; the job then waits ("questions") until they are answered or skipped
  3. the summary: long material is cut into chunks and digested first, then merged
  4. screenshots: each video (720p at most, no sound) is downloaded to data/video/<id>/cache/<material>, frames are cut out with
     ffmpeg (the copy from the imageio-ffmpeg package, or ffmpeg on the PATH), near-duplicates are dropped
The cached videos stay for 30 minutes after the last use (so "pick another moment" is instant) and are deleted when the person
finishes, deletes the draft, or the time runs out.

The AI steps work in API mode and in Claude-app mode (handoff.py) like every other AI button. Everything else is plain code,
so it is tested offline. AFRA_VIDEO_FAKE_LLM=1 swaps the AI for a clearly marked fake (tests and smoke runs only).
"""
import asyncio
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import db, handoff, tugas
from . import google_api as g
from .config import DATA_DIR, get_settings, replace_file

DIR = DATA_DIR / "video"
NOTICE = "For your own study notes. Videos are downloaded only temporarily to take screenshots."
NO_GOOGLE = "Connect a Google account first: Settings › Google."
VID_RE = re.compile(r"^[0-9a-f]{10}$")
YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
MAX_MATERIALS = 10              # videos + documents in one run
MAX_SECONDS = 3 * 3600          # a longer video is refused
CACHE_MINUTES = 30              # the downloaded video is kept this long after its last use
DOWNLOAD_LIMIT = 15 * 60        # seconds A.F.R.A waits for one download
MAX_BYTES = 600_000_000
DOC_TYPES = (".txt", ".md", ".markdown", ".docx", ".pdf")
DOC_CAP = 60000                 # characters kept per document
SCENE_THRESHOLD = 0.3
SAME_FRAME = 4.0                # mean difference (0-255) of two 16x16 thumbnails below which a frame counts as a duplicate
DETAIL = {
    "short": "3 to 5 sections; each section's notes are one or two sentences",
    "normal": "5 to 8 sections; each section's notes are a short paragraph or 3 to 5 bullets",
    "detailed": "8 to 14 sections; each section's notes are 4 to 8 bullets with the specific facts, numbers and examples",
}
FRAME_MODES = ("none", "ai", "scenes", "interval")
JOBS: dict[str, dict] = {}
_LOCK = threading.RLock()
_TIMERS: dict[str, threading.Timer] = {}


# ---------------------------------------------------------------- the links
def parse_url(url) -> str:
    """The 11-character video id of a YouTube link, or ValueError with a plain message. Playlists are not supported."""
    s = (url or "").strip() if isinstance(url, str) else ""
    if not s:
        raise ValueError("Paste a YouTube link first.")
    if not re.match(r"^[a-z]+://", s, re.I):
        s = "https://" + s
    u = urlparse(s)
    host = (u.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    q = parse_qs(u.query)
    parts = [p for p in u.path.split("/") if p]
    if host not in ("youtube.com", "m.youtube.com", "music.youtube.com", "youtube-nocookie.com", "youtu.be"):
        raise ValueError("That isn't a YouTube link. Paste one like https://www.youtube.com/watch?v=... or https://youtu.be/...")
    vid = ""
    if host == "youtu.be":
        vid = parts[0] if parts else ""
    elif parts and parts[0] == "playlist":
        raise ValueError("Playlists aren't supported. Open one video and paste its link.")
    elif parts and parts[0] == "watch":
        vid = (q.get("v") or [""])[0]
    elif len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
        vid = parts[1]
    if not YT_ID.match(vid):
        if "list" in q or (parts and parts[0] in ("channel", "c", "user", "feed")) or (parts and parts[0].startswith("@")):
            raise ValueError("That is a playlist or channel link. Playlists and channels aren't supported: open one video and paste its link.")
        raise ValueError("A.F.R.A couldn't find a video in that link. Paste a link like https://www.youtube.com/watch?v=... or https://youtu.be/...")
    return vid


def parse_links(text) -> tuple[list[str], list[str]]:
    """Many links, one per line (or separated by spaces): (video ids without duplicates, plain messages about links that were skipped)."""
    ids, problems = [], []
    for tok in re.split(r"[\s,;]+", text or ""):
        if not tok or not re.search(r"://|youtu|\.[a-z]{2,}/", tok, re.I):
            continue  # not a link: ignored
        try:
            v = parse_url(tok)
        except ValueError as e:
            problems.append(f"{tok[:60]}: {e}")
            continue
        if v not in ids:
            ids.append(v)
    return ids, problems


def mmss(t) -> str:
    t = max(0, int(t or 0))
    h, m, s = t // 3600, t % 3600 // 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def link(video_id: str, t) -> str:
    return f"https://youtu.be/{video_id}?t={max(0, int(t or 0))}"


# ---------------------------------------------------------------- captions
def _clean_cue(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text).strip()


def _secs(ts: str) -> float:
    n = 0.0
    for p in ts.replace(",", ".").split(":"):
        n = n * 60 + float(p)
    return n


def parse_vtt(text: str) -> list[list]:
    """WebVTT -> [[seconds, text], ...]. YouTube's automatic captions roll: every cue repeats the line before it, which is dropped."""
    out: list[list] = []
    prev_lines: list[str] = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n")):
        lines = block.strip().split("\n")
        i = next((k for k, ln in enumerate(lines) if "-->" in ln), -1)
        if i < 0:
            continue
        m = re.match(r"\s*([\d:.,]+)\s*-->", lines[i])
        if not m:
            continue
        try:
            start = _secs(m.group(1))
        except ValueError:
            continue
        cur = [c for c in (_clean_cue(x) for x in lines[i + 1:]) if c]
        fresh = [c for c in cur if c not in prev_lines]
        prev_lines = cur or prev_lines
        line = " ".join(fresh).strip()
        if line and not (out and out[-1][1] == line):
            out.append([round(start, 2), line])
    return out


def parse_json3(text: str) -> list[list]:
    """YouTube's json3 captions -> [[seconds, text], ...]."""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    out: list[list] = []
    for ev in data.get("events") or []:
        segs = ev.get("segs")
        if not segs or "tStartMs" not in ev:
            continue
        line = _clean_cue("".join(s.get("utf8", "") for s in segs))
        if line and not (out and out[-1][1] == line):
            out.append([round(ev["tStartMs"] / 1000, 2), line])
    return out


def parse_captions(name: str, text: str) -> list[list]:
    return parse_json3(text) if name.lower().endswith(".json3") else parse_vtt(text)


def transcript_lines(segs: list[list], span: int = 15) -> list[list]:
    """Short caption pieces joined into lines of about `span` seconds: [[start, text], ...] (fewer tokens for the AI)."""
    out: list[list] = []
    for t, txt in segs:
        if out and t - out[-1][0] < span:
            out[-1][1] += " " + txt
        else:
            out.append([t, txt])
    return out


def chunk_transcript(segs: list[list], max_chars: int) -> list[dict]:
    """The transcript cut into parts of at most about max_chars: [{"start", "end", "text"}] with a [MM:SS] marker on every line."""
    chunks, cur, size = [], [], 0
    lines = transcript_lines(segs)
    for t, txt in lines:
        row = f"[{mmss(t)}] {txt}"
        if cur and size + len(row) > max_chars:
            chunks.append({"start": cur[0][0], "end": t, "text": "\n".join(r for _, r in cur)})
            cur, size = [], 0
        cur.append((t, row))
        size += len(row) + 1
    if cur:
        chunks.append({"start": cur[0][0], "end": lines[-1][0], "text": "\n".join(r for _, r in cur)})
    return chunks


def chunk_text(text: str, max_chars: int) -> list[dict]:
    """A document cut at paragraph breaks into parts of at most about max_chars (no timestamps)."""
    chunks, cur, size = [], [], 0
    for para in re.split(r"\n\s*\n", text):
        if cur and size + len(para) > max_chars:
            chunks.append({"start": 0, "end": 0, "text": "\n\n".join(cur)})
            cur, size = [], 0
        cur.append(para[:max_chars])
        size += len(para) + 2
    if cur:
        chunks.append({"start": 0, "end": 0, "text": "\n\n".join(cur)})
    return chunks


def excerpt(segs: list[list], start: float, end: float, cap: int = 12000) -> str:
    return "\n".join(f"[{mmss(t)}] {x}" for t, x in transcript_lines([s for s in segs if start <= s[0] < end]))[:cap]


# ---------------------------------------------------------------- yt-dlp (the only place that talks to YouTube)
NO_CAPTIONS = ("This video has no captions A.F.R.A can read. Video summary needs captions (the video's own or YouTube's automatic ones). "
               "Pick a video with captions turned on (the CC button in YouTube).")


def friendly_error(msg: str) -> str:
    low = (msg or "").lower()
    if "private video" in low:
        return "This video is private. A.F.R.A can only read public videos."
    if "members-only" in low or "members only" in low or "join this channel" in low:
        return "This video is for channel members only. A.F.R.A can only read public videos."
    if "age" in low and ("confirm" in low or "restricted" in low or "inappropriate" in low):
        return "This video is age-restricted. A.F.R.A doesn't sign in to YouTube, so it can't read it."
    if "not a bot" in low or "sign in to confirm" in low or "http error 429" in low or "too many requests" in low:
        return "YouTube is asking this connection to prove it isn't a robot, so it blocked A.F.R.A. Try again later or from another network."
    if "unavailable" in low or "removed" in low or "does not exist" in low or "terminated" in low:
        return "This video isn't available (removed, blocked in your country, or the link is wrong)."
    if "premieres in" in low or "live event" in low or "will begin" in low:
        return "This is a live stream or premiere that hasn't ended. Try again when it is over."
    if "drm" in low:
        return "This video is protected and can't be read."
    return "YouTube didn't let A.F.R.A fetch this video: " + (re.sub(r"\x1b\[[0-9;]*m", "", msg or "").replace("ERROR:", "").strip()[:200] or "unknown error")


def _ydl(opts: dict):
    try:
        import yt_dlp
    except ImportError:
        raise ValueError("The video library (yt-dlp) isn't installed. Run: pip install yt-dlp imageio-ffmpeg")
    base = {"quiet": True, "no_warnings": True, "noplaylist": True, "ignoreconfig": True, "cachedir": False, "socket_timeout": 20,
            "retries": 3, "noprogress": True}
    return yt_dlp.YoutubeDL({**base, **opts})


def _extract_info(video_id: str) -> dict:
    try:
        with _ydl({"skip_download": True}) as y:
            return y.extract_info("https://www.youtube.com/watch?v=" + video_id, download=False) or {}
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(friendly_error(str(e)))


def pick_caption(info: dict):
    """(language, "manual"|"auto") of the best caption track, or None. The video's own language first; manual before automatic."""
    subs = {k: v for k, v in (info.get("subtitles") or {}).items() if v and k != "live_chat"}
    auto = {k: v for k, v in (info.get("automatic_captions") or {}).items() if v}
    lang = (info.get("language") or "").lower()
    base = lang.split("-")[0]

    def match(d):
        for k in d:
            if lang and k.lower() == lang:
                return k
        for k in d:
            if base and k.lower().split("-")[0] == base and not k.lower().endswith("-orig"):
                return k
        return None
    k = match(subs)
    if k:
        return k, "manual"
    k = next((x for x in auto if x.endswith("-orig")), None) or match(auto)
    if k:
        return k, "auto"
    if subs:
        return ("en" if "en" in subs else next(iter(subs))), "manual"
    if "en" in auto:
        return "en", "auto"
    return None


def _fetch_subs(video_id: str, lang: str, kind: str, folder: Path) -> Path | None:
    folder.mkdir(parents=True, exist_ok=True)
    opts = {"skip_download": True, "writesubtitles": kind == "manual", "writeautomaticsub": kind == "auto", "subtitleslangs": [lang],
            "subtitlesformat": "json3/vtt", "outtmpl": str(folder / "subs")}
    try:
        with _ydl(opts) as y:
            y.download(["https://www.youtube.com/watch?v=" + video_id])
    except Exception as e:
        raise ValueError(friendly_error(str(e)))
    files = sorted(folder.glob("subs*.json3")) or sorted(folder.glob("subs*.vtt"))
    return files[0] if files else None


def get_captions(video_id: str, step=lambda s: None) -> dict:
    """{"meta": {...}, "lang", "kind", "segments"}; ValueError with a plain message when the video can't be read."""
    step("Getting the video details…")
    info = _extract_info(video_id)
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        raise ValueError("This is a live stream or premiere that hasn't ended. Try again when it is over.")
    duration = int(info.get("duration") or 0)
    if duration > MAX_SECONDS:
        raise ValueError(f"This video is {mmss(duration)} long. Videos longer than 3 hours aren't supported.")
    meta = {"title": (info.get("title") or "Untitled video")[:200], "channel": (info.get("channel") or info.get("uploader") or "")[:120],
            "duration": duration, "language": info.get("language") or "", "url": "https://www.youtube.com/watch?v=" + video_id}
    pick = pick_caption(info)
    if not pick:
        raise ValueError(NO_CAPTIONS)
    step("Reading the captions…")
    lang, kind = pick
    tmp = DIR / f"_subs-{secrets.token_hex(4)}"
    try:
        f = _fetch_subs(video_id, lang, kind, tmp)
        segs = parse_captions(f.name, f.read_text(encoding="utf-8", errors="ignore")) if f else []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if len(" ".join(x[1] for x in segs)) < 40:
        raise ValueError(NO_CAPTIONS)
    if not meta["duration"]:
        meta["duration"] = int(segs[-1][0]) + 5
    return {"meta": meta, "lang": lang, "kind": kind, "segments": segs}


# ---------------------------------------------------------------- ffmpeg
def ffmpeg_exe() -> str:
    """The ffmpeg that ships with the imageio-ffmpeg package, else ffmpeg on the PATH."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    raise ValueError("ffmpeg isn't available. Run: pip install imageio-ffmpeg")


def _run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def frame_cmd(ff: str, video: str, t: float, out: str) -> list[str]:
    """One JPEG at second t, at most 1280 pixels wide (about 720p)."""
    return [ff, "-v", "error", "-y", "-ss", f"{max(0.0, t):.2f}", "-i", video, "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2", "-q:v", "3", out]


def scene_cmd(ff: str, video: str, threshold: float = SCENE_THRESHOLD) -> list[str]:
    return [ff, "-hide_banner", "-i", video, "-an", "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"]


def thumb_cmd(ff: str, image: str) -> list[str]:
    return [ff, "-v", "error", "-i", image, "-vf", "scale=16:16:flags=area,format=gray", "-frames:v", "1", "-f", "rawvideo", "-"]


def scene_times(video: str, min_gap: float = 5.0) -> list[float]:
    p = _run(scene_cmd(ffmpeg_exe(), video), 600)
    found = [float(x) for x in re.findall(r"pts_time:([\d.]+)", (p.stderr or b"").decode("utf-8", "ignore"))]
    out: list[float] = []
    for t in found:
        if not out or t - out[-1] >= min_gap:
            out.append(round(t, 2))
    return out


def extract_frame(video: str, t: float, out: Path) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        p = _run(frame_cmd(ffmpeg_exe(), video, t, str(out)), 90)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return p.returncode == 0 and out.exists() and out.stat().st_size > 500


def thumb(image: Path) -> bytes:
    try:
        p = _run(thumb_cmd(ffmpeg_exe(), str(image)), 30)
    except (subprocess.TimeoutExpired, OSError):
        return b""
    return p.stdout if p.returncode == 0 else b""


def too_similar(a: bytes, b: bytes) -> bool:
    if not a or len(a) != len(b):
        return False
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a) < SAME_FRAME


def is_blank(a: bytes) -> bool:
    """A black or white frame (fade, transition): nothing to see."""
    return bool(a) and (max(a) - min(a) < 6) and (sum(a) / len(a) < 14 or sum(a) / len(a) > 245)


def find_video(folder: Path) -> Path | None:
    return next((p for p in sorted(folder.glob("video.*")) if p.suffix not in (".part", ".ytdl")), None) if folder.exists() else None


def _download(video_id: str, folder: Path, step=lambda s: None) -> Path:
    """The video without sound, 720p at most, into folder/video.<ext>."""
    folder.mkdir(parents=True, exist_ok=True)
    started = time.time()

    def hook(d):
        if time.time() - started > DOWNLOAD_LIMIT:
            raise ValueError("The download took too long (over 15 minutes). Try again, or use a screenshot mode that needs fewer frames.")
        tot = d.get("total_bytes") or d.get("total_bytes_estimate")
        if d.get("status") == "downloading" and tot:
            step(f"Downloading the video for screenshots ({int(100 * (d.get('downloaded_bytes') or 0) / tot)}%)…")
    opts = {"format": "bv*[height<=720][ext=mp4]/bv*[height<=720]/b[height<=720]/worst", "outtmpl": str(folder / "video.%(ext)s"),
            "max_filesize": MAX_BYTES, "progress_hooks": [hook], "fragment_retries": 3}
    try:
        with _ydl(opts) as y:
            y.download(["https://www.youtube.com/watch?v=" + video_id])
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(friendly_error(str(e)))
    f = find_video(folder)
    if not f:
        raise ValueError("The video couldn't be downloaded (it may be too large). The notes are kept without screenshots.")
    return f


# ---------------------------------------------------------------- drafts on disk
def _file(vid: str) -> Path:
    if not isinstance(vid, str) or not VID_RE.match(vid):
        raise ValueError("That video summary doesn't exist.")
    return DIR / f"{vid}.json"


def _folder(vid: str) -> Path:
    _file(vid)
    return DIR / vid


def load(vid: str) -> dict:
    try:
        return json.loads(_file(vid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("That video summary no longer exists.")


def save(st: dict) -> dict:
    with _LOCK:
        DIR.mkdir(parents=True, exist_ok=True)
        st["updated"] = time.time()
        f = _file(st["id"])
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        replace_file(tmp, f)
    return st


def mat(st: dict, mid: str) -> dict | None:
    return next((m for m in st["materials"] if m["id"] == mid), None)


def ok_materials(st: dict) -> list[dict]:
    return [m for m in st["materials"] if m["status"] == "ok"]


def multi(st: dict) -> bool:
    return len(ok_materials(st)) > 1


def public(st: dict) -> dict:
    out = {k: v for k, v in st.items() if k != "materials"}
    out["materials"] = [{k: v for k, v in m.items() if k not in ("segments", "text")} | {"words": len((m.get("text") or " ".join(x[1] for x in m.get("segments") or [])).split())}
                        for m in st["materials"]]
    out["cache_left"] = max(0, int(st.get("video_expires", 0) - time.time())) if st.get("video_cached") else 0
    out["notice"] = NOTICE
    return out


def listing() -> list[dict]:
    sweep()
    rows = []
    for f in DIR.glob("*.json") if DIR.exists() else []:
        try:
            st = json.loads(f.read_text(encoding="utf-8"))
            rows.append({"id": st["id"], "title": (st.get("meta") or {}).get("title") or st["materials"][0]["label"], "step": st.get("step", "input"),
                         "updated": st.get("updated", 0), "sections": len((st.get("summary") or {}).get("sections") or []), "materials": len(st["materials"])})
        except (OSError, ValueError, KeyError, IndexError):
            continue
    return sorted(rows, key=lambda r: -r["updated"])


def _options(o: dict, base: dict | None = None) -> dict:
    out = dict(base or {"detail": "normal", "language": "", "frames": "ai", "max_frames": 12, "every": 2, "links": True, "ask": True})
    o = o or {}
    if o.get("detail") in DETAIL:
        out["detail"] = o["detail"]
    if isinstance(o.get("language"), str):
        out["language"] = o["language"].strip()[:40]
    if o.get("frames") in FRAME_MODES:
        out["frames"] = o["frames"]
    for k, lo, hi in (("max_frames", 1, 40), ("every", 1, 30)):
        if k in o:
            try:
                out[k] = max(lo, min(hi, int(o[k])))
            except (TypeError, ValueError):
                pass
    for k in ("links", "ask"):
        if k in o:
            out[k] = bool(o[k])
    return out


def doc_material(label: str, text: str, ref: dict) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError(f"No text found in {label} (a scanned PDF has no text to read).")
    return {"kind": "doc", "label": label[:150], "status": "ok", "note": f"{len(text.split())} words", "text": text[:DOC_CAP],
            "meta": {"title": label[:150], "channel": "", "duration": 0}, **ref}


def new(body: dict, uploads: list[tuple[str, bytes]] | None = None) -> list[dict]:
    """Drafts for one run. body: url_text (links, one per line), doc_ids (A.F.R.A documents), project_id, options, mode ("combined"|"separate").
    uploads: [(file name, bytes)]. Combined (or a single material): one draft. Separate: one draft per material."""
    pid = body.get("project_id") or ""
    if not any(p["id"] == pid for p in db.list_projects()):
        raise ValueError("Pick a project first.")
    ids, problems = parse_links(body.get("url_text") or body.get("url") or "")
    mats = [{"kind": "video", "video_id": v, "url": "https://www.youtube.com/watch?v=" + v, "label": "youtu.be/" + v, "status": "waiting", "note": "", "meta": None,
             "segments": []} for v in ids]
    for did in body.get("doc_ids") or []:
        d = db.get_document(did)
        if not d:
            raise ValueError("One of the documents no longer exists.")
        mats.append(doc_material(d["title"], d["content"], {"doc_id": d["id"]}))
    for name, data in uploads or []:
        n = (name or "file").replace("\\", "/").rsplit("/", 1)[-1]
        if not n.lower().endswith(DOC_TYPES):
            raise ValueError(f"{n}: upload a .txt, .md, .docx or .pdf file.")
        from .gaps import manuscript_text
        mats.append(doc_material(n, manuscript_text(n, data), {"file": n}))
    if not mats:
        raise ValueError(problems[0] if problems else "Paste at least one YouTube link, or add a document.")
    if len(mats) > MAX_MATERIALS:
        raise ValueError(f"A run takes up to {MAX_MATERIALS} materials (videos and documents together); you gave {len(mats)}. Remove some, or make two runs.")
    for i, m in enumerate(mats, 1):
        m["id"] = f"m{i}"
    opts = _options(body.get("options") or {})
    separate = body.get("mode") == "separate" and len(mats) > 1
    groups = [[m] for m in mats] if separate else [mats]
    out = []
    for grp in groups:
        st = {"id": secrets.token_hex(5), "created": time.time(), "step": "input", "project_id": pid, "mode": "separate" if separate else "combined",
              "options": dict(opts), "materials": [dict(m, id=f"m{k}") for k, m in enumerate(grp, 1)], "meta": None, "questions": [], "answers": {},
              "clarify_done": separate or not opts["ask"], "summary": None, "frames_done": False, "video_cached": False, "video_expires": 0, "error": "",
              "doc_id": "", "problems": problems}
        out.append(save(st))
    return out


def delete(vid: str):
    discard_video(vid)
    shutil.rmtree(_folder(vid), ignore_errors=True)
    try:
        _file(vid).unlink()
    except OSError:
        pass


# ---------------------------------------------------------------- the cached videos
def discard_video(vid: str):
    """Delete the downloaded videos (the frame images stay)."""
    t = _TIMERS.pop(vid, None)
    if t:
        t.cancel()
    shutil.rmtree(_folder(vid) / "cache", ignore_errors=True)
    with _LOCK:
        try:
            st = load(vid)
        except ValueError:
            return
        if st.get("video_cached"):
            st.update(video_cached=False, video_expires=0)
            save(st)


def _arm(st: dict):
    """The videos stay 30 minutes after the last use."""
    st.update(video_cached=True, video_expires=time.time() + CACHE_MINUTES * 60)
    t = _TIMERS.pop(st["id"], None)
    if t:
        t.cancel()
    t = threading.Timer(CACHE_MINUTES * 60 + 2, sweep)
    t.daemon = True
    _TIMERS[st["id"]] = t
    t.start()


def sweep():
    """Delete every cached video whose time is up (also the ones left by an earlier run of A.F.R.A)."""
    if not DIR.exists():
        return
    for f in DIR.glob("*.json"):
        try:
            st = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        cache = DIR / st.get("id", "-") / "cache"
        if cache.exists() and (not st.get("video_cached") or time.time() > st.get("video_expires", 0)):
            if not any(j["video"] == st["id"] and j["state"] == "working" for j in JOBS.values()):
                discard_video(st["id"])


# ---------------------------------------------------------------- AI plumbing
def _fake_llm() -> bool:
    return os.environ.get("AFRA_VIDEO_FAKE_LLM") == "1"


def ensure_ai():
    """API mode needs a model and a key; in Claude mode the hand-off layer answers."""
    if _fake_llm() or get_settings().get("ai_mode") == "claude":
        return
    from .llm import LLMError, resolve
    provider, base, key, model = resolve("main")
    if not model or not (key or re.search(r"localhost|127\.0\.0\.1", base or "")):
        raise LLMError("No AI is connected. Add an API key under Settings › AI model, or set A.F.R.A to use the Claude app.")


async def ask_json(prompt: str, system: str, check):
    """One AI call whose answer must pass `check`; asked once more when it does not."""
    from .llm import complete, extract_json
    sysm = system + "\n\nRespond with valid JSON only. No commentary."
    note = ""
    for _ in (1, 2):
        text = await complete(prompt + note, system=sysm, task="video summary")
        try:
            return check(extract_json(text))
        except ValueError as e:
            note = f"\n\nIMPORTANT: your previous answer could not be used ({e}). Return ONLY valid JSON in exactly the format described."
    raise ValueError("The AI's answer couldn't be read as a summary. Try again, or choose a shorter level of detail.")


SYSTEM = "You turn source material (video transcripts, documents) into clear study notes for a student."
SECTION_JSON = '{"title": "...", "start": seconds, "notes": "markdown", "frame_at": seconds or null}'


def m_text(m: dict) -> str:
    """The text of a material as the AI reads it: a video's transcript with [MM:SS] markers, a document as it is."""
    if m["kind"] == "video":
        return "\n".join(f"[{mmss(t)}] {x}" for t, x in transcript_lines(m["segments"]))
    return m["text"]


def m_duration(m: dict) -> int:
    return int((m.get("meta") or {}).get("duration") or 0)


def m_head(m: dict) -> str:
    me = m["meta"] or {}
    if m["kind"] == "video":
        return f"video \"{me.get('title', m['label'])}\" by {me.get('channel') or 'unknown'}, length {mmss(me.get('duration'))}"
    return f"document \"{m['label']}\""


def answers_text(st: dict) -> str:
    a = [f"- {v['q']} -> {v['a']}" for v in (st.get("answers") or {}).values() if v.get("a")]
    return "What the reader said about the purpose and focus (follow it):\n" + "\n".join(a) + "\n" if a else ""


def rules(st: dict, video: bool = True) -> str:
    o = st["options"]
    lang = o.get("language") or "the same language as the source"
    frames = (f"For up to {o['max_frames']} sections, set frame_at to a second inside that section where the screen probably shows something worth a screenshot "
              "(a slide, diagram, chart, demo, code or a result). Use null for sections where someone is only talking.") if o["frames"] == "ai" and video else \
        "Set frame_at to null for every section."
    return (f"Level of detail: {DETAIL[o['detail']]}. Language of the notes: {lang}.\n"
            "Seconds are whole numbers taken from the [MM:SS] or [H:MM:SS] markers of the transcript; a document has no seconds (use 0). "
            "Use only what the sources say: never invent facts, names or numbers. Notes are markdown: short paragraphs or lines starting with \"- \".\n"
            + answers_text(st) + frames)


# ---------------------------------------------------------------- clarify (0 to 4 questions, once per run)
def validate_questions(raw) -> list[dict]:
    if not isinstance(raw, dict) or not isinstance(raw.get("questions", []), list):
        raise ValueError("no question list in the answer")
    out = []
    for q in raw.get("questions") or []:
        if not isinstance(q, dict) or not str(q.get("question") or "").strip():
            continue
        ch = [str(c).strip()[:80] for c in (q.get("choices") or []) if str(c).strip()][:4]
        out.append({"id": f"q{len(out) + 1}", "question": str(q["question"]).strip()[:240], "choices": ch})
    return out[:4]


def fake_questions() -> list[dict]:
    """TEST ONLY: AFRA_VIDEO_FAKE_QUESTIONS=1 makes the fake AI ask one question so the form can be tried."""
    if os.environ.get("AFRA_VIDEO_FAKE_QUESTIONS") != "1":
        return []
    return [{"id": "q1", "question": "[FAKE] What are these notes for?", "choices": ["Exam notes", "A presentation", "Research"]}]


async def clarify(st: dict) -> list[dict]:
    """The AI looks at the materials and the options and asks only what it genuinely needs; [] when nothing is needed."""
    if _fake_llm():
        return fake_questions()
    ensure_ai()
    o = st["options"]
    mats = "\n".join(f"- {m_head(m)}; starts: {(m_text(m)[:700]).replace(chr(10), ' ')}" for m in ok_materials(st))
    known = (f"detail={o['detail']}, language={o.get('language') or 'not chosen (same as the source)'}, screenshots={o['frames']}, "
             f"one {'combined resume' if len(ok_materials(st)) > 1 and st['mode'] == 'combined' else 'resume per source'}")
    prompt = (f"A student wants study notes made from these materials:\n{mats}\n\nThe options are already chosen: {known}.\n"
              "Decide whether you genuinely need answers before writing. Good reasons: the purpose is unclear and would change the notes (exam notes, a presentation, "
              "research), the materials cover several topics and a focus would help, or a part should be left out. Never ask what the options already answer, never ask "
              "trivial things, never ask about screenshots, detail level or combined/separate. Ask 0 to 4 short questions; most of the time 0 or 1 is right.\n"
              "Return JSON: {\"questions\": [{\"question\": \"...\", \"choices\": [\"2 to 4 short suggested answers\"]}]}. If you need nothing, return {\"questions\": []}.")
    return await ask_json(prompt, SYSTEM, validate_questions)


def answer(vid: str, answers: dict) -> dict:
    """Save the answers ({question id: text}; blank or missing = skipped). The page then starts the job again."""
    with _LOCK:
        st = load(vid)
        qs = {q["id"]: q for q in st.get("questions") or []}
        st["answers"] = {k: {"q": qs[k]["question"], "a": str(v).strip()[:400]} for k, v in (answers or {}).items()
                         if k in qs and str(v).strip() and str(v).strip().lower() != "you decide"}
        st["step"] = "working"
        return save(st)


# ---------------------------------------------------------------- summary
def _int_t(v, duration: int, default=None):
    try:
        t = int(float(v))
    except (TypeError, ValueError):
        return default
    return max(0, min(t, max(0, duration - 1))) if duration else max(0, t)


def _sections(raw_sections, duration: int) -> list[dict]:
    secs = []
    for s in raw_sections or []:
        if not isinstance(s, dict):
            continue
        start = _int_t(s.get("start"), duration, 0)
        title, notes = str(s.get("title") or "").strip(), str(s.get("notes") or "").strip()
        if not (title or notes):
            continue
        secs.append({"title": title[:160] or "Untitled section", "start": start, "notes": notes[:6000], "frame_at": _int_t(s.get("frame_at"), duration)})
    return secs


def validate_summary(raw, duration: int, need_takeaways: bool = True) -> dict:
    """One material's answer as {"tldr", "takeaways", "sections"} with every timestamp a whole second inside the video; ValueError if unusable."""
    if not isinstance(raw, dict):
        raise ValueError("the answer is not a JSON object")
    secs = _sections(raw.get("sections"), duration)
    if not secs:
        raise ValueError("no sections in the answer")
    secs.sort(key=lambda s: s["start"])
    uniq = []
    for s in secs:
        if not (uniq and s["start"] == uniq[-1]["start"] and duration):
            uniq.append(s)
    for i, s in enumerate(uniq):
        end = uniq[i + 1]["start"] if i + 1 < len(uniq) else (duration or s["start"] + 1)
        if duration and s["frame_at"] is not None and not (s["start"] <= s["frame_at"] < max(end, s["start"] + 1)):
            s["frame_at"] = None  # a frame outside its own section is a mistake
        if not duration:
            s["frame_at"] = None
    out = {"sections": uniq}
    if need_takeaways:
        tl = str(raw.get("tldr") or "").strip()
        if not tl:
            raise ValueError("no tldr in the answer")
        tk = [{"text": str(k["text"]).strip()[:400], "t": _int_t(k.get("t"), duration, uniq[0]["start"])}
              for k in raw.get("takeaways") or [] if isinstance(k, dict) and str(k.get("text") or "").strip()]
        if not tk:
            raise ValueError("no takeaways in the answer")
        out.update(tldr=tl[:2000], takeaways=tk[:10])
    return out


def validate_points(raw, duration: int) -> list[dict]:
    pts = [{"text": str(k["text"]).strip()[:400], "t": _int_t(k.get("t"), duration, 0)} for k in (raw or {}).get("points") or []
           if isinstance(k, dict) and str(k.get("text") or "").strip()]
    return pts


def validate_combined(raw, tags: dict[str, dict]) -> dict:
    """The combined answer: every takeaway and section names its source (S1, S2...). tags: {"S1": material}. Returns tldr, title, takeaways, sections with m/refs."""
    if not isinstance(raw, dict):
        raise ValueError("the answer is not a JSON object")
    first = next(iter(tags))

    def src(v):
        return tags[v if v in tags else first]

    def ref(tag, t):
        m = src(tag)
        return {"m": m["id"], "t": _int_t(t, m_duration(m), 0) if m["kind"] == "video" else 0}
    secs = []
    for s in raw.get("sections") or []:
        if not isinstance(s, dict) or not (str(s.get("title") or "").strip() or str(s.get("notes") or "").strip()):
            continue
        p = ref(s.get("src"), s.get("start"))
        m = mat_of(tags, p["m"])
        also = [ref(a.get("src"), a.get("t")) for a in s.get("also") or [] if isinstance(a, dict)]
        also = [a for a in also if a != p]
        fa = _int_t(s.get("frame_at"), m_duration(m)) if m["kind"] == "video" else None
        secs.append({"title": str(s.get("title") or "Untitled section").strip()[:160], "start": p["t"], "m": p["m"], "refs": [p] + also,
                     "notes": str(s.get("notes") or "").strip()[:6000], "frame_at": fa})
    if not secs:
        raise ValueError("no sections in the answer")
    tl = str(raw.get("tldr") or "").strip()
    tk = [{"text": str(k["text"]).strip()[:400], **ref(k.get("src"), k.get("t"))} for k in raw.get("takeaways") or []
          if isinstance(k, dict) and str(k.get("text") or "").strip()]
    if not tl or not tk:
        raise ValueError("no tldr or takeaways in the answer")
    return {"title": str(raw.get("title") or "").strip()[:150], "tldr": tl[:2000], "takeaways": tk[:10], "sections": secs}


def mat_of(tags: dict, mid: str) -> dict:
    return next(m for m in tags.values() if m["id"] == mid)


def fake_summary(st: dict, m: dict) -> dict:
    """TEST ONLY (AFRA_VIDEO_FAKE_LLM=1): sections cut at even spacing from the real captions, no AI."""
    if m["kind"] == "doc":
        return {"tldr": "[TEST ONLY: fake summary] " + m["text"][:200], "takeaways": [{"text": "[FAKE] Point", "t": 0}],
                "sections": [{"title": "[FAKE] " + m["label"], "start": 0, "notes": "- [TEST ONLY, no AI was called] " + m["text"][:200], "frame_at": None}]}
    segs = m["segments"]
    dur = max(m_duration(m), int(segs[-1][0]) + 1)
    n = max(2, min(6, dur // 45))
    secs = []
    for i in range(n):
        a, b = dur * i // n, dur * (i + 1) // n
        text = excerpt(segs, a, b, 300) or "(no captions here)"
        secs.append({"title": f"[FAKE] Part {i + 1}", "start": a, "notes": "- [TEST ONLY, no AI was called] " + text.replace("\n", " ")[:200], "frame_at": a + min(6, (b - a) // 2)})
    return {"tldr": "[TEST ONLY: fake summary, no AI was called] " + " ".join(s[1] for s in segs[:5])[:240],
            "takeaways": [{"text": f"[FAKE] Point {i + 1}", "t": s["start"]} for i, s in enumerate(secs)], "sections": secs}


async def summarize_material(st: dict, m: dict, step=lambda s: None) -> dict:
    """One material -> {"tldr", "takeaways", "sections"}. A long one is read in parts, then merged."""
    dur = m_duration(m)
    if _fake_llm():
        return validate_summary(fake_summary(st, m), dur)
    ensure_ai()
    claude = get_settings().get("ai_mode") == "claude"
    cap = 110_000 if claude else 45_000
    video = m["kind"] == "video"
    chunks = chunk_transcript(m["segments"], cap) if video else chunk_text(m["text"], cap)
    style = f"Source: {m_head(m)}.\n{rules(st, video)}"
    if len(chunks) == 1:
        step(f"Writing the summary of {m['label']}…")
        prompt = (f"{style}\n\nReturn JSON: {{\"tldr\": \"2 to 4 sentences\", \"takeaways\": [{{\"text\": \"...\", \"t\": seconds}}] (5 to 10, in order), "
                  f"\"sections\": [{SECTION_JSON}]}}\n\nText:\n{chunks[0]['text']}")
        return await ask_json(prompt, SYSTEM, lambda r: validate_summary(r, dur))
    sections, points = [], []
    for i, c in enumerate(chunks, 1):
        step(f"Reading part {i} of {len(chunks)} of {m['label']}…")
        prompt = (f"{style}\n\nThis is part {i} of {len(chunks)} (from {mmss(c['start'])}). Write the sections for this part only. "
                  f"Return JSON: {{\"points\": [{{\"text\": \"an important point\", \"t\": seconds}}] (3 to 6), \"sections\": [{SECTION_JSON}]}}\n\nText:\n{c['text']}")

        def check(r):
            return validate_summary(r, dur, need_takeaways=False)["sections"], validate_points(r, dur)
        s, p = await ask_json(prompt, SYSTEM, check)
        sections += s
        points += p
    step(f"Putting the parts of {m['label']} together…")
    pl = "\n".join(f"- [{mmss(p['t'])}] {p['text']}" for p in points) or "\n".join(f"- [{mmss(s['start'])}] {s['title']}" for s in sections)
    prompt = (f"Source: {m_head(m)}. Language: {st['options'].get('language') or 'the same as the notes below'}.\n{answers_text(st)}"
              "Here are the important points found in each part. Return JSON: {\"tldr\": \"2 to 4 sentences for the whole source\", "
              "\"takeaways\": [{\"text\": \"...\", \"t\": seconds}] (the 5 to 10 most important, in order; keep each point's own second)}\n\n" + pl[:30000])
    merged = await ask_json(prompt, SYSTEM, lambda r: validate_summary({**r, "sections": [{"title": "x", "start": 0, "notes": "x"}]}, dur))
    return validate_summary({"tldr": merged["tldr"], "takeaways": merged["takeaways"], "sections": sections}, dur)


def digest_text(m: dict, v: dict) -> str:
    """A material's own summary, short enough to sit next to the others in the merge step."""
    rows = [f"Summary: {v['tldr']}"] + [f"- [{mmss(k['t'])}] {k['text']}" for k in v["takeaways"]]
    rows += [f"## [{mmss(s['start'])}] {s['title']}: {s['notes'][:500]}" for s in v["sections"]]
    return "\n".join(rows)[:14000]


def fake_combined(st: dict, tags: dict) -> dict:
    secs, tk = [], []
    for tag, m in tags.items():
        v = fake_summary(st, m)
        for s in v["sections"]:
            secs.append({**s, "src": tag})
        tk += [{**k, "src": tag} for k in v["takeaways"]]
    return {"title": "[FAKE] Combined notes", "tldr": "[TEST ONLY: fake combined summary, no AI was called] " + " | ".join(m["label"] for m in tags.values()),
            "takeaways": tk[:10], "sections": secs}


async def summarize(st: dict, step=lambda s: None) -> dict:
    """The stored summary for the draft. One material: its own summary. Several (combined): per-material digests when they are long, then one merge."""
    mats = ok_materials(st)
    if len(mats) == 1 or st["mode"] == "separate":
        m = mats[0]
        return finish_summary(st, [m], validate_single(await summarize_material(st, m, step), m))
    tags = {f"S{i}": m for i, m in enumerate(mats, 1)}
    if _fake_llm():
        return finish_summary(st, mats, validate_combined(fake_combined(st, tags), tags))
    ensure_ai()
    claude = get_settings().get("ai_mode") == "claude"
    budget = 110_000 if claude else 45_000
    direct = sum(len(m_text(m)) for m in mats) <= budget
    parts = []
    for tag, m in tags.items():
        if direct:
            parts.append(f"=== {tag}: {m_head(m)} ===\n{m_text(m)}")
        else:
            v = await summarize_material(st, m, step)  # a digest of this material alone
            parts.append(f"=== {tag}: {m_head(m)} (its own summary) ===\n{digest_text(m, v)}")
    step("Combining the sources…")
    lang = st["options"].get("language") or "the same language as the sources"
    prompt = (f"{rules(st, any(m['kind'] == 'video' for m in mats))}\n\nWrite ONE combined study resume across all {len(mats)} sources below. "
              "Group the sections by THEME, not by source: a theme may draw on several sources. Every takeaway and every section names its main source "
              "(\"src\": \"S1\") and the second in that source (\"start\" / \"t\"); a section lists other sources that support it in \"also\". "
              f"Language: {lang}. For a document source use 0 for seconds and never set a frame for it. "
              f"Return JSON: {{\"title\": \"a short title for the combined notes\", \"tldr\": \"3 to 5 sentences overview\", "
              f"\"takeaways\": [{{\"text\": \"...\", \"src\": \"S1\", \"t\": seconds}}] (5 to 10), \"sections\": [{{\"title\": \"...\", \"notes\": \"markdown\", "
              f"\"src\": \"S1\", \"start\": seconds, \"also\": [{{\"src\": \"S2\", \"t\": seconds}}], \"frame_at\": seconds in the main source or null}}]}}\n\n" + "\n\n".join(parts))
    return finish_summary(st, mats, await ask_json(prompt, SYSTEM, lambda r: validate_combined(r, tags)))


def validate_single(v: dict, m: dict) -> dict:
    """A one-material summary in the shape finish_summary takes (every section and takeaway carries its material)."""
    return {"title": "", "tldr": v["tldr"], "takeaways": [{**k, "m": m["id"]} for k in v["takeaways"]],
            "sections": [{**s, "m": m["id"], "refs": [{"m": m["id"], "t": s["start"]}]} for s in v["sections"]]}


def finish_summary(st: dict, mats: list[dict], v: dict) -> dict:
    """Validated AI output -> the stored summary (ids on sections, empty frame lists). In AI mode without any frame_at it picks a few itself."""
    secs = [{"id": f"s{i}", "title": s["title"], "start": s["start"], "m": s["m"], "refs": s["refs"], "notes": s["notes"], "frame_at": s["frame_at"], "frames": []}
            for i, s in enumerate(v["sections"], 1)]
    if st["options"]["frames"] == "ai" and not any(s["frame_at"] is not None for s in secs):
        for s in secs:
            m = next(x for x in mats if x["id"] == s["m"])
            if m["kind"] != "video":
                continue
            nxt = next((x["start"] for x in secs if x["m"] == s["m"] and x["start"] > s["start"]), m_duration(m) or s["start"] + 30)
            s["frame_at"] = s["start"] + max(1, min(20, (nxt - s["start"]) // 2))
    if len(mats) == 1:
        title = mats[0]["meta"]["title"]
        meta = {"title": title, "channel": mats[0]["meta"].get("channel", ""), "duration": m_duration(mats[0])}
    else:
        meta = {"title": v.get("title") or f"{mats[0]['label']} and {len(mats) - 1} more", "channel": "", "duration": 0}
    st["meta"] = meta
    return {"tldr": v["tldr"], "takeaways": v["takeaways"], "sections": secs}


def section_context(st: dict, i: int) -> str:
    """The source text a section was written from (for rewriting it)."""
    secs = st["summary"]["sections"]
    s, out = secs[i], []
    for r in s["refs"] or [{"m": s["m"], "t": s["start"]}]:
        m = mat(st, r["m"])
        if not m:
            continue
        if m["kind"] == "doc":
            out.append(f"{m['label']}:\n{m['text'][:6000]}")
        else:
            nxt = next((x["start"] for x in secs[i + 1:] if x["m"] == m["id"] and x["start"] > r["t"]), r["t"] + (600 if len(st["materials"]) == 1 else 180))
            out.append(f"{m['label']}:\n{excerpt(m['segments'], r['t'], nxt, 8000)}")
    return "\n\n".join(out)


async def rewrite_section(vid: str, sid: str, instruction: str) -> dict:
    """The AI rewrites one section (title and notes) from the source text of its own stretch."""
    st = load(vid)
    secs = (st.get("summary") or {}).get("sections") or []
    i = next((k for k, s in enumerate(secs) if s["id"] == sid), -1)
    if i < 0:
        raise ValueError("That section no longer exists.")
    s = secs[i]
    if _fake_llm():
        new_ = {"title": s["title"], "notes": s["notes"] + "\n- [FAKE rewrite] " + (instruction or "")[:80]}
    else:
        ensure_ai()
        lang = st["options"].get("language") or "the same language as the current notes"
        prompt = (f"Rewrite ONE section of the study notes. Instruction: {instruction.strip() or 'Make it clearer and more precise.'}\n"
                  f"Language: {lang}. Use only what the source says. Notes are markdown (short paragraphs or lines starting with \"- \").\n{answers_text(st)}"
                  f"Current section: {json.dumps({'title': s['title'], 'notes': s['notes']}, ensure_ascii=False)}\n\n"
                  f"Return JSON: {{\"title\": \"...\", \"notes\": \"markdown\"}}\n\nSource text of this section:\n{section_context(st, i)}")

        def check(r):
            if not isinstance(r, dict) or not str(r.get("notes") or "").strip():
                raise ValueError("no notes in the answer")
            return {"title": str(r.get("title") or s["title"]).strip()[:160], "notes": str(r["notes"]).strip()[:6000]}
        new_ = await ask_json(prompt, SYSTEM, check)
    with _LOCK:
        st = load(vid)
        for x in st["summary"]["sections"]:
            if x["id"] == sid:
                x.update(new_)
        save(st)
    return {"id": sid, **new_}


# ---------------------------------------------------------------- frames
def owner_section(secs: list[dict], mid: str, t: float) -> int:
    """The section of material `mid` that covers second t: the latest one starting at or before t, else the first one that uses the material."""
    best, first = -1, -1
    for i, s in enumerate(secs):
        for r in s["refs"] or [{"m": s["m"], "t": s["start"]}]:
            if r["m"] != mid:
                continue
            if first < 0:
                first = i
            if r["t"] <= t and (best < 0 or r["t"] >= next(x["t"] for x in secs[best]["refs"] if x["m"] == mid)):
                best = i
    return best if best >= 0 else max(first, 0)


def thin(items: list, n: int) -> list:
    """At most n items, evenly spread, order kept."""
    if len(items) <= n:
        return list(items)
    if n <= 1:
        return items[:1]
    return [items[round(i * (len(items) - 1) / (n - 1))] for i in range(n)]


def frame_plan(st: dict, scenes: dict[str, list[float]] | None = None) -> list[tuple[str, float, int]]:
    """[(material id, second, section index)] of the frames to take, at most max_frames, in the order of the materials and the video."""
    o, secs = st["options"], st["summary"]["sections"]
    vids = [m for m in ok_materials(st) if m["kind"] == "video"]
    plan = []
    if o["frames"] == "ai":
        plan = [(s["m"], float(s["frame_at"]), i) for i, s in enumerate(secs) if s.get("frame_at") is not None and mat(st, s["m"])]
    elif o["frames"] == "interval":
        step = o["every"] * 60
        for m in vids:
            dur = m_duration(m)
            for k in range(max(1, (dur or step) // step + 1)):
                t = max(3.0, float(k * step))
                if not dur or t < dur - 1:
                    plan.append((m["id"], t, owner_section(secs, m["id"], t)))
    elif o["frames"] == "scenes":
        for m in vids:
            plan += [(m["id"], t, owner_section(secs, m["id"], t)) for t in (scenes or {}).get(m["id"], [])]
    order = {m["id"]: i for i, m in enumerate(st["materials"])}
    return thin(sorted(plan, key=lambda p: (order.get(p[0], 0), p[1])), o["max_frames"])


def frame_name(t: float, taken: set[str]) -> str:
    t = int(t)
    base = f"frame-{t // 60:02d}-{t % 60:02d}"
    name, n = base + ".jpg", 2
    while name in taken:
        name, n = f"{base}-{n}.jpg", n + 1
    return name


def take_frames(st: dict, videos: dict[str, Path], plan: list[tuple[str, float, int]], step=lambda s: None) -> tuple[int, int]:
    """Cut the frames out of the videos into the draft. A frame that fails, is blank or repeats the one before it is skipped. Returns (kept, skipped)."""
    folder = _folder(st["id"]) / "frames"
    shutil.rmtree(folder, ignore_errors=True)  # a new run replaces the old screenshots
    folder.mkdir(parents=True, exist_ok=True)
    for s in st["summary"]["sections"]:
        s["frames"] = []
    taken: set[str] = set()
    last: dict[str, bytes] = {}
    kept = skipped = 0
    for n, (mid, t, si) in enumerate(plan, 1):
        step(f"Taking screenshots ({n} of {len(plan)})…")
        video = videos.get(mid)
        if not video:
            skipped += 1
            continue
        dur = m_duration(mat(st, mid))
        t = min(t, max(0, dur - 1)) if dur else t
        name = frame_name(t, taken)
        out = folder / name
        if not extract_frame(str(video), t, out):
            skipped += 1
            out.unlink(missing_ok=True)
            continue
        small = thumb(out)
        if is_blank(small) or (last.get(mid) and too_similar(last[mid], small)):
            out.unlink(missing_ok=True)
            skipped += 1
            continue
        last[mid] = small
        taken.add(name)
        st["summary"]["sections"][si]["frames"].append({"id": "f" + secrets.token_hex(3), "t": int(t), "m": mid, "file": name, "keep": True})
        kept += 1
    for m in st["materials"]:
        if m["kind"] == "video" and m["status"] == "ok" and mid_has_plan(plan, m["id"]):
            c = sum(1 for s in st["summary"]["sections"] for f in s["frames"] if f["m"] == m["id"])
            m["frames_note"] = f"{c} screenshot{'s' if c != 1 else ''}"
    return kept, skipped


def mid_has_plan(plan, mid) -> bool:
    return any(p[0] == mid for p in plan)


def pick_frame(vid: str, sid: str, t: float, replace: str = "", mid: str = "") -> dict:
    """A new frame at second t from the cached video (the 'pick another moment' scrubber); replaces frame `replace` when given."""
    with _LOCK:
        st = load(vid)
        secs = (st.get("summary") or {}).get("sections") or []
        sec = next((s for s in secs if s["id"] == sid), None)
        if not sec:
            raise ValueError("That section no longer exists.")
        old = next((f for f in sec["frames"] if f["id"] == replace), None)
        mid = mid or (old or {}).get("m") or sec["m"]
        m = mat(st, mid)
        if not m or m["kind"] != "video":
            raise ValueError("Screenshots come from videos only.")
        v = find_video(_folder(vid) / "cache" / mid) if st.get("video_cached") else None
        if not v:
            raise ValueError("The video was removed to save space. Press “Get the video again” first.")
        dur = m_duration(m)
        t = max(0, min(int(float(t)), max(0, dur - 1)))
        folder = _folder(vid) / "frames"
        name = frame_name(t, {f["file"] for s in secs for f in s["frames"]})
        if not extract_frame(str(v), t, folder / name):
            (folder / name).unlink(missing_ok=True)
            raise ValueError("A.F.R.A couldn't take a screenshot at that moment. Try a second earlier or later.")
        fr = {"id": "f" + secrets.token_hex(3), "t": t, "m": mid, "file": name, "keep": True}
        if old:
            sec["frames"][sec["frames"].index(old)] = fr
            if not any(f["file"] == old["file"] for s in secs for f in s["frames"]):
                (folder / old["file"]).unlink(missing_ok=True)
        else:
            sec["frames"].append(fr)
        sec["frames"].sort(key=lambda f: (f.get("m", ""), f["t"]))
        _arm(st)
        save(st)
    return fr


def frame_path(vid: str, fid: str) -> Path:
    st = load(vid)
    for s in (st.get("summary") or {}).get("sections") or []:
        for f in s["frames"]:
            if f["id"] == fid:
                p = _folder(vid) / "frames" / f["file"]
                if p.exists():
                    return p
    raise ValueError("That screenshot no longer exists.")


# ---------------------------------------------------------------- the job
def start(vid: str, hid: str | None = None, redo: str = "", batch: list[str] | None = None) -> dict:
    """Run (or resume) the pipeline in the background. redo: "" | "summary" | "frames" | "video" (download the videos again only) | "all".
    batch: more drafts (separate resumes) to run one after the other after this one; returns {"job": id, "jobs": {draft id: job id}}."""
    ids = [vid] + [b for b in (batch or []) if b != vid]
    if redo not in ("", "summary", "frames", "video", "all"):
        raise ValueError("Unknown step.")
    jobs = {}
    for d in ids:
        st = load(d)
        if any(j["video"] == d and j["state"] in ("working", "queued") for j in JOBS.values()):
            raise ValueError("This video is already being worked on.")
        if redo in ("summary", "all"):
            st.update(summary=None, frames_done=False, meta=None if redo == "all" else st.get("meta"))
        if redo == "all":
            for m in st["materials"]:
                if m["kind"] == "video":
                    m.update(status="waiting", note="", segments=[], meta=None)
            st.update(clarify_done=not st["options"].get("ask", True) or st["mode"] == "separate", questions=[], answers={})
        if redo == "frames":
            st["frames_done"] = False
        st.update(step="working", error="")
        save(st)
        jid = secrets.token_hex(5)
        JOBS[jid] = {"id": jid, "video": d, "state": "working" if d == vid else "queued", "step": "Starting…" if d == vid else "Waiting for the others…",
                     "error": "", "started": time.time()}
        jobs[d] = jid
    for k in [k for k, v in JOBS.items() if time.time() - v["started"] > 86400]:
        JOBS.pop(k, None)

    def run():
        token = handoff.CURRENT.set(hid) if hid else None
        try:
            for d in ids:
                J = JOBS[jobs[d]]
                J.update(state="working", step="Starting…")
                try:
                    res = asyncio.run(pipeline(d, lambda s, J=J: J.update(step=s), only_video=(redo == "video")))
                    if res == "questions":
                        J.update(state="questions", step="Waiting for your answers")
                    else:
                        J.update(state="done", step="Done")
                except Exception as e:  # shown in the page
                    msg = str(e) or e.__class__.__name__
                    if not isinstance(e, ValueError) and e.__class__.__name__ != "LLMError":
                        msg = "Something went wrong: " + msg
                    J.update(state="error", error=msg)
                    try:
                        cur = load(d)
                        cur.update(step="review" if cur.get("summary") else "input", error=msg)
                        save(cur)
                    except ValueError:
                        pass
        finally:
            if token:
                handoff.CURRENT.reset(token)
    threading.Thread(target=run, daemon=True).start()
    return {"job": jobs[vid], "jobs": jobs}


def job(jid: str) -> dict:
    j = JOBS.get(jid)
    if not j:
        raise ValueError("Unknown job (A.F.R.A was restarted?). Open the draft again and press Resume.")
    return j


def fetch_materials(st: dict, step) -> None:
    """Captions for every video that isn't read yet. A video that fails is marked with the reason and the others go on."""
    for m in st["materials"]:
        if m["kind"] != "video" or m["status"] == "ok":
            continue
        try:
            got = get_captions(m["video_id"], lambda s, m=m: step(f"{m['label']}: {s}"))
            m.update(meta=got["meta"], label=got["meta"]["title"], segments=got["segments"], status="ok",
                     note=f"Captions found ({'the video’s own' if got['kind'] == 'manual' else 'automatic'}, {got['lang']})")
        except ValueError as e:
            m.update(status="failed", note=str(e))
        save(st)
    if not ok_materials(st):
        reasons = "; ".join(f"{m['label']}: {m['note']}" for m in st["materials"])
        raise ValueError(("Nothing could be read. " if len(st["materials"]) > 1 else "") + (reasons if len(st["materials"]) > 1 else st["materials"][0]["note"]))


def _cached(st: dict, m: dict, step) -> Path:
    cache = _folder(st["id"]) / "cache" / m["id"]
    v = find_video(cache) if st.get("video_cached") else None
    if v:
        return v
    shutil.rmtree(cache, ignore_errors=True)
    v = _download(m["video_id"], cache, lambda s: step(f"{m['label']}: {s}"))
    with _LOCK:
        cur = load(st["id"])
        _arm(cur)
        save(cur)
        st.update(video_cached=True, video_expires=cur["video_expires"])
    return v


async def pipeline(vid: str, step=lambda s: None, only_video: bool = False):
    """Returns "questions" when it stops to let the person answer, else None."""
    st = load(vid)
    if only_video:
        for m in ok_materials(st):
            if m["kind"] == "video":
                await asyncio.to_thread(_cached, st, m, step)
        return None
    if not st.get("summary") and any(m["status"] != "ok" for m in st["materials"] if m["kind"] == "video"):
        await asyncio.to_thread(fetch_materials, st, step)
    if not st.get("summary") and not st.get("clarify_done"):
        step("Checking whether anything needs your input…")
        qs = await clarify(st)
        st.update(clarify_done=True, questions=qs)  # one round per run
        if qs:
            st["step"] = "questions"
            save(st)
            return "questions"
        save(st)
    if not st.get("summary"):
        st["summary"] = await summarize(st, step)
        st["frames_done"] = False
        save(st)
    if st["options"]["frames"] != "none" and not st.get("frames_done") and any(m["kind"] == "video" for m in ok_materials(st)):
        try:
            await asyncio.to_thread(frames_step, st, step)
        except ValueError as e:
            st["frames_error"] = str(e)  # the notes are kept; the page offers "Get the screenshots again"
            save(st)
    st.update(step="review", error="")
    save(st)
    return None


def frames_step(st: dict, step):
    ffmpeg_exe()  # fail now, before any download
    videos, scenes = {}, {}
    for m in ok_materials(st):
        if m["kind"] != "video":
            continue
        try:
            videos[m["id"]] = _cached(st, m, step)
            if st["options"]["frames"] == "scenes":
                step(f"{m['label']}: looking for scene changes…")
                scenes[m["id"]] = scene_times(str(videos[m["id"]]))
        except ValueError as e:
            m["frames_note"] = "No screenshots: " + str(e)
    plan = frame_plan(st, scenes)
    kept, skipped = take_frames(st, videos, plan, step) if plan and videos else (0, 0)
    err = "" if kept or not plan else "No usable screenshots could be taken."
    if not videos and plan:
        err = "; ".join(m.get("frames_note", "") for m in st["materials"] if m.get("frames_note", "").startswith("No screenshots")) or err
    st.update(frames_done=bool(videos) or not plan, frames_error=err, frames_skipped=skipped)
    with _LOCK:
        _arm(st)
        save(st)


# ---------------------------------------------------------------- editing
def patch(vid: str, body: dict) -> dict:
    """The review page's edits: text, the order of sections, which frames are kept. Frames themselves can't be invented from here."""
    with _LOCK:
        st = load(vid)
        sm = st.get("summary")
        if not sm:
            raise ValueError("There is no summary to edit yet.")
        if "tldr" in body:
            sm["tldr"] = str(body["tldr"])[:4000]
        if "takeaways" in body and isinstance(body["takeaways"], list):
            sm["takeaways"] = [{"text": str(k.get("text") or "").strip()[:400], "t": _int_t(k.get("t"), 0, 0), "m": k.get("m") if mat(st, k.get("m")) else sm["sections"][0]["m"]}
                               for k in body["takeaways"] if isinstance(k, dict) and str(k.get("text") or "").strip()]
        if "sections" in body and isinstance(body["sections"], list):
            old = {s["id"]: s for s in sm["sections"]}
            out = []
            for s in body["sections"]:
                o = old.get(s.get("id")) if isinstance(s, dict) else None
                if not o or o in out:
                    continue
                o = dict(o)
                o["title"] = str(s.get("title", o["title"]))[:160]
                o["notes"] = str(s.get("notes", o["notes"]))[:6000]
                flags = {f.get("id"): bool(f.get("keep", True)) for f in s.get("frames") or [] if isinstance(f, dict)}
                o["frames"] = [{**f, "keep": flags.get(f["id"], f["keep"])} for f in o["frames"]]
                out.append(o)
            if not out:
                raise ValueError("Keep at least one section.")
            sm["sections"] = out
        if isinstance(body.get("title"), str) and body["title"].strip():
            st["meta"]["title"] = body["title"].strip()[:200]
        if isinstance(body.get("options"), dict):
            st["options"] = _options(body["options"], st["options"])
        return save(st)


# ---------------------------------------------------------------- output
def _kept(sec: dict) -> list[dict]:
    return [f for f in sec["frames"] if f.get("keep", True)]


def src_title(st: dict, mid: str) -> str:
    m = mat(st, mid)
    return ((m["meta"] or {}).get("title") if m and m["meta"] else m["label"] if m else "") or "Source"


def ref_parts(st: dict, r: dict) -> tuple[str, str]:
    """(text, url) of a pointer into a source; url is "" for a document or when links are off. One video: just the time. Several: title and time."""
    m = mat(st, r["m"])
    if not m:
        return "", ""
    many = len(st["materials"]) > 1
    if m["kind"] == "doc":
        return m["label"], ""
    short = src_title(st, r["m"])
    short = short if len(short) <= 40 else short[:38] + "…"
    text = f"{short} {mmss(r['t'])}" if many else mmss(r["t"])
    return text, link(m["video_id"], r["t"]) if st["options"].get("links", True) else ""


def ref_md(st: dict, r: dict) -> str:
    text, url = ref_parts(st, r)
    if not text:
        return ""
    if not url and st["options"].get("links", True) is False and mat(st, r["m"])["kind"] == "video":
        return ""
    return f"[{text}]({url})" if url else text


def sources_list(st: dict) -> list[str]:
    rows = []
    for m in st["materials"]:
        if m["status"] != "ok":
            continue
        if m["kind"] == "video":
            me = m["meta"]
            rows.append(f"{me['title']}" + (f" ({me['channel']})" if me.get("channel") else "") + f" – {m['url']}")
        else:
            rows.append(f"{m['label']} (document)")
    return rows


def to_markdown(st: dict, images: bool = False, frame_dir: str = "frames") -> str:
    """The notes as Markdown. images=True adds ![...](frames/frame-MM-SS.jpg) lines under each section."""
    m, sm = st["meta"], st["summary"]
    many = len(st["materials"]) > 1
    first = ok_materials(st)[0] if ok_materials(st) else st["materials"][0]
    head = [x for x in (m.get("channel"), mmss(m["duration"]) if m.get("duration") else "",
                        f"[Watch on YouTube]({first['url']})" if not many and first["kind"] == "video" else "") if x]
    lines = [f"# {m['title']}", "", " · ".join(head), "", "## Summary", "", sm["tldr"], "", "## Key takeaways", ""]
    for k in sm["takeaways"]:
        tag = ref_md(st, {"m": k.get("m") or first["id"], "t": k["t"]})
        lines.append(f"- {k['text']}" + (f" ({tag})" if tag and many else f" {tag}" if tag else ""))
    lines += ["", "## Notes", ""]
    for s in sm["sections"]:
        tags = [t for t in (ref_md(st, r) for r in s["refs"]) if t]
        lines += [f"### {s['title']}", ""] + ([", ".join(tags), ""] if tags else []) + [s["notes"], ""]
        if images:
            for f in _kept(s):
                lines += [f"![Frame at {mmss(f['t'])}]({frame_dir}/{f['file']})", ""]
    if many:
        lines += ["## Sources", ""] + [f"{i}. {r}" for i, r in enumerate(sources_list(st), 1)] + [""]
    return "\n".join(lines).rstrip() + "\n"


def doc_title(st: dict) -> str:
    return (st["meta"]["title"] if st.get("meta") else "Video summary")[:150]


def _safe_name(title: str, ext: str = "") -> str:
    return (re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "", title or "Video summary").strip(" .") or "Video summary")[:80] + ext


def _need_summary(st: dict):
    if not st.get("summary") or not st.get("meta"):
        raise ValueError("There is no summary to export yet.")


def _hyperlink(par, url: str, text: str):
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    h = OxmlElement("w:hyperlink")
    h.set(qn("r:id"), par.part.relate_to(url, RT.HYPERLINK, is_external=True))
    r, pr, c, u, t = (OxmlElement(x) for x in ("w:r", "w:rPr", "w:color", "w:u", "w:t"))
    c.set(qn("w:val"), "0563C1")
    u.set(qn("w:val"), "single")
    pr.append(c)
    pr.append(u)
    r.append(pr)
    t.text = text
    t.set(qn("xml:space"), "preserve")
    r.append(t)
    h.append(r)
    par._p.append(h)


def _inline(par, text: str):
    for i, bit in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if bit:
            par.add_run(bit).bold = bool(i % 2)


def _refs(par, st: dict, refs: list[dict]):
    for n, r in enumerate(refs):
        text, url = ref_parts(st, r)
        if not text or (not url and mat(st, r["m"])["kind"] == "video"):
            continue
        if n:
            par.add_run(", ")
        if url:
            _hyperlink(par, url, text)
        else:
            par.add_run(text)


def build_docx(st: dict) -> bytes:
    """Word file: title, source link(s) and channel, summary, takeaways with source tags, one heading per section with its frames, sources."""
    from docx import Document
    from docx.shared import Inches
    _need_summary(st)
    m, sm = st["meta"], st["summary"]
    many = len(st["materials"]) > 1
    first = ok_materials(st)[0]
    d = Document()
    d.core_properties.title = m["title"]
    d.add_heading(m["title"], 0)
    if not many and first["kind"] == "video":
        p = d.add_paragraph()
        p.add_run(("Channel: " + m["channel"] + " · ") if m.get("channel") else "")
        p.add_run("Source: ")
        _hyperlink(p, first["url"], first["url"])
        if m.get("duration"):
            d.add_paragraph("Length: " + mmss(m["duration"]))
    d.add_heading("Summary", 1)
    d.add_paragraph(sm["tldr"])
    d.add_heading("Key takeaways", 1)
    for k in sm["takeaways"]:
        p = d.add_paragraph(style="List Bullet")
        _inline(p, k["text"] + " ")
        _refs(p, st, [{"m": k.get("m") or first["id"], "t": k["t"]}])
    d.add_heading("Notes", 1)
    for s in sm["sections"]:
        d.add_heading(s["title"], 2)
        if any(ref_parts(st, r)[0] for r in s["refs"]):
            _refs(d.add_paragraph(), st, s["refs"])
        for line in s["notes"].split("\n"):
            line = line.rstrip()
            if not line.strip():
                continue
            b = re.match(r"^\s*[-*•]\s+(.*)", line)
            _inline(d.add_paragraph(style="List Bullet" if b else None), b.group(1) if b else line.strip())
        for f in _kept(s):
            path = _folder(st["id"]) / "frames" / f["file"]
            if path.exists():
                d.add_picture(io.BytesIO(path.read_bytes()), width=Inches(5.6))
                cap = d.add_paragraph(style="Caption")
                text, url = ref_parts(st, {"m": f.get("m") or s["m"], "t": f["t"]})
                if url:
                    _hyperlink(cap, url, "Frame: " + text)
                else:
                    cap.add_run("Frame at " + mmss(f["t"]))
    if many:
        d.add_heading("Sources", 1)
        for r in sources_list(st):
            d.add_paragraph(r, style="List Number")
    d.add_paragraph().add_run(NOTICE).italic = True
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def folder_files(st: dict) -> dict[str, bytes]:
    """{"note.md": ..., "frames/frame-MM-SS.jpg": ...}: the Markdown folder."""
    _need_summary(st)
    files = {"note.md": to_markdown(st, images=True).encode("utf-8")}
    for s in st["summary"]["sections"]:
        for f in _kept(s):
            p = _folder(st["id"]) / "frames" / f["file"]
            if p.exists():
                files["frames/" + f["file"]] = p.read_bytes()
    return files


def build_zip(st: dict) -> bytes:
    buf = io.BytesIO()
    root = _safe_name(doc_title(st))
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in folder_files(st).items():
            z.writestr(f"{root}/{name}", data)
    return buf.getvalue()


def export_file(vid: str, fmt: str) -> tuple[bytes, str, str]:
    """(bytes, file name, mime type) for a download."""
    st = load(vid)
    if fmt == "docx":
        return build_docx(st), _safe_name(doc_title(st), ".docx"), g.MIME_DOCX
    if fmt == "zip":
        return build_zip(st), _safe_name(doc_title(st), ".zip"), "application/zip"
    raise ValueError("Export as docx or zip.")


def save_to_tugas(vid: str, folder: str, fmt: str) -> dict:
    """Into a Tugas folder: the .docx, or a folder with note.md and frames/. A taken name becomes "name (2)"."""
    st = load(vid)
    if fmt == "docx":
        return {"path": tugas.save_bytes(folder, _safe_name(doc_title(st), ".docx"), build_docx(st))}
    if fmt != "md":
        raise ValueError("Save as docx or md.")
    files = folder_files(st)
    base = tugas.resolve(folder)
    if not base.is_dir():
        raise tugas.TugasError("That folder doesn't exist.")
    target = tugas.unique(base, tugas.clean_name(_safe_name(doc_title(st))))
    for name, data in files.items():
        p = target / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return {"path": tugas.rel(target.resolve())}


def export_google_doc(vid: str, account: str | None = None) -> dict:
    """The Word file goes to "A.F.R.A Decks" in Drive and Google converts it to a Google Doc (same path as the Create into PPT essay)."""
    if not g.accounts():
        raise ValueError(NO_GOOGLE)
    st = load(vid)
    data, name = build_docx(st), _safe_name(doc_title(st), ".docx")
    email, _ = g.access_token(account or None)
    folders = g.ensure_folders(email)
    f = g.upload(data, name.rsplit(".", 1)[0], g.MIME_DOCX, folders["decks"], g.MIME_DOC, email)
    return {"id": f["id"], "name": f.get("name") or name, "account": email, "url": f.get("webViewLink") or f"https://docs.google.com/document/d/{f['id']}/edit"}


def save_document(vid: str) -> dict:
    """The notes as an A.F.R.A document in the draft's project (updated, not duplicated, when saved again); it links back to the sources.
    This is also how a summary goes into Create into PPT: the wizard starts from an A.F.R.A document (text only; the frames stay here)."""
    st = load(vid)
    _need_summary(st)
    text = to_markdown(st) + "\n*Screenshots are in the Video summary page.*\n"
    meta = {"video": {"ids": [m["video_id"] for m in st["materials"] if m["kind"] == "video"], "urls": [m["url"] for m in st["materials"] if m["kind"] == "video"], "draft": vid}}
    d = db.get_document(st.get("doc_id") or "") if st.get("doc_id") else None
    if d:
        d = db.update_document(d["id"], doc_title(st), text, meta)
    else:
        d = db.create_document(st["project_id"], doc_title(st), "doc", text, meta)
        with _LOCK:
            cur = load(vid)
            cur["doc_id"] = d["id"]
            save(cur)
    return {"doc_id": d["id"], "title": d["title"]}


def finish(vid: str) -> dict:
    """The person is done: delete the downloaded videos now."""
    discard_video(vid)
    with _LOCK:
        st = load(vid)
        st["step"] = "done"
        save(st)
    return public(st)
