"""GreatPPT (https://www.greatppt.com) as a template source, three ways. GreatPPT gives out its own templates free under its own licence.

A template page is https://www.greatppt.com/downloads/<slug>/ (WordPress + Easy Digital Downloads). Its Download button is a form
(add_to_cart, edd_redirect_to_checkout=1) that leads to a $0 checkout, which needs an account.

  1. Rebuild (no account)  The page's public slide previews (lazy-loaded: the address is in data-src / data-lazy-src / srcset) are
                           downloaded and rebuilt like Slidesgo's: a GLM vision model in API mode, Claude + PPT Master in Claude mode
                           (slidesgo.rebuild_site). The sidecar says it is a rebuild, not the original file.
  2. Connected account     "Connect GreatPPT" opens a VISIBLE Edge/Chrome window with an A.F.R.A profile folder and a loopback debugging
                           port. The USER signs in there and passes any check there; A.F.R.A never sees, stores or types a password. With
                           the window connected, an imported template is downloaded the way a person does it: open the page, press its
                           Download form, follow the $0 checkout (only if the total is 0 and no payment details are asked for), take the file
                           link from the confirmation, download it with that session's cookies for that one request and keep an exact copy.
                           A Cloudflare / captcha / sign-in page is never solved or skipped: the window comes to the front and A.F.R.A waits.
  3. Downloads watcher     (watcher.py) picks up a .pptx the user downloads themselves and credits GreatPPT from the file's origin.

Not used, ever: GreatPPT's "Slidesgo Free Downloader" or any tool that fetches another site's files around its limits.
"""
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from contextlib import contextmanager
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from . import cdp, designs, slidesgo, template_import as ti
from .config import DATA_DIR

HOME = "https://www.greatppt.com"
LOGIN_URL = HOME + "/login/"
REBUILT_CREDIT = ("Rebuilt from GreatPPT (free template) — not the original file. "
                  "Template by GreatPPT, free under GreatPPT's own licence: keep its credit.")
HINT = "Connect GreatPPT for the original file, or download it in your browser — the Downloads watcher picks it up."
HINT_RECONNECT = ("Your GreatPPT sign-in has ended, so this was rebuilt from the previews. Connect GreatPPT again for the original file "
                  "(tick \"Remember me\" there so it lasts), or download it in your browser — the Downloads watcher picks it up.")
GATE_TIMEOUT = 300      # seconds A.F.R.A waits for the user to pass a check or sign in
STEP_TIMEOUT = 40       # seconds a page may take to move on
POLL = 1.5
MAX_FILE = ti.MAX_PPTX


# ---------------------------------------------------------------- the template page
def is_template_url(url: str) -> bool:
    u = urlparse(url)
    return ti.classify(url) == "greatppt" and bool(re.fullmatch(r"/downloads/[^/]+/?", u.path))


def template_slug(url: str) -> str:
    m = re.match(r"/downloads/([^/?#]+)", urlparse(url).path)
    return m.group(1).lower() if m else ""


def page_facts(html: str) -> dict:
    """Price of the Download form (0.00 = free), the download id and whether the title says Free. Pure."""
    html = (html or "")[:6_000_000]
    i = html.find("edd_download_purchase_form")
    form = html[i:i + 6000] if i >= 0 else ""
    m = re.search(r"""data-price=["']?(\d+(?:\.\d+)?)""", form)
    price = float(m.group(1)) if m else None
    d = re.search(r"""name=["']?download_id["']?\s+value=["']?(\d+)""", form) or re.search(r"""data-download-id=["']?(\d+)""", form)
    head = " ".join(re.findall(r"<(?:title|h1)[^>]*>(.*?)</(?:title|h1)>", html[:400_000], re.S | re.I))
    desc = " ".join(re.findall(r'<meta[^>]+(?:name|property)=["\'](?:description|og:title)["\'][^>]+content=["\']([^"\']+)', html[:400_000], re.I))
    word = bool(re.search(r"\bfree\b", unescape(head + " " + desc), re.I))
    return {"price": price, "download_id": d.group(1) if d else "", "free_word": word,
            "free": price == 0 or (price is None and word)}


