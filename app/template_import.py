"""Templates from links, saved into the Designs folder. Free templates only.

Path 1, direct download: read the template page like a browser, find the site's own free .pptx download and keep an
exact copy (Slidesgo, SlidesCarnival, Microsoft templates, a public Google Slides deck, or a plain .pptx link). A link
only counts when it really serves a .pptx (zip magic "PK" and a PowerPoint inside). A page that marks the template as
premium or paid stops the import, and a download that sends the browser to a sign-in page is never followed: A.F.R.A
never logs in and never gets around a paywall. A sidecar "<file>.source.json" records the link and the credit the free licence asks for.

Path 2, rebuild from the preview: how a free template with no freely downloadable .pptx is handled — the page offers
no download at all, or the download is the site's own sign-in gate (that gate is never opened). Only the pictures the
page shows publicly are used: a headless
Edge or Chrome opens the page on a loopback debugging port, REA (rea-agents, installed with npm) lists the page and
inspects it for the preview pictures, a GLM vision model (setting template_vision_model, through OpenRouter) turns each
preview into a layout spec, and pptx_build writes an editable .pptx with real text boxes. If REA, a browser or the model
key is missing, the answer says what is missing; Path 1 never needs them.

import_link() does one link; start() runs several in the background with a status per link (job()).
"""
import base64
import hashlib
import json
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import zipfile
from collections import Counter
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx

from . import db, designs, models, pptx_build
from .config import DATA_DIR, get_settings

TRANSPORT = None  # tests put an httpx.MockTransport here
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
MAX_PPTX = 120_000_000
JOBS: dict[str, dict] = {}
DEFAULT_VISION_MODEL = models.VISION_FALLBACK
LOGIN_RE = re.compile(r"(accounts\.google\.com|login\.microsoftonline\.com|login\.live\.com|id\.magnific\.com|id\.freepik\.com)|"
                      r"/(log-?in|sign-?in|signin|signup|sign-up|register|auth|sso)(/|$)", re.I)
CREDIT = {
    "slidesgo": "Template by Slidesgo. The free licence asks you to keep the credits slide (or credit Slidesgo, Flaticon and Freepik).",
    "slidescarnival": "Template by SlidesCarnival. The free licence asks you to keep the credits slide that comes with it.",
    "microsoft": "Template from Microsoft 365 templates.",
    "greatppt": "Template by GreatPPT (free licence).",
    "gslides": "", "file": "", "canva": "", "other": "",
}
SITE_NAME = {"slidesgo": "Slidesgo", "slidescarnival": "SlidesCarnival", "microsoft": "Microsoft", "greatppt": "GreatPPT", "gslides": "Google Slides", "canva": "Canva"}
PREMIUM_RE = re.compile(
    r"(premium|paid|pro|plus)[\s-]+(only|members?\b|users?\b|subscribers?\b)|for (premium|pro|paid) (members|users|subscribers)|"
    r"(available|exclusive|reserved)\s+(only\s+)?(to|for)\s+(premium|pro|paid|plus)\b|"
    r"requires? (a |an )?(premium|paid|pro|plus)\b|upgrade to (premium|pro) to download|premium template\b|this is a premium", re.I)


class Stop(Exception):
    """An import that ends with a plain message: status is saved, premium, signin, no_file, unclear, missing or error."""

    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status, self.message = status, message


# ---------------------------------------------------------------- reading a page
def classify(url: str) -> str:
    u = urlparse(url.strip())
    host, path = (u.hostname or "").lower(), u.path.lower()
    if host == "docs.google.com" and "/presentation/d/" in path:
        return "gslides"
    if path.endswith(".pptx"):
        return "file"
    if host.endswith("slidesgo.com"):
        return "slidesgo"
    if host.endswith("slidescarnival.com"):
        return "slidescarnival"
    if host == "greatppt.com" or host.endswith(".greatppt.com"):
        return "greatppt"
    if host.endswith(("microsoft.com", "office.com", "cloud.microsoft", "office.net")):
        return "microsoft"
    if host.endswith(("canva.com", "canva.cn")):
        return "canva"
    return "other"


def is_zip_magic(head: bytes) -> bool:
    return head[:4] == b"PK\x03\x04"


def is_pptx(data: bytes) -> bool:
    """A zip that holds a PowerPoint presentation (not just any zip, and not an HTML page)."""
    if not is_zip_magic(data):
        return False
    try:
        import io
        return "ppt/presentation.xml" in zipfile.ZipFile(io.BytesIO(data)).namelist()
    except zipfile.BadZipFile:
        return False


def public_host(url: str) -> bool:
    """Page-supplied links must go to the public web, not to this computer or the local network."""
    u = urlparse(url)
    h = (u.hostname or "").lower()
    if u.scheme not in ("http", "https") or not h or h == "localhost" or h.endswith((".local", ".internal", ".localhost")):
        return False
    return not re.fullmatch(r"(127\.|10\.|192\.168\.|169\.254\.|172\.(1[6-9]|2\d|3[01])\.|0\.|\[?::1\]?|\[?f[cd][0-9a-f]{2}:).*", h)


