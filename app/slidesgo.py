"""Free Slidesgo templates, rebuilt from their public slide previews as an editable .pptx.

Slidesgo's real .pptx needs a sign-in. A.F.R.A never signs in, never uses a third-party "free downloader" and never goes around
Slidesgo's limits. What every visitor may see is the template page: a Free or Premium label and one preview picture per slide
(media.slidesgo.com). For a template marked Free those pictures are downloaded (public, no login) and the design is rebuilt:

  API mode         a GLM vision model reads each preview into a layout spec (template_import.read_slide), pptx_build writes the .pptx.
  Claude-app mode  a task goes to Claude, which rebuilds the deck with the PPT Master skill (REA for more detail from the page)
                   and hands it back with the save_design tool (mcp_server.py -> POST /api/designs/rebuilt/{task}).

A Premium template stops with a plain message. The sidecar of a rebuilt file says it is a rebuild, not the original, and that the
credits slide stays.
"""
import hashlib
import json
import re
import shutil
import time
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import designs, handoff, template_import as ti, tugas
from .config import DATA_DIR, get_settings

PREMIUM_MESSAGE = "This template is for paid members only, so A.F.R.A can't import it."
REBUILT_CREDIT = ("Rebuilt from Slidesgo (free template) — not the original file. "
                  "Template by Slidesgo / Freepik: keep the credits slide.")
MAX_SLIDES = 40
MAX_IMAGE = 6_000_000
MAX_TOTAL = 90_000_000
MAX_SECONDS = 150
API_WORKERS = 4
# .../storage/<id>/conversions/<n>-<slug>-thumb.jpg | .../responsive-images/<n>-<slug>___media_library_original_<w>_<h>.jpg | conversions/<n>-<slug>-original.jpg
IMG_RE = re.compile(r"https?://media\.slidesgo\.com/storage/(\d+)/(?:conversions|responsive-images)/(\d+)-([\w%.-]+?)"
                    r"(-thumb|-original|___media_library_original_(\d+)_(\d+))\.(jpe?g|png|webp)(?![\w/])", re.I)


def previews_dir() -> Path:
    return DATA_DIR / "slidesgo_previews"  # also holds GreatPPT's previews: one throw-away folder per rebuild


# ---------------------------------------------------------------- finding the previews
def theme_slug(url: str) -> str:
    m = re.match(r"/theme/([^/?#]+)", urlparse(url).path)
    return m.group(1).lower() if m else ""


def preview_urls(html: str, page_url: str = "", extra: list[str] | None = None) -> list[str]:
    """The slide previews of a theme page, in slide order, the largest variant of each. Pure.

    A slide is one storage id; its pictures are a small thumbnail and responsive copies named ..._original_<width>_<height>
    (the biggest is 1600x900). Pictures of other themes the page shows (related templates) have another slug and are left out."""
    text = unescape((html or "")[:6_000_000]).replace("\\/", "/")
    found = IMG_RE.findall(text) + [m for u in (extra or []) for m in IMG_RE.findall(u)]
    slides: dict[tuple[str, int, str], tuple[int, str]] = {}
    for sid, n, slug, kind, w, _h, ext in found:
        size = 10**6 if kind.lower() == "-original" else int(w) if w else 0  # full size > responsive width > thumbnail
        base = f"https://media.slidesgo.com/storage/{sid}/"
        if kind.lower() == "-thumb":
            url = f"{base}conversions/{n}-{slug}-thumb.{ext}"
        elif kind.lower() == "-original":
            url = f"{base}conversions/{n}-{slug}-original.{ext}"
        else:
            url = f"{base}responsive-images/{n}-{slug}{kind}.{ext}"
        key = (sid, int(n), slug.lower())
        if key not in slides or size > slides[key][0]:
            slides[key] = (size, url)
    if not slides:
        return []
    slugs = [k[2] for k in slides]
    want = theme_slug(page_url)
    slug = want if want in slugs else max(set(slugs), key=slugs.count)
    mine = sorted((k, v) for k, v in slides.items() if k[2] == slug)
    ordered = {}
    for (sid, n, _), (_, url) in sorted(mine, key=lambda kv: kv[0][1]):
        ordered.setdefault(n, url)
    return list(ordered.values())[:MAX_SLIDES]