# a preview is .../wp-content/uploads/edd/<yyyy>/<mm>/<Free-Name-...-Theme>-<n>.jpg (the cover has no number); the page lists it as
# data-src / data-lazy-src / data-src-webp / srcset / a <noscript> img, often with a ?cache query, a .webp suffix or a -768x432 size
UP_RE = re.compile(r"https?://(?:www\.)?greatppt\.com/wp-content/uploads/edd/(\d{4})/(\d{2})/([^\s\"'<>()\\,?]+?)\.(jpe?g|png|webp)(?:\.webp)?(?=[?\s\"'<>)\\,]|$)", re.I)
SIZE_RE = re.compile(r"-(\d+)x(\d+)$")


def _parse(urls_text: str):
    for y, mo, stem, ext in UP_RE.findall(urls_text):
        sized = SIZE_RE.search(stem)
        core = SIZE_RE.sub("", stem)
        m = re.fullmatch(r"(.+?)-(\d*)", core)
        if m:
            yield y, mo, m.group(1), int(m.group(2) or 0), ext.lower(), stem, bool(sized)


def preview_urls(html: str, page_url: str = "", extra: list[str] | None = None) -> list[str]:
    """The slide previews of a template page, in slide order, the full-size copy of each. Pure.

    Pictures of other templates (the related ones, the logo, blog images) have another name and are left out."""
    text = unescape((html or "")[:6_000_000]).replace("\\/", "/").replace("\\u002f", "/")
    text += "\n" + "\n".join(extra or [])
    found = list(_parse(text))
    if not found:
        return []
    og = re.search(r"""property=["']og:image["'][^>]*content=["']([^"']+)|content=["']([^"']+)["'][^>]*property=["']og:image["']""", text)
    main = next(iter(_parse(next(x for x in (og.groups() if og else ()) if x))), None) if og and any(og.groups()) else None
    bases = [f[2] for f in found]
    base = main[2] if main else max(set(bases), key=bases.count)
    best: dict[int, tuple[int, str]] = {}
    for y, mo, b, n, ext, stem, sized in found:
        if b != base:
            continue
        rank = (0 if sized else 2) + (1 if ext != "webp" else 0)  # full size beats a resized copy; jpg/png beat webp
        url = f"{HOME}/wp-content/uploads/edd/{y}/{mo}/{stem}.{ext}"
        if n not in best or rank > best[n][0]:
            best[n] = (rank, url)
    return [best[n][1] for n in sorted(best)][:slidesgo.MAX_SLIDES]


def safe_image_url(u: str) -> bool:
    """Only GreatPPT's own public picture folder, never a login, checkout or download address."""
    p = urlparse(u)
    return (p.scheme in ("http", "https") and (p.hostname or "") in ("greatppt.com", "www.greatppt.com") and p.path.startswith("/wp-content/uploads/")
            and not ti.LOGIN_RE.search(p.netloc + p.path) and "/checkout" not in p.path and ti.public_host(u))


SITE = slidesgo.SITES["greatppt"] = {
    "key": "greatppt", "name": "GreatPPT", "rebuilt_credit": REBUILT_CREDIT,
    "premium_message": "This GreatPPT template isn't free, so A.F.R.A can't import it.",
    "origins": [HOME], "image_host": "greatppt.com/wp-content/uploads/",
    "slug": template_slug, "preview_urls": preview_urls, "safe": safe_image_url,
    "not_free": "That GreatPPT page doesn't say the template is free, so A.F.R.A didn't try to rebuild it.",
    "no_previews": "A.F.R.A couldn't find the preview slides on that GreatPPT page.",
    "download_failed": "The preview pictures couldn't be downloaded from GreatPPT.",
    "keep": "Keep any GreatPPT credit or credits slide that is in the previews.",
    "forbidden": "Do NOT sign in to GreatPPT, do NOT use GreatPPT's \"Slidesgo Free Downloader\" or any other third-party downloader, and do not get around GreatPPT's limits",
    "note": "Rebuilt by Claude with PPT Master from GreatPPT's public preview pictures; similar to the original, not an exact copy.",
    "remind": "remind them to keep GreatPPT's credit",
}