def visible_text(html: str) -> str:
    html = re.sub(r"<(script|style|svg|noscript)\b.*?</\1\s*>", " ", html[:3_000_000], flags=re.S | re.I)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def candidates(html: str, base: str) -> list[str]:
    r"""Links on the page that may serve the template's .pptx, best guesses first.

    Besides the ordinary link attributes this reads the site's own page data (the JSON in its scripts): a page that
    names its free file there, Slidesgo only naming the download address — gets its direct .pptx link too, whether it
    sits in escaped form ("https:\/\/cdn…\/x.pptx") or in capital letters (.PPTX).
    """
    text = html[:3_000_000].replace("\\u002f", "/").replace("\\u002F", "/").replace("\\/", "/")
    raw = re.findall(r"""(?:href|data-href|data-url|data-download|data-file)=["']([^"']+)""", text, re.I)
    raw += re.findall(r'"(?:(?:download[\w-]*|file)[\w-]*|pptx)"\s*:\s*"([^"]+\.pptx\b[^"]*)"', text, re.I)
    raw += re.findall(r"https?://[^\"'\s<>\\]+?\.pptx\b[^\"'\s<>]*", text, re.I)
    exact, routes, downloads, seen = [], [], [], set()
    for r in raw:
        u = urljoin(base, unescape(r).strip())
        q = parse_qs(urlparse(u).query)
        for key in ("src", "url", "file"):  # viewer links such as view.officeapps.live.com/op/view.aspx?src=<the .pptx>
            inner = unquote((q.get(key) or [""])[0])
            if inner.lower().split("?")[0].endswith(".pptx"):
                u = inner
        if u in seen or not public_host(u):
            continue
        seen.add(u)
        path = urlparse(u).path.lower().rstrip("/")
        if path.endswith(".pptx"):
            exact.append(u)
        elif re.search(r"/download/[^/]+/[^/]+/pptx$", path):
            routes.append(u)
        elif re.search(r"^/theme/[^/]+/download$", path):
            downloads.append(u)
    return exact + routes + downloads


def pick_candidates(cands: list[str], page_url: str) -> list[str]:
    """The links that belong to this template. A page that lists many templates gives none (paste one template's link)."""
    slugs = [x for x in urlparse(page_url).path.lower().split("/") if len(x) > 3 and not x.isdigit() and x not in ("template", "templates", "theme")]
    mine = [c for c in cands if any(x in unquote(c).lower() for x in slugs)]
    if mine:
        return mine
    return cands if len(cands) <= 3 else []


def clean_title(t: str) -> str:
    """"Check Out This Minimal Thesis Defense Presentation Template" -> "Minimal Thesis Defense"."""
    t = re.sub(r"^(check out this|free|download)\s+", "", t.strip(), flags=re.I)
    t = re.split(r"\s*[.|–—]\s+(?:free|download)\b|\s+[|–—]\s+", t, maxsplit=1)[0]
    t = re.sub(r"(\s+(free|ppt|powerpoint|google slides|&|and))*(\s+presentation)?(\s+(template|theme))?s?\s*$", "", t, flags=re.I)
    return t.strip(" .-")[:100] or "Template"


def page_info(html: str, url: str) -> dict:
    """What a template page says about itself: title, free or premium, its .pptx links, the credit it asks for. Pure."""
    kind = classify(url)
    p = designs.parse_page(html, url)
    text = visible_text(html)
    head = " ".join(re.findall(r"<(?:title|h1)[^>]*>(.*?)</(?:title|h1)>", html[:400_000], re.S | re.I))
    desc = " ".join(re.findall(r'<meta[^>]+(?:name|property)=["\'](?:description|og:description|og:title)["\'][^>]+content=["\']([^"\']+)', html[:400_000], re.I))
    premium, free, why = False, None, ""
    ctype = re.search(r'"content_type"\s*:\s*"(free|premium)"', html)  # Slidesgo says it in the page's data
    if ctype:
        premium, free = ctype.group(1) == "premium", ctype.group(1) == "free"
        why = "marked premium by the site" if premium else ""
    elif PREMIUM_RE.search(re.sub(r"\s+", " ", unescape(head + " " + desc))) or (kind != "slidesgo" and PREMIUM_RE.search(text)):
        premium, why = True, "the page says it is for paid members"
    if free is None and not premium:
        free = bool(re.search(r"\bfree\b", unescape(head + " " + desc), re.I))
    cands = candidates(html, url)
    links = pick_candidates(cands, url)
    if kind == "greatppt":  # its Download button is a form that needs an account: never a direct .pptx link on the page
        from . import greatppt
        f = greatppt.page_facts(html)
        premium, free, why, links, cands = f["price"] is not None and f["price"] > 0, f["free"], "the page has a price" if (f["price"] or 0) > 0 else "", [], []
    m = re.fullmatch(r"(/theme/[^/]+)/?", urlparse(url).path)
    if kind == "slidesgo" and m and not links:  # Slidesgo only names its download address inside scripts
        links = [f"{urlparse(url).scheme}://{urlparse(url).netloc}{m.group(1)}/download"]
    return {"kind": kind, "title": clean_title(p["title"]), "premium": premium, "why": why, "free": bool(free) and not premium,
            "links": links, "listing": len(cands) > 3 and not links,
            "credit_mentioned": bool(re.search(r"attribution|credits? slide|keep this slide", text, re.I)), "images": p["images"]}