def rea_image_urls(page_url: str, step=lambda s: None, site: dict | None = None) -> list[str]:
    """Image addresses the page loads in a private headless browser, read with REA. [] when REA or a browser is missing or fails
    (the page's own HTML lists every preview as well, so this only adds to it)."""
    cmd, browsers = ti.rea_command(), ti.find_browsers()
    if not cmd or not browsers:
        step("REA or a browser isn't installed: reading the page directly…")
        return []
    try:
        if not ti.rea_ready(cmd):
            step("REA doesn't pass `rea doctor`: reading the page directly…")
            return []
        for exe in browsers:
            try:
                b = ti.Browser(exe, page_url).start()
                break
            except ti.Stop:
                continue
        else:
            return []
        with b:
            step("REA is looking at the page…")
            time.sleep(5)
            origins = [*((site or {}).get("origins") or ["https://slidesgo.com", "https://media.slidesgo.com"]),
                       f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"]
            host = (site or {}).get("image_host") or "media.slidesgo.com"
            flags = [x for o in dict.fromkeys(origins) for x in ("--allowed-origins", o)]
            targets = ti.rea_run(cmd, ["list-browser-targets", b.endpoint, *flags])["normalized_result"]["targets"]
            page = next((t for t in targets if t.get("type") == "page" and t.get("url", "").startswith("http")), None)
            if not page:
                return []
            insp = ti.rea_run(cmd, ["inspect-web-page", b.endpoint, page["target_id"], *flags, "--observation-ms", "1500"], 180)["normalized_result"]
            return [r["url"] for r in insp.get("resources", []) if r.get("type") == "Image" and host in r.get("url", "")]
    except (ti.Stop, KeyError, TypeError, ValueError):
        return []


# ---------------------------------------------------------------- downloading them (public pictures only)
def safe_image_url(u: str) -> bool:
    """Only Slidesgo's public picture host, never a login, sign-in or download address."""
    p = urlparse(u)
    return p.scheme in ("http", "https") and (p.hostname or "") == "media.slidesgo.com" and not ti.LOGIN_RE.search(p.netloc + p.path) \
        and "/download" not in p.path.lower() and ti.public_host(u)


def image_kind(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return ""


def download_previews(urls: list[str], dest: Path, referer: str, step=lambda s: None, safe=None) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    out, total, t0 = [], 0, time.time()
    with ti._client() as h:
        for i, u in enumerate(urls, 1):
            if time.time() - t0 > MAX_SECONDS or total > MAX_TOTAL:
                break
            if not (safe or safe_image_url)(u):
                continue
            step(f"Collecting previews… {i} of {len(urls)}")
            try:
                r = h.get(u, headers={"Referer": referer})
            except httpx.HTTPError:
                continue
            kind = image_kind(r.content[:16]) if r.status_code == 200 else ""
            if not kind or len(r.content) > MAX_IMAGE:
                continue
            total += len(r.content)
            f = dest / f"{len(out) + 1:02d}.{kind}"
            f.write_bytes(r.content)
            out.append(f)
    return out


def clean_old_previews(keep_hours: int = 48):
    root = previews_dir()
    if root.is_dir():
        for d in root.iterdir():
            try:
                if d.is_dir() and time.time() - d.stat().st_mtime > keep_hours * 3600:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass


# ---------------------------------------------------------------- the two rebuild engines
def claude_mode() -> bool:
    return (get_settings().get("ai_mode") or "claude") == "claude"


def design_name(info: dict, site: dict | None = None) -> str:
    return (info.get("title") or (site or SLIDESGO)["name"] + " template").strip() or "Template"


# One description per site the rebuild works for (GreatPPT adds its own in greatppt.py). A site gives: how to find the preview pictures,
# which picture addresses may be downloaded, the credit, and the words the Claude task uses.
SITES: dict[str, dict] = {}
SLIDESGO = SITES["slidesgo"] = {
    "key": "slidesgo", "name": "Slidesgo", "rebuilt_credit": REBUILT_CREDIT, "premium_message": PREMIUM_MESSAGE,
    "origins": ["https://slidesgo.com", "https://media.slidesgo.com"], "image_host": "media.slidesgo.com",
    "slug": theme_slug, "preview_urls": preview_urls, "safe": safe_image_url,
    "not_free": "That Slidesgo page doesn't say the template is free, so A.F.R.A didn't try to rebuild it.",
    "no_previews": "A.F.R.A couldn't find the preview slides on that Slidesgo page.",
    "download_failed": "The preview pictures couldn't be downloaded from Slidesgo.",
    "keep": "Keep the Slidesgo credits slide (the last slide) as it is in the preview.",
    "forbidden": "Do NOT sign in to Slidesgo, do NOT use any third-party \"free downloader\" site, and do not get around Slidesgo's limits",
    "note": "Rebuilt by Claude with PPT Master from Slidesgo's public preview pictures; similar to the original, not an exact copy.",
    "remind": "remind them to keep the credits slide",
}


def rebuild(page_url: str, html: str, info: dict, step=lambda s: None) -> dict:
    """A Free Slidesgo page -> the rebuild (API mode: a saved design; Claude mode: a task for Claude). Raises template_import.Stop."""
    return rebuild_site(SLIDESGO, page_url, html, info, step)


def rebuild_site(site: dict, page_url: str, html: str, info: dict, step=lambda s: None) -> dict:
    """The same rebuild for any site in SITES, from the template page's public preview pictures only."""
    if info.get("premium"):
        raise ti.Stop("premium", site["premium_message"])
    if not info.get("free"):
        raise ti.Stop("unclear", site["not_free"])
    claude = claude_mode()
    conn = None if claude else ti.vision_conn()  # a missing key stops before anything is downloaded (Claude mode needs none)
    step("Collecting previews…")
    clean_old_previews()
    urls = site["preview_urls"](html, page_url, rea_image_urls(page_url, step, site))
    if not urls:
        raise ti.Stop("no_file", site["no_previews"])
    slug = site["slug"](page_url) or "template"
    folder = previews_dir() / (re.sub(r"[^\w-]", "", slug)[:50] + "-" + hashlib.sha1(f"{time.time()}".encode()).hexdigest()[:6])
    files = download_previews(urls, folder, page_url, step, site["safe"])
    if not files:
        shutil.rmtree(folder, ignore_errors=True)
        raise ti.Stop("error", site["download_failed"])
    page = designs.parse_page(html, page_url)
    if claude:
        return claude_task(page_url, info, files, page, site)
    try:
        step(f"Rebuilding (GLM)… {len(files)} slides")
        shots = [(f.read_bytes(), {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[f.suffix[1:]]) for f in files]
        credit = site["rebuilt_credit"]
        return ti.rebuild_from_images(shots, page_url, info, conn, lambda s: step("Rebuilding (GLM)… " + s.replace(" with " + conn[2], "")),
                                      credit, {"attribution": credit, "engine": "glm", "model": conn[2]}, API_WORKERS)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def task_system(site: dict) -> str:
    n = site["name"]
    return (f"You rebuild a FREE {n} template as an editable PowerPoint (.pptx) for the user's A.F.R.A Designs folder.\n"
            f"{n}'s own file is not used here. {site['forbidden']}: work only from the public slide previews listed below. "
            "Use the ppt-master skill (ppt-master) to build the deck.")


def task_prompt(page_url: str, name: str, files: list[Path], page: dict, target: Path, site: dict | None = None) -> str:
    site = site or SLIDESGO
    n = site["name"]
    previews = "\n".join(f"{i}. {f}" for i, f in enumerate(files, 1))
    palette = ", ".join(page.get("colors") or []) or "(read them from the previews)"
    fonts = ", ".join(page.get("fonts") or []) or "(read them from the previews)"
    return f"""Rebuild the free {n} template "{name}" as an editable .pptx.

Template page: {page_url}
Preview pictures (public, already downloaded, one per slide, in slide order; open them to see each slide):
{previews}

What to do
- Use the ppt-master skill. Make ONE slide per preview, same order, 16:9, matching each preview's layout, colours, fonts and decorative shapes.
- Text must be REAL editable text boxes (keep the preview's wording, e.g. "Title here" / the sample text); shapes must be real shapes
  (rectangles, ellipses, lines, freeforms), not pictures of the preview. Photos and icons may be simple placeholder shapes.
- {site['keep']}
- If you need more detail from the page itself (colours, fonts, shape positions), you may use REA (inspect-web-page / capture-web-screenshot) on {page_url}.
  Page palette seen so far: {palette}. Page fonts seen so far: {fonts}. Prefer Google Fonts the user can install.
- Do not sign in anywhere and do not download {n}'s own file.

When the .pptx is finished, save it at {target} (or anywhere you can write) and call the A.F.R.A tool
save_design with task_id and pptx_path. A.F.R.A checks that it is a real .pptx, puts it in the Designs folder with its {n} credit and the
"Rebuilt" badge, and finishes this task. Do not call finish_task for this task. In the chat just say it is done."""


def claude_task(page_url: str, info: dict, files: list[Path], page: dict, site: dict | None = None) -> dict:
    site = site or SLIDESGO
    name = design_name(info, site)
    target = designs.folder() / f"{tugas.clean_name(name)} (rebuilt).pptx"
    tid = handoff.create(f"Rebuild {site['name']} template: {name}", task_system(site), task_prompt(page_url, name, files, page, target, site),
                         kind="design", prefix="sg" if site["key"] == "slidesgo" else site["key"][:2],
                         design={"name": name, "url": page_url, "target": str(target), "slides": len(files), "site": site["key"],
                                 "title": info.get("title") or "", "previews": str(files[0].parent)})
    msg = f'A.F.R.A task {tid}: Rebuild {site["name"]} template "{name}". Please do it (get_task, then save_design).'
    return {"status": "claude", "task": tid, "claude_msg": msg, "name": name,
            "message": f"Sent to the Claude app ({len(files)} preview slides). Open Claude and press Enter: it rebuilds the deck with PPT Master."}


def claude_item_state(it: dict):
    """Bring a job item that waits for Claude up to date from its task."""
    t = handoff.TASKS.get(it.get("task"))
    if not t:
        it.update(state="done", step="", status="error", message="A.F.R.A was restarted before Claude finished. Add the link again.")
    elif t["status"] == "done" and t.get("saved"):
        site = SITES.get((t.get("design") or {}).get("site") or "slidesgo", SLIDESGO)
        it.update(state="done", step="", status="rebuilt", name=t["saved"]["name"],
                  message="Saved to the Designs folder. " + site["rebuilt_credit"])
    elif t["status"] in ("cancelled", "expired"):
        it.update(state="done", step="", status="error", message="Cancelled." if t["status"] == "cancelled" else "Claude didn't hand the design back in time.")
    elif t["status"] == "claimed":
        it.update(step="Claude is rebuilding it…")
    elif time.time() - t["created"] > handoff.WAIT:
        t["status"] = "expired"


# ---------------------------------------------------------------- Claude hands the .pptx back
def finish_rebuild(tid: str, data: bytes, filename: str = "") -> dict:
    """save_design: check the file is a real .pptx, keep it in the Designs folder with its sidecar and finish the task. ValueError says why not."""
    t = handoff.TASKS.get(tid)
    if not t or t.get("kind") != "design":
        raise ValueError(f"There is no template rebuild task {tid}. Ask the user to add the link in A.F.R.A again.")
    if t["status"] in ("done", "cancelled", "expired"):
        raise ValueError(f"Task {tid} is already {t['status']}.")
    if not ti.is_zip_magic(data):
        raise ValueError("That file isn't a .pptx (a PowerPoint file is a zip starting with PK). Save the deck as .pptx and send it again.")
    if not ti.is_pptx(data):
        raise ValueError("That zip isn't a PowerPoint presentation (ppt/presentation.xml is missing). Save the deck as .pptx and send it again.")
    try:
        slides = designs.pptx_theme(data)["slides"]
    except Exception:
        raise ValueError("A.F.R.A can't read that .pptx. Save the deck again with PPT Master and send it again.")
    if not slides:
        raise ValueError("That .pptx has no slides.")
    d = t["design"]
    site = SITES.get(d.get("site") or "slidesgo", SLIDESGO)
    target = Path(d["target"])
    try:
        same = target.is_file() and target.read_bytes() == data  # Claude already wrote it where the task said: keep that file
    except OSError:
        same = False
    info = {"title": d["name"], "credit_mentioned": True}
    if same:
        path = target
        side = {"source_url": d["url"], "site": site["name"], "attribution": site["rebuilt_credit"], "method": "rebuilt", "engine": "claude",
                "imported": time.strftime("%Y-%m-%d %H:%M"), "slides": slides, "note": site["note"]}
        path.with_name(path.name + ".source.json").write_text(json.dumps(side, ensure_ascii=False, indent=1), encoding="utf-8")
        res = {"name": path.stem, "path": str(path)}
    else:
        res = ti.save_design(data, d["name"] + " (rebuilt)", d["url"], site["key"], info, "rebuilt",
                             {"attribution": site["rebuilt_credit"], "engine": "claude", "slides": slides, "note": site["note"]})
    t["saved"] = {"name": res["name"], "path": res["path"]}
    handoff.finish(tid, res["path"])
    prev = Path(d.get("previews") or "")
    if prev.parent == previews_dir():  # the downloaded previews are not needed any more
        shutil.rmtree(prev, ignore_errors=True)
    return {"ok": True, "name": res["name"], "path": res["path"], "slides": slides, "site": site["name"], "remind": site["remind"]}