def rebuild(page_url: str, html: str, info: dict, step=lambda s: None) -> dict:
    return slidesgo.rebuild_site(SITE, page_url, html, info, step)


# ---------------------------------------------------------------- the account: a visible browser the user signs in to
class NotConnected(Exception):
    """The browser's GreatPPT sign-in is gone (or was never made)."""


def browser_dir() -> Path:
    return DATA_DIR / "browser"


def profile_dir() -> Path:
    return browser_dir() / "greatppt"


def state_file() -> Path:
    return browser_dir() / "greatppt.json"


_LOCK = threading.RLock()


def read_state() -> dict:
    try:
        d = json.loads(state_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(**kw) -> dict:
    with _LOCK:
        st = {**read_state(), **kw}  # only a port, a process id and a yes/no: never a cookie or a password
        browser_dir().mkdir(parents=True, exist_ok=True)
        state_file().write_text(json.dumps(st), encoding="utf-8")
        return st


def endpoint() -> str:
    port = read_state().get("port")
    return f"http://127.0.0.1:{port}" if port else ""


def window_open() -> bool:
    ep = endpoint()
    return bool(ep) and cdp.alive(ep)


def launch(exe: str, port: int, url: str, minimized: bool) -> subprocess.Popen:
    """A visible browser on its own profile folder and a loopback debugging port."""
    profile_dir().mkdir(parents=True, exist_ok=True)
    args = [exe, f"--remote-debugging-port={port}", "--remote-debugging-address=127.0.0.1", f"--user-data-dir={profile_dir()}",
            "--no-first-run", "--no-default-browser-check", "--window-size=1200,860"]
    if minimized:
        args.append("--start-minimized")
    return subprocess.Popen([*args, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ensure_window(url: str = LOGIN_URL, minimized: bool = False) -> str:
    """The debugging endpoint of A.F.R.A's GreatPPT browser, started when it isn't running. ti.Stop says why when it can't."""
    with _LOCK:
        if window_open():
            return endpoint()
        exes = ti.find_browsers()
        if not exes:
            raise ti.Stop("missing", "Connecting GreatPPT needs Microsoft Edge or Google Chrome, and neither was found.")
        port = ti.free_port()
        proc = launch(exes[0], port, url, minimized)
        write_state(port=port, pid=proc.pid)
        for _ in range(60):
            if cdp.alive(f"http://127.0.0.1:{port}"):
                return f"http://127.0.0.1:{port}"
            if proc.poll() is not None:
                break
            time.sleep(0.5)
        raise ti.Stop("error", "The GreatPPT browser window didn't start. If a GreatPPT window is already open, close it and try again.")


def _logged_in(endpoint_: str) -> bool | None:
    """True / False when a GreatPPT page in the window says whether the user is signed in; None when no such page is open."""
    try:
        for t in cdp.pages(endpoint_):
            if urlparse(t.get("url", "")).hostname in ("greatppt.com", "www.greatppt.com"):
                tab = cdp.Tab(t["webSocketDebuggerUrl"], 10)
                try:
                    return bool(tab.eval(LOGGED_IN_JS))
                finally:
                    tab.close()
    except (httpx.HTTPError, cdp.CDPError, OSError, KeyError, ValueError):
        pass
    return None


LOGGED_IN_JS = "!!(document.body && document.body.classList.contains('logged-in')) || !!document.querySelector('a[href*=\"logout\"]')"


def connect() -> dict:
    """Open (or bring forward) the visible GreatPPT window at the sign-in page. The user does the rest there."""
    ep = ensure_window(LOGIN_URL, minimized=False)
    try:
        t = cdp.new_page(ep, LOGIN_URL) if _logged_in(ep) is not None else None
        if t:
            cdp.Tab(t["webSocketDebuggerUrl"], 10).call("Page.bringToFront")
    except (httpx.HTTPError, cdp.CDPError, OSError, KeyError):
        pass
    return status()


def status() -> dict:
    """{"connected", "open", "browser"}: connected only once a GreatPPT page was seen signed in; the window may be closed since."""
    st = read_state()
    opened = window_open()
    if opened:
        seen = _logged_in(endpoint())
        if seen is not None:
            st = write_state(connected=seen)
    return {"connected": bool(st.get("connected")) and profile_dir().exists(), "open": opened, "browser": bool(ti.find_browsers()),
            "login_url": LOGIN_URL}


def disconnect() -> dict:
    """Close the window and delete the profile folder (and with it every cookie of the sign-in)."""
    st = read_state()
    ep = endpoint()
    if ep and cdp.alive(ep):
        try:
            cdp.browser_tab(ep).call("Browser.close")
        except (cdp.CDPError, OSError, httpx.HTTPError):
            pass
    pid = st.get("pid")
    if pid:  # only the process A.F.R.A started, by its id
        for _ in range(10):
            if not ep or not cdp.alive(ep):
                break
            time.sleep(0.3)
        else:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
            else:
                try:
                    import os
                    import signal
                    os.kill(int(pid), signal.SIGTERM)
                except OSError:
                    pass
    for _ in range(10):  # Windows lets go of the folder a moment after the browser ends
        shutil.rmtree(profile_dir(), ignore_errors=True)
        if not profile_dir().exists():
            break
        time.sleep(0.5)
    state_file().unlink(missing_ok=True)
    return {"connected": False, "open": False, "browser": bool(ti.find_browsers()), "login_url": LOGIN_URL}


# ---------------------------------------------------------------- reading the page state (scripts only read; none types or fills a field)
STATE_JS = """(() => {
  const vis = (e) => !!(e && e.offsetParent !== null);
  const b = document.body;
  return {url: location.href, title: document.title, text: ((b && b.innerText) || '').slice(0, 400), ready: document.readyState,
    logged_in: !!(b && b.classList.contains('logged-in')) || !!document.querySelector('a[href*="logout"]'),
    challenge: !!document.querySelector('#challenge-form, #cf-challenge-running, .cf-turnstile, iframe[src*="challenges.cloudflare.com"], iframe[src*="recaptcha"], iframe[src*="hcaptcha"]'),
    login_form: [...document.querySelectorAll('#loginform, #edd_login_form, form[action*="wp-login"]')].some(vis)};
})()"""

FORM_JS = """(() => {
  const f = document.querySelector('form.edd_download_purchase_form');
  if (!f) return {form: false};
  const btn = f.querySelector('button[data-price], input[data-price]');
  return {form: true, price: btn ? btn.getAttribute('data-price') : null, id: (f.querySelector('input[name=download_id]') || {}).value || ''};
})()"""

CLICK_DOWNLOAD_JS = """(() => {
  const f = document.querySelector('form.edd_download_purchase_form');
  const b = f && (f.querySelector('button.edd-add-to-cart') || f.querySelector('input[type=submit]'));
  if (!b) return false;
  b.click();
  return true;
})()"""

SUBMIT_FORM_JS = "(() => { const f = document.querySelector('form.edd_download_purchase_form'); if (!f) return false; f.submit(); return true; })()"

CHECKOUT_JS = """(() => {
  const vis = (e) => !!(e && e.offsetParent !== null);
  const amounts = [...document.querySelectorAll('.edd_cart_total .edd_cart_amount, #edd_checkout_cart .edd_cart_total *')]
    .map((e) => e.getAttribute('data-total') || e.textContent).filter((x) => x && /\\d|free/i.test(x));
  const terms = [...document.querySelectorAll('#edd_agree_to_terms, input[name=edd_agree_to_terms], #edd-agree-to-terms, #edd_agree_to_privacy_policy, input[name=edd_agree_to_privacy_policy]')];
  const btn = document.querySelector('#edd-purchase-button, input[name=edd_submit], #edd_purchase_submit input[type=submit], #edd_purchase_submit button');
  return {url: location.href, total: amounts.length ? amounts[amounts.length - 1] : null, items: document.querySelectorAll('.edd_cart_item').length,
    card_fields: [...document.querySelectorAll('input[name=card_number], input[name=card_cvc], #card_number, #edd_cc_fields input, #edd-card-number-wrap input')].some(vis),
    terms_pending: terms.some((e) => !e.checked), button: !!btn};
})()"""

CLICK_PURCHASE_JS = """(() => {
  const b = document.querySelector('#edd-purchase-button, input[name=edd_submit], #edd_purchase_submit input[type=submit], #edd_purchase_submit button');
  if (!b) return false;
  b.click();
  return true;
})()"""

LINKS_JS = "[...document.querySelectorAll('a[href]')].slice(0, 400).map((a) => ({href: a.href, text: (a.textContent || '').trim().slice(0, 80), cls: a.className || ''}))"

CHALLENGE_RE = re.compile(r"just a moment|attention required|verify (that )?you are human|security check|checking your browser|one more step|"
                          r"checking if the site connection is secure|are you a robot", re.I)
LOGIN_PATH_RE = re.compile(r"^/(login|log-in|signin|sign-in|wp-login\.php|my-account/lost-password)(/|$)", re.I)
FILE_LINK_RE = re.compile(r"edd_action=download|edd-free-download|download_file|/edd-download/|edd_file=|\bedd-token|\.(pptx|potx|zip)(\?|$)", re.I)
SKIP_LINK_RE = re.compile(r"/(checkout|cart|login|log-?in|logout|wp-login\.php|register)(/|\?|$)|action=logout|action=lostpassword", re.I)


def gate(st: dict) -> str:
    """"challenge" (Cloudflare / captcha), "login" (the sign-in page) or "" for an ordinary page. Pure."""
    path = urlparse(st.get("url", "")).path
    if st.get("challenge") or "/cdn-cgi/" in path or CHALLENGE_RE.search(st.get("title", "") or "") \
            or (CHALLENGE_RE.search((st.get("text") or "")[:200]) and len(st.get("text") or "") < 400):
        return "challenge"
    if LOGIN_PATH_RE.match(path) or (st.get("login_form") and not st.get("logged_in")):
        return "login"
    return ""


def money(v) -> float | None:
    """"$0.00" / "Free" / "0" -> 0.0; "$9.00" -> 9.0; unreadable -> None. Pure."""
    t = str(v if v is not None else "").strip().lower()
    if not t:
        return None
    if t in ("free", "gratis"):
        return 0.0
    nums = re.sub(r"[^\d.,]", "", t)
    if not nums:
        return None
    if "," in nums and "." in nums:
        nums = nums.replace(",", "") if nums.rfind(".") > nums.rfind(",") else nums.replace(".", "").replace(",", ".")
    elif "," in nums:
        nums = nums.replace(",", ".") if len(nums.rsplit(",", 1)[-1]) <= 2 else nums.replace(",", "")
    try:
        return float(nums)
    except ValueError:
        return None


def pick_download_links(links: list[dict], base: str) -> list[str]:
    """Links on a confirmation / account page that serve the purchased file, best first. Pure."""
    host = urlparse(base).hostname or "greatppt.com"
    out = []
    for a in links:
        href = urljoin(base, a.get("href") or "")
        if not href or SKIP_LINK_RE.search(href) or not ti.public_host(href):
            continue
        cls = a.get("cls") or ""
        direct = bool(FILE_LINK_RE.search(href)) or "edd_download_file_link" in cls or "edd_download_link" in cls
        if direct and (urlparse(href).hostname == host or re.search(r"\.(pptx|potx|zip)(\?|$)", href, re.I)):
            out.append(href)
    return list(dict.fromkeys(out))


class Session:
    """One tab of A.F.R.A's GreatPPT browser. Tests replace it with a fake that has the same methods."""

    def __init__(self, ep: str):
        self.ep = ep
        page = cdp.new_page(ep, "about:blank")
        self.tab_id = page["id"]
        self.tab = cdp.Tab(page["webSocketDebuggerUrl"], 60)

    def goto(self, url: str):
        self.tab.call("Page.navigate", {"url": url})

    def eval(self, js: str):
        return self.tab.eval(js)

    def state(self) -> dict:
        return self.eval(STATE_JS) or {}

    def front(self):
        try:
            self.tab.call("Page.bringToFront")
            w = cdp.browser_tab(self.ep)
            wid = w.call("Browser.getWindowForTarget", {"targetId": self.tab_id}).get("windowId")
            w.call("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "normal"}})
            w.close()
        except (cdp.CDPError, OSError, httpx.HTTPError):
            pass

    def cookies(self) -> str:
        c = self.tab.call("Network.getCookies", {"urls": [HOME + "/"]}).get("cookies") or []
        return "; ".join(f"{x['name']}={x['value']}" for x in c if x.get("name"))

    def user_agent(self) -> str:
        return self.eval("navigator.userAgent") or ti.UA

    def download(self, url: str, dest: Path, timeout: float = 120) -> Path | None:
        """The browser's own download of one address into dest (a fallback when a plain request is not accepted)."""
        dest.mkdir(parents=True, exist_ok=True)
        w = cdp.browser_tab(self.ep)
        w.call("Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(dest)})
        self.goto(url)
        end = time.time() + timeout
        while time.time() < end:
            done = [f for f in dest.iterdir() if f.is_file() and not f.name.endswith((".crdownload", ".tmp"))]
            if done:
                w.close()
                return done[0]
            time.sleep(1)
        w.close()
        return None

    def close(self):
        try:
            self.tab.close()
            httpx.get(f"{self.ep}/json/close/{self.tab_id}", timeout=5)
        except (httpx.HTTPError, OSError):
            pass


@contextmanager
def open_session(step=lambda s: None):
    """A tab in the GreatPPT browser (started minimised when the user's window isn't open)."""
    was_open = window_open()
    ep = ensure_window(HOME + "/", minimized=not was_open)
    s = Session(ep)
    try:
        yield s
    finally:
        s.close()


def _sleep(sec: float):
    time.sleep(sec)


def _now() -> float:
    return time.time()


def wait_ready(sess, timeout: float = STEP_TIMEOUT) -> dict:
    """The page state once the page has loaded."""
    end = _now() + timeout
    st = {}
    while _now() < end:
        try:
            st = sess.state()
        except cdp.CDPError:
            st = {}  # the page is switching documents
        if st.get("ready") == "complete" or st.get("challenge"):
            return st
        _sleep(POLL / 3)
    return st


def clear_gates(sess, step, resume_url: str, timeout: float = GATE_TIMEOUT) -> dict:
    """Wait for the user while a check or the sign-in page is up: the window comes forward and A.F.R.A does nothing until the page
    is an ordinary page again. Never solves or skips anything. ti.Stop("waiting") after `timeout` seconds."""
    st = wait_ready(sess)
    kind = gate(st)
    if not kind:
        return st
    user_wait = getattr(step, "user_wait", lambda t: None)
    user_wait("Waiting for you in the GreatPPT window")
    sess.front()
    end = _now() + timeout
    while _now() < end:
        _sleep(POLL * 1.3)
        try:
            st = sess.state()
        except cdp.CDPError:
            continue
        if not gate(st) and st.get("ready") == "complete":
            user_wait("")
            if kind == "login" or urlparse(st.get("url", "")).path != urlparse(resume_url).path:
                sess.goto(resume_url)  # signed in: back to where the import was
                return clear_gates(sess, step, resume_url, max(30, end - _now()))
            return st
    user_wait("")
    raise ti.Stop("waiting", "GreatPPT is still waiting for you in its window (a check or the sign-in). Nothing was downloaded; add the link again when you are ready.")


def extract_pptx(data: bytes) -> tuple[bytes, str] | None:
    """The .pptx itself, or the .pptx/.potx inside a zip that holds one. None when there is none."""
    if ti.is_pptx(data):
        return data, ""
    if not ti.is_zip_magic(data):
        return None
    import io
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        names = sorted((i for i in z.infolist() if re.search(r"\.(pptx|potx)$", i.filename, re.I) and not i.filename.rsplit("/", 1)[-1].startswith(("~$", "."))
                        and i.file_size <= MAX_FILE), key=lambda i: -i.file_size)
        for i in names:
            inner = z.read(i)
            if ti.is_pptx(inner):
                return inner, re.sub(r"\.(pptx|potx)$", "", i.filename.rsplit("/", 1)[-1], flags=re.I)
    except (zipfile.BadZipFile, OSError):
        pass
    return None


def http_file(url: str, cookies: str, ua: str, referer: str) -> bytes | None:
    """GET one address (following redirects by hand: the session's cookies go to greatppt.com only, never anywhere else, never stored)."""
    with httpx.Client(timeout=60, follow_redirects=False, transport=ti.TRANSPORT) as h:
        for _ in range(6):
            host = urlparse(url).hostname or ""
            if not ti.public_host(url):
                return None
            headers = {"User-Agent": ua, "Referer": referer}
            if host in ("greatppt.com", "www.greatppt.com") and cookies:
                headers["Cookie"] = cookies
            with h.stream("GET", url, headers=headers) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    url = urljoin(url, r.headers["location"])
                    if ti.LOGIN_RE.search(urlparse(url).netloc + urlparse(url).path):
                        return None
                    continue
                if r.status_code >= 400 or "html" in r.headers.get("content-type", ""):
                    return None
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf += chunk
                    if len(buf) > MAX_FILE:
                        raise ti.Stop("error", "That file is over 120 MB, so A.F.R.A didn't download it.")
                return bytes(buf)
    return None


def exact_file(sess, page_url: str, step=lambda s: None, tmp: Path | None = None) -> tuple[bytes, str]:
    """The original .pptx through the connected account, the way a person does it. ti.Stop (paid, waiting, error…) or NotConnected."""
    step("Opening the GreatPPT page…")
    sess.goto(page_url)
    st = clear_gates(sess, step, page_url)
    if not st.get("logged_in"):
        raise NotConnected()
    form = sess.eval(FORM_JS) or {}
    if not form.get("form"):
        raise ti.Stop("error", "GreatPPT's page has no Download button A.F.R.A can use.")
    if money(form.get("price")) not in (0.0, None):
        raise ti.Stop("paid", "This GreatPPT template has a price, so A.F.R.A didn't buy it. Only free ($0) templates are downloaded.")
    step("Pressing Download…")
    if not sess.eval(CLICK_DOWNLOAD_JS):
        raise ti.Stop("error", "GreatPPT's Download button couldn't be pressed.")
    end, resubmitted = _now() + STEP_TIMEOUT, False
    while "/checkout" not in urlparse(sess.state().get("url", "")).path:
        if _now() > end:
            if resubmitted:
                raise ti.Stop("error", "GreatPPT didn't go to its checkout page.")
            resubmitted, end = True, _now() + STEP_TIMEOUT
            sess.eval(SUBMIT_FORM_JS)  # the form's own plain submit, as a browser without scripts would
        _sleep(POLL)
        if gate(sess.state()):
            clear_gates(sess, step, page_url)
            sess.eval(CLICK_DOWNLOAD_JS)
    st = clear_gates(sess, step, urljoin(HOME, "/checkout/"))
    step("Checking the checkout is free…")
    c = sess.eval(CHECKOUT_JS) or {}
    total = money(c.get("total"))
    if total is None:
        raise ti.Stop("paid", "A.F.R.A couldn't read the total on GreatPPT's checkout, so it stopped. Nothing was bought.")
    if total != 0 or c.get("card_fields"):
        raise ti.Stop("paid", "GreatPPT's checkout is not $0 or asks for payment details, so A.F.R.A stopped. Nothing was bought.")
    if not c.get("button"):
        raise ti.Stop("error", "GreatPPT's checkout has no button A.F.R.A can use.")
    if c.get("terms_pending"):  # agreeing to terms is the user's own decision
        user_wait = getattr(step, "user_wait", lambda t: None)
        user_wait("Waiting for you in the GreatPPT window: tick the agreement box and press Download")
        sess.front()
        end = _now() + GATE_TIMEOUT
        while _now() < end:
            _sleep(POLL)
            s2 = sess.state()
            if "/checkout" not in urlparse(s2.get("url", "")).path:
                break
            if not (sess.eval(CHECKOUT_JS) or {}).get("terms_pending"):
                user_wait("")
                sess.eval(CLICK_PURCHASE_JS)
                break
        else:
            user_wait("")
            raise ti.Stop("waiting", "GreatPPT is still waiting for you to agree to its terms in its window. Nothing was downloaded.")
        user_wait("")
    else:
        step("Completing the $0 order…")
        sess.eval(CLICK_PURCHASE_JS)
    end = _now() + STEP_TIMEOUT
    while "/checkout" in urlparse(sess.state().get("url", "")).path and _now() < end:
        _sleep(POLL)
        if gate(sess.state()):
            clear_gates(sess, step, urljoin(HOME, "/checkout/"))
    st = clear_gates(sess, step, st.get("url") or HOME)
    step("Finding the download link…")
    links = pick_download_links(sess.eval(LINKS_JS) or [], st.get("url") or HOME)
    for path in ("/purchase-history/", "/account/", "/my-account/") if not links else ():
        sess.goto(HOME + path)
        s3 = clear_gates(sess, step, HOME + path)
        links = pick_download_links(sess.eval(LINKS_JS) or [], s3.get("url") or HOME)
        if links:
            break
    if not links:
        raise ti.Stop("error", "The order went through but A.F.R.A found no download link on GreatPPT's confirmation or account page. "
                               "Download the file in your browser: the Downloads watcher picks it up.")
    step("Downloading the file…")
    cookies, ua = sess.cookies(), sess.user_agent()
    for link in links[:4]:
        data = http_file(link, cookies, ua, st.get("url") or HOME)
        got = extract_pptx(data) if data else None
        if not got and tmp is not None:  # the plain request wasn't accepted: let the browser download it
            f = sess.download(link, tmp)
            if f:
                got = extract_pptx(f.read_bytes())
        if got:
            return got[0], got[1]
    raise ti.Stop("error", "What GreatPPT served wasn't a PowerPoint file, so A.F.R.A didn't keep it.")


def download_exact(page_url: str, info: dict, step=lambda s: None) -> dict:
    with _LOCK_DOWNLOAD:
        tmp = browser_dir() / "greatppt_dl"
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            with open_session(step) as sess:
                data, name = exact_file(sess, page_url, step, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return ti.save_design(data, info.get("title") or name or "GreatPPT template", page_url, "greatppt", info, "download",
                          {"via": "GreatPPT account (free licence)"})


_LOCK_DOWNLOAD = threading.Lock()


def import_page(page_url: str, html: str, info: dict, step=lambda s: None) -> dict:
    """A GreatPPT template page -> the original file when the account is connected, else rebuilt from its previews (with a hint)."""
    if info.get("premium"):
        raise ti.Stop("premium", SITE["premium_message"])
    if not info.get("free"):
        raise ti.Stop("unclear", SITE["not_free"])
    hint = HINT
    if read_state().get("connected"):
        try:
            return download_exact(page_url, info, step)
        except NotConnected:
            write_state(connected=False)
            hint = HINT_RECONNECT
    res = rebuild(page_url, html, info, step)
    return {**res, "hint": hint, "site": "GreatPPT"}