# ---------------------------------------------------------------- Path 1: the real file
def _client() -> httpx.Client:
    return httpx.Client(timeout=40, follow_redirects=True, transport=TRANSPORT, headers={"User-Agent": UA, "Accept-Language": "en"})


def _filename(r: httpx.Response) -> str:
    cd = r.headers.get("content-disposition", "")
    m = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", cd, re.I) or re.search(r'filename="?([^";]+)', cd, re.I)
    name = unquote(m.group(1).strip().strip('"')) if m else unquote(urlparse(str(r.url)).path.rsplit("/", 1)[-1])
    return re.sub(r"\.pptx$", "", name, flags=re.I)


def fetch_file(h: httpx.Client, url: str, referer: str = "") -> tuple[bytes, str] | None:
    """(bytes, name) when the link serves a .pptx; None when it is a page or something else. Sign-in redirects stop the import."""
    with h.stream("GET", url, headers={"Referer": referer} if referer else {}) as r:
        if LOGIN_RE.search(urlparse(str(r.url)).netloc + urlparse(str(r.url)).path) or r.status_code == 401:
            raise Stop("signin", "The site sent the download to a sign-in page, and A.F.R.A never signs in. Download the file in your "
                                 "browser and drop it into the Designs folder: it shows up here by itself.")
        if r.status_code >= 400 or "html" in r.headers.get("content-type", ""):
            return None
        buf = bytearray()
        for chunk in r.iter_bytes():
            buf += chunk
            if len(buf) >= 4 and not is_zip_magic(bytes(buf[:4])):
                return None
            if len(buf) > MAX_PPTX:
                raise Stop("error", "That file is over 120 MB, so A.F.R.A didn't download it.")
        data = bytes(buf)
        return (data, _filename(r)) if is_pptx(data) else None


def _credit(kind: str, info: dict) -> str:
    text = CREDIT.get(kind, "")
    return text or ("Keep the credits slide." if info.get("credit_mentioned") else "")


def save_design(data: bytes, name: str, url: str, kind: str, info: dict, method: str = "download", extra: dict | None = None) -> dict:
    try:
        designs.pptx_theme(data)
    except ValueError:
        raise Stop("error", "The file isn't a usable PowerPoint, so A.F.R.A didn't keep it.")
    path = designs.save_to_folder(data, (name or "Template") + ".pptx")
    side = {"source_url": url, "site": SITE_NAME.get(kind, urlparse(url).hostname or ""), "attribution": _credit(kind, info),
            "method": method, "imported": time.strftime("%Y-%m-%d %H:%M"), **(extra or {})}
    path.with_name(path.name + ".source.json").write_text(json.dumps(side, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"status": "saved" if method == "download" else "rebuilt", "name": path.stem, "path": str(path), "attribution": side["attribution"]}


NEED_SIGNIN = ("{site} asks you to sign in before it lets you download, and A.F.R.A never signs in for you. "
               "Download the .pptx in your browser and drop it into the Designs folder: it shows up here by itself.")


def import_google(url: str, h: httpx.Client) -> dict:
    m = re.search(r"/presentation/d/([\w-]+)", url)
    fid = m.group(1)
    try:
        got = fetch_file(h, f"https://docs.google.com/presentation/d/{fid}/export/pptx")
    except Stop:
        got = None
    if got:
        return save_design(got[0], got[1] or "Google Slides deck", url, "gslides", {})
    from . import google_api as g
    for acc in g.accounts():  # a deck that is not public: the connected account may be able to export it
        try:
            data = g.export(fid, g.MIME_PPTX, acc["email"])
            if is_pptx(data):
                return save_design(data, g.file_meta(fid, acc["email"]).get("name") or "Google Slides deck", url, "gslides", {})
        except (g.GoogleError, httpx.HTTPError):
            continue
    raise Stop("signin", "This Google Slides deck isn't shared publicly. Set it to \"Anyone with the link can view\", or connect your "
                         "Google account under Settings › Google so A.F.R.A can export it.")


def import_link(url: str, step=lambda s: None) -> dict:
    """One link -> {"status", "message", "name", "path", "attribution"}. Never raises for an ordinary failure."""
    url = (url or "").strip()
    try:
        if urlparse(url).scheme not in ("http", "https") or not public_host(url):
            raise Stop("error", "Paste the web address of a template page (https://…).")
        kind = classify(url)
        if kind == "greatppt":
            from . import greatppt
            if not greatppt.is_template_url(url):
                raise Stop("no_file", "Paste the link of one GreatPPT template page (greatppt.com/downloads/<template>/).")
        with _client() as h:
            if kind == "gslides":
                step("Exporting the Google Slides deck…")
                return {"url": url, **import_google(url, h)}
            if kind == "file":
                step("Downloading the file…")
                got = fetch_file(h, url)
                if not got:
                    raise Stop("error", "That link doesn't serve a PowerPoint file.")
                return {"url": url, **save_design(got[0], got[1], url, kind, {})}
            step("Reading the page…")
            try:
                r = h.get(url)
            except httpx.HTTPError as e:
                raise Stop("error", f"Couldn't open that page ({e.__class__.__name__}).")
            if LOGIN_RE.search(urlparse(str(r.url)).netloc + urlparse(str(r.url)).path) or r.status_code in (401, 403):
                raise Stop("signin", "That page needs a login, so A.F.R.A doesn't read it. Open it in your browser instead.")
            if r.status_code >= 400:
                raise Stop("error", f"The page answered {r.status_code}.")
            if is_pptx(r.content):
                return {"url": url, **save_design(r.content, _filename(r), url, "file", {})}
            info = page_info(r.text, str(r.url))
            if info["premium"]:
                raise Stop("premium", "This template is for paid members only, so A.F.R.A can't import it.")
            if info["kind"] == "slidesgo":  # Slidesgo's download needs a sign-in: free templates are rebuilt from the public previews instead
                from . import slidesgo
                return {"url": url, **slidesgo.rebuild(str(r.url), r.text, info, step)}
            if info["kind"] == "greatppt":  # connected: the exact file through the user's own account; else rebuilt from the public previews
                from . import greatppt
                return {"url": url, **greatppt.import_page(str(r.url), r.text, info, step)}
            blocked = False
            for link in info["links"][:4]:
                step("Downloading the template…")
                try:
                    got = fetch_file(h, link, str(r.url))
                except Stop as s:
                    if s.status != "signin":
                        raise
                    blocked = True
                    continue
                except httpx.HTTPError:
                    continue
                if got:
                    return {"url": url, **save_design(got[0], info["title"] or got[1], url, info["kind"], info)}
            if blocked:
                if not info["free"]:
                    raise Stop("signin", NEED_SIGNIN.format(site=SITE_NAME.get(info["kind"], "That site")))
                try:  # the page itself says free: its download is what sits behind the gate, so rebuild from what the page shows publicly
                    res = rebuild_from_preview(str(r.url), info, step)
                except Stop as s:
                    if s.status == "missing":
                        raise Stop("missing", s.message + " Or download the .pptx in your browser and drop it into the "
                                   "Designs folder: it shows up there by itself.")
                    raise
                return {"url": url, **res}
            if info["listing"]:
                raise Stop("no_file", "That page lists many templates. Paste the link of one template.")
            if not info["free"]:
                raise Stop("unclear", "That page offers no .pptx and doesn't say the template is free, so A.F.R.A didn't try to rebuild it.")
            res = rebuild_from_preview(str(r.url), info, step)
            return {"url": url, **res}
    except Stop as s:
        return {"url": url, "status": s.status, "message": s.message}
    except (OSError, httpx.HTTPError, zipfile.BadZipFile) as e:
        return {"url": url, "status": "error", "message": f"Something went wrong ({e.__class__.__name__}: {str(e)[:120]})."}


# ---------------------------------------------------------------- Path 2: REA + a browser + a vision model
def rea_command() -> list[str] | None:
    """How to run REA: `node …/rea.mjs` when the script is found, else the `rea` program itself; None when not installed."""
    node = shutil.which("node")
    scripts = []
    exe = shutil.which("rea")
    if exe:
        base = Path(exe).resolve().parent
        scripts += [base / "node_modules" / "rea-agents" / "scripts" / "rea.mjs", base.parent / "lib" / "node_modules" / "rea-agents" / "scripts" / "rea.mjs"]
    npm = shutil.which("npm")
    if npm and not scripts:
        try:
            root = subprocess.run([npm, "root", "-g"], capture_output=True, text=True, timeout=30).stdout.strip()
            if root:
                scripts.append(Path(root) / "rea-agents" / "scripts" / "rea.mjs")
        except (OSError, subprocess.SubprocessError):
            pass
    for s in scripts:
        if node and s.is_file():
            return [node, str(s)]
    return [exe] if exe else None


def rea_run(cmd: list[str], args: list[str], timeout: int = 120, allow_fail: bool = False) -> dict:
    """Run one REA command and return its JSON. allow_fail: `rea doctor` exits 1 when anything it checks is missing, but still answers."""
    try:
        out = subprocess.run([*cmd, *args, "--format", "json"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Stop("error", "REA took too long on that page, so A.F.R.A stopped it.")
    except (OSError, subprocess.SubprocessError) as e:
        raise Stop("error", f"REA didn't run ({e.__class__.__name__}).")
    try:
        data = json.loads(out.stdout)
    except ValueError:
        data = None
    if data is None or (out.returncode != 0 and not allow_fail):
        raise Stop("error", f"REA stopped: {(out.stderr or out.stdout).strip()[:200]}" if out.returncode else "REA answered with something A.F.R.A can't read.")
    return data


def rea_ready(cmd: list[str]) -> bool:
    """`rea doctor` runs and its node check passes (the other checks are about reverse-engineering tools we don't use)."""
    try:
        d = rea_run(cmd, ["doctor", "--filter-output", "scope_checks"], 90, allow_fail=True)
    except Stop:
        return False
    checks = d.get("scope_checks") if isinstance(d, dict) else d
    return any(c.get("name") == "node" and c.get("ok") for c in (checks or []) if isinstance(c, dict))


def find_browsers() -> list[str]:
    """Edge first (every Windows has it), then Chrome."""
    import os
    pf = [os.environ.get(k, "") for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
    paths = [str(Path(b) / "Microsoft/Edge/Application/msedge.exe") for b in pf if b] + [str(Path(b) / "Google/Chrome/Application/chrome.exe") for b in pf if b]
    paths += ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    paths += [w for n in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser") if (w := shutil.which(n))]
    return list(dict.fromkeys(p for p in paths if Path(p).is_file()))


def find_browser() -> str | None:
    return next(iter(find_browsers()), None)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Browser:
    """A headless Edge/Chrome with a loopback debugging port and its own throw-away profile. Stopped by its own PID."""

    def __init__(self, exe: str, url: str):
        self.exe, self.url, self.port = exe, url, free_port()
        self.profile = DATA_DIR / "browser_profiles" / hashlib.sha1(f"{time.time()}{self.port}".encode()).hexdigest()[:10]
        self.proc = None
        self.endpoint = f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        return self if self.proc else self.start()

    def start(self):
        self.profile.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen([self.exe, "--headless=new", f"--remote-debugging-port={self.port}", f"--user-data-dir={self.profile}",
                                      "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--window-size=1366,900", self.url],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                if httpx.get(self.endpoint + "/json/version", timeout=2).status_code == 200:
                    return self
            except httpx.HTTPError:
                pass
            if self.proc.poll() is not None:
                break
            time.sleep(0.5)
        self.__exit__()
        raise Stop("error", "The browser didn't start.")

    def __exit__(self, *a):
        if self.proc and self.proc.poll() is None:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True)  # our process tree only
            else:
                self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        for _ in range(5):  # Windows lets go of the profile a moment after the process ends
            shutil.rmtree(self.profile, ignore_errors=True)
            if not self.profile.exists():
                break
            time.sleep(0.5)


def _natural_key(url: str) -> list[tuple[int, str]]:
    """"slide-2" before "slide-10": a file name read as alternating text and number pieces."""
    name = unquote(urlparse(url).path).rsplit("/", 1)[-1]
    return [(int(t), "") if t.isdigit() else (-1, t) for t in re.split(r"(\d+)", name)]


def capture_previews(cmd: list[str], exe: str, url: str, hint_images: list[str], step=lambda s: None, limit: int = 12) -> list[tuple[bytes, str]]:
    """The preview slide pictures of a page: images the browser loaded (found by REA), else one screenshot."""
    origins = list(dict.fromkeys(f"{urlparse(u).scheme}://{urlparse(u).netloc}" for u in [url, *hint_images] if public_host(u)))[:6]
    flags = [x for o in origins for x in ("--allowed-origins", o)]
    for exe_ in [exe] if isinstance(exe, str) else exe:  # the next browser if one won't start
        try:
            b = Browser(exe_, url).start()
            break
        except Stop:
            continue
    else:
        raise Stop("error", "The browser didn't start.")
    with b:
        step("Opening the page in a private browser…")
        time.sleep(5)  # let the page draw its pictures
        targets = rea_run(cmd, ["list-browser-targets", b.endpoint, *flags])["normalized_result"]["targets"]
        page = next((t for t in targets if t.get("type") == "page" and t.get("url", "").startswith("http")), None)
        if not page:
            raise Stop("error", "REA couldn't find the page in the browser.")
        step("REA is inspecting the page…")
        insp = rea_run(cmd, ["inspect-web-page", b.endpoint, page["target_id"], *flags, "--observation-ms", "1500"], 180)["normalized_result"]
        urls = [r["url"] for r in insp.get("resources", []) if r.get("type") == "Image" and (r.get("content_size") or 99999) >= 15000
                and "image/" in (r.get("mime_type") or "image/")]
        shot = None
        try:
            art = rea_run(cmd, ["capture-web-screenshot", b.endpoint, page["target_id"], *flags], 120)["normalized_result"]["artifact"]
            shot = (base64.b64decode(art["data_base64"]), "image/png")
        except (Stop, KeyError, ValueError):
            pass
    out, seen = [], set()
    step("Collecting the preview pictures…")
    with _client() as h:
        for u in sorted(dict.fromkeys(urls + hint_images), key=_natural_key):  # previews land in slide order
            if len(out) >= limit or not public_host(u):
                continue
            try:
                r = h.get(u, headers={"Referer": url})
            except httpx.HTTPError:
                continue
            ct = r.headers.get("content-type", "").split(";")[0]
            if r.status_code == 200 and ct in ("image/png", "image/jpeg", "image/webp") and 15_000 <= len(r.content) <= 8_000_000:
                dig = hashlib.sha1(r.content).hexdigest()
                if dig not in seen:
                    seen.add(dig)
                    out.append((r.content, ct))
    return out or ([shot] if shot else [])


# the layout spec the vision model fills in, and its checks
SPEC_SYSTEM = """You turn the picture of ONE presentation slide into a layout spec so it can be rebuilt as an editable PowerPoint slide.
Answer with JSON only, in this shape:
{"background": "#RRGGBB", "palette": ["#RRGGBB", ...up to 6 main colours], "fonts": {"heading": "font name", "body": "font name"},
 "shapes": [{"type": "rect|ellipse|round_rect|triangle|line", "x": 0-100, "y": 0-100, "w": 0-100, "h": 0-100, "fill": "#RRGGBB or null", "line": "#RRGGBB or null"}],
 "texts": [{"role": "title|subtitle|body|caption|other", "text": "the words on the slide", "x": 0-100, "y": 0-100, "w": 0-100, "h": 0-100,
            "font": "font name", "size": points for a slide 720 pt wide, "color": "#RRGGBB", "bold": true|false, "align": "left|center|right", "anchor": "top|middle|bottom"}]}
x, y, w, h are percentages of the slide (x, y = top-left corner). Describe the slide's own layout: big coloured blocks, bars, shapes and every text box. Skip photos and icons.
Use common fonts (Arial, Calibri, Georgia, Montserrat...). Keep text short; use the real words you can read."""


def _hex(v, default=None):
    m = re.fullmatch(r"#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", str(v or "").strip())
    if not m:
        return default
    h = m.group(1)
    return "#" + (("".join(c * 2 for c in h) if len(h) == 3 else h).upper())


def _num(v, lo, hi, default):
    try:
        return min(hi, max(lo, float(v)))
    except (TypeError, ValueError):
        return default


def validate_spec(j) -> dict:
    """One slide's spec from the model -> a clean spec (numbers clamped, colours as #RRGGBB, junk dropped). ValueError if nothing usable."""
    if not isinstance(j, dict):
        raise ValueError("The vision model didn't return a layout.")
    shapes, texts = [], []
    for s in (j.get("shapes") or [])[:40]:
        if not isinstance(s, dict):
            continue
        x, y = _num(s.get("x"), 0, 100, None), _num(s.get("y"), 0, 100, None)
        w, h = _num(s.get("w"), 0, 100, None), _num(s.get("h"), 0, 100, None)
        if None in (x, y, w, h):
            continue
        t = s.get("type") if s.get("type") in ("rect", "ellipse", "round_rect", "triangle", "line") else "rect"
        shapes.append({"type": t, "x": x, "y": y, "w": min(w, 100 - x), "h": min(h, 100 - y), "fill": _hex(s.get("fill")), "line": _hex(s.get("line"))})
    for t in (j.get("texts") or [])[:40]:
        if not isinstance(t, dict) or not str(t.get("text") or "").strip():
            continue
        x, y = _num(t.get("x"), 0, 100, None), _num(t.get("y"), 0, 100, None)
        w, h = _num(t.get("w"), 1, 100, None), _num(t.get("h"), 1, 100, None)
        if None in (x, y, w, h):
            continue
        texts.append({"role": t.get("role") if t.get("role") in ("title", "subtitle", "body", "caption", "other") else "other",
                      "text": str(t["text"]).strip()[:400], "x": x, "y": y, "w": min(w, 100 - x) or 1, "h": min(h, 100 - y) or 1,
                      "font": re.sub(r"[^\w .-]", "", str(t.get("font") or ""))[:60].strip(), "size": _num(t.get("size"), 6, 96, 18),
                      "color": _hex(t.get("color"), "#000000"), "bold": bool(t.get("bold")),
                      "align": t.get("align") if t.get("align") in ("left", "center", "right") else "left",
                      "anchor": t.get("anchor") if t.get("anchor") in ("top", "middle", "bottom") else "top"})
    if not shapes and not texts:
        raise ValueError("The layout has no shapes or text.")
    fonts = j.get("fonts") if isinstance(j.get("fonts"), dict) else {}
    return {"background": _hex(j.get("background")), "palette": [c for c in (_hex(c) for c in (j.get("palette") or [])[:8]) if c],
            "fonts": {k: re.sub(r"[^\w .-]", "", str(fonts.get(k) or ""))[:60].strip() for k in ("heading", "body")}, "shapes": shapes, "texts": texts}


def build_deck_spec(slides: list[dict], name: str, aspect: tuple[int, int] | None = None) -> dict:
    """Validated slide specs -> the deck spec pptx_build.build_from_spec takes: size in points and one theme for the whole deck."""
    if not slides:
        raise ValueError("No slides to build.")
    w = 720
    h = round(w * aspect[1] / aspect[0]) if aspect and aspect[0] else 405
    h = min(max(h, 300), 720)
    pal = Counter(c for s in slides for c in s["palette"] + [s["background"]] if c)
    fonts = [Counter(s["fonts"][k] for s in slides if s["fonts"][k]).most_common(1) for k in ("heading", "body")]
    theme = {**designs.THEME, **designs.theme_from_list(name, [c for c, _ in pal.most_common(8)], [f[0][0] for f in fonts if f])}
    bgs = [s["background"] for s in slides if s["background"] and designs.lum(s["background"]) > .8]
    if bgs:
        theme["light"] = Counter(bgs).most_common(1)[0][0]
    return {"size": (w, h), "theme": theme, "slides": slides}


def vision_conn() -> tuple[str, str, str]:
    """(base_url, api_key, model) of the connection that reads previews; a message in Stop when there is none."""
    s = get_settings()
    model = (s.get("template_vision_model") or "").strip() or models.vision_default()
    conns = [models._conn(src, s) for src in ("1", "2")]
    usable = [(b, k) for p, b, k in conns if p != "anthropic" and b and k]
    pick = next((c for c in usable if "openrouter.ai" in c[0]), None) or (usable[0] if usable and not model.startswith("z-ai/") else None)
    if not pick:
        raise Stop("missing", "Rebuilding from a preview needs an OpenRouter key (Settings › AI model) to read the pictures with a GLM vision model.")
    return pick[0], pick[1], model


def read_slide(img: bytes, mime: str, n: int, total: int, conn: tuple[str, str, str]) -> dict:
    base, key, model = conn
    body = {"model": model, "temperature": 0.1, "max_tokens": 8000,
            "messages": [{"role": "system", "content": SPEC_SYSTEM},
                         {"role": "user", "content": [{"type": "text", "text": f"Slide {n} of {total}. Return the JSON layout spec only."},
                                                      {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(img).decode()}"}}]}]}
    if "openrouter.ai" in base:
        body["usage"] = {"include": True}
    from .llm import extract_json
    for attempt in (1, 2):
        try:
            with httpx.Client(timeout=180, transport=TRANSPORT) as h:
                r = h.post(base.rstrip("/") + "/chat/completions", json=body, headers={"authorization": f"Bearer {key}"})
        except httpx.HTTPError as e:
            raise Stop("error", f"The vision model couldn't be reached ({e.__class__.__name__}).")
        if r.status_code == 429 or r.status_code >= 500:  # busy for a moment: once more, then this slide falls back to its picture
            if attempt == 2:
                raise Stop("busy", f"The vision model was busy ({r.status_code}).")
            time.sleep(4)
            continue
        if r.status_code >= 400:
            raise Stop("error", f"The vision model answered {r.status_code}: {r.text[:160]}")
        j = r.json()
        u = j.get("usage") or {}
        try:
            db.log_usage("template-vision", model, u.get("prompt_tokens", 0), u.get("completion_tokens", 0), 0, u.get("cost"), not u)
        except Exception:
            pass
        try:
            return validate_spec(extract_json((j.get("choices") or [{}])[0].get("message", {}).get("content") or ""))
        except ValueError:
            if attempt == 2:
                raise
            body["messages"].append({"role": "user", "content": "That wasn't valid JSON (maybe cut off). Answer again with the "
                                     "complete JSON layout spec only: compact, no comments, no text around it."})
    raise ValueError("unreachable")


def rebuild_from_preview(url: str, info: dict, step=lambda s: None) -> dict:
    missing = []
    cmd = rea_command()
    if not cmd:
        missing.append("REA (install it with: npm install -g rea-agents)")
    exe = find_browsers()
    if not exe:
        missing.append("Microsoft Edge or Google Chrome")
    try:
        conn = vision_conn()
    except Stop:
        missing.append("an OpenRouter key for the GLM vision model (Settings › AI model)")
    if missing:
        raise Stop("missing", "This page has no .pptx to download, so it needs a rebuild from its preview, and these are missing: " + ", ".join(missing) + ".")
    step("Checking REA…")
    if not rea_ready(cmd):
        raise Stop("missing", "REA is installed but `rea doctor` doesn't pass (Node.js is needed). Run `rea doctor` in a terminal to see why.")
    shots = capture_previews(cmd, exe, url, info.get("images") or [], step)
    if not shots:
        raise Stop("no_file", "A.F.R.A couldn't find preview slides on that page.")
    return rebuild_from_images(shots, url, info, conn, step)


def rebuild_from_images(shots: list[tuple[bytes, str]], url: str, info: dict, conn: tuple[str, str, str], step=lambda s: None,
                        note: str = "", extra: dict | None = None, workers: int = 1) -> dict:
    """Preview pictures -> a GLM layout spec per slide -> an editable .pptx in the Designs folder."""
    def one(a):
        i, (img, mime) = a
        step(f"Reading slide {i} of {len(shots)} with {conn[2]}…")
        try:
            return read_slide(img, mime, i, len(shots), conn)
        except (ValueError, Stop) as e:
            if isinstance(e, Stop) and e.status != "busy":
                raise
            ext = {"image/jpeg": "jpeg", "image/png": "png"}.get(mime)  # keep the slide in its place: its preview as a picture
            return {"background": "", "palette": [], "fonts": {"heading": "", "body": ""}, "texts": [], "shapes": [],
                    "_image": (img, ext) if ext else None, "_fallback": True}
    if workers > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(workers) as ex:
            specs = list(ex.map(one, enumerate(shots, 1)))
    else:
        specs = list(map(one, enumerate(shots, 1)))
    if all(s.get("_fallback") for s in specs):
        raise Stop("error", "The vision model didn't return a usable layout for any preview.")
    pictures = sum(1 for s in specs if s.get("_fallback"))
    sz = pptx_build.image_size(shots[0][0])
    step("Writing the PowerPoint…")
    deck = build_deck_spec(specs, info["title"] or "Template", sz)
    thumb = (shots[0][0], "png" if shots[0][1] == "image/png" else "jpeg") if shots[0][1] in ("image/png", "image/jpeg") else None
    data = pptx_build.build_from_spec(deck, info["title"], thumb)
    note = note or "Rebuilt by A.F.R.A from the preview pictures (similar layouts, colours and fonts; not an exact copy)."
    return save_design(data, (info["title"] or "Template") + " (rebuilt)", url, info["kind"], info, "rebuilt",
                       {"note": note + (f" {pictures} slide(s) kept as a picture of the preview, since they couldn't be rebuilt." if pictures else ""),
                        "slides": len(specs), "pictures": pictures, **(extra or {})})


# ---------------------------------------------------------------- several links in the background
def run_link(url: str, step=lambda s: None) -> dict:
    """What the "Add from links" box does with one link, with the old behaviour kept as the fallback: a template page
    that gives no file still becomes a design from its public colours and fonts."""
    from . import gslides
    from . import google_api as g
    if designs.is_slides_link(url) and g.accounts():  # connected: the existing route copies the deck into Drive and keeps its layouts
        try:
            d = gslides.add_slides_design(url)
            return {"url": url, "status": "added", "message": d.get("note") or "Added with its real layouts.", "name": d["name"]}
        except (ValueError, g.GoogleError):
            pass
    res = import_link(url, step)
    if res["status"] in ("saved", "rebuilt"):
        res["message"] = "Saved to the Designs folder." + (" " + res["attribution"] if res.get("attribution") else "")
    elif res["status"] in ("signin", "missing", "no_file", "unclear") and classify(url) not in ("gslides", "file"):
        try:  # as before: the page's own public colours, fonts and picture
            d = designs.add_web(url)
            res["message"] += f" Added \"{d['name']}\" with its colours and fonts only."
            res["name"] = d["name"]
        except ValueError:
            pass
    return res


class Step:
    """What an import reports its progress to: call it with a text, or user_wait(text) when only the user can go on (a sign-in or
    a check in a browser window); user_wait("") says the wait is over."""

    def __init__(self, item: dict):
        self.it = item

    def __call__(self, text: str):
        self.it.update(step=text)

    def user_wait(self, text: str = ""):
        self.it.update(state="user" if text else "working", step=text or "Continuing…")


def start(links) -> dict:
    urls = designs.split_links(links) if isinstance(links, str) else list(links or [])
    if not urls:
        raise ValueError("Paste one or more links (one per line).")
    jid = hashlib.sha1(f"{time.time()}{urls}".encode()).hexdigest()[:10]
    J = JOBS[jid] = {"id": jid, "state": "working", "started": time.time(),
                     "items": [{"url": u, "state": "waiting", "step": "", "status": "", "message": "", "name": ""} for u in urls]}
    for k in [k for k, v in JOBS.items() if time.time() - v["started"] > 86400]:
        JOBS.pop(k, None)

    def run():
        for it in J["items"]:
            it.update(state="working", step="Starting…")
            try:
                res = run_link(it["url"], Step(it))
            except Exception as e:  # one bad link must not stop the others
                res = {"status": "error", "message": f"Something went wrong ({e.__class__.__name__})."}
            if res.get("status") == "claude":  # handed to the Claude app: done when Claude calls save_design (see job())
                it.update(state="claude", step="Waiting for Claude", status="", message=res.get("message", ""), name=res.get("name", ""),
                          task=res["task"], claude_msg=res["claude_msg"], hint=res.get("hint", ""))
                continue
            it.update(state="done", step="", status=res.get("status", ""), message=res.get("message", ""), name=res.get("name", ""),
                      hint=res.get("hint", ""), site=res.get("site", ""))
        J["dispatched"] = True
    threading.Thread(target=run, daemon=True).start()
    return {"job": jid}


def job(jid: str) -> dict:
    """The job with its items brought up to date: an item handed to Claude finishes when Claude saves the design."""
    j = JOBS.get(jid)
    if not j:
        raise ValueError("Unknown job (A.F.R.A was restarted?). Check the Designs folder.")
    from . import slidesgo
    for it in j["items"]:
        if it["state"] == "claude":
            slidesgo.claude_item_state(it)
    j["state"] = "done" if j.get("dispatched") and all(it["state"] == "done" for it in j["items"]) else "working"
    return j
