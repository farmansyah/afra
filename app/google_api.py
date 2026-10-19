"""Google Drive, Slides and Sheets over plain HTTPS (httpx). No Google SDK.

Sign-in is Google's installed-app flow with PKCE: the browser opens Google's consent page, Google sends the code
back to a one-off server on 127.0.0.1 at a random port, and A.F.R.A swaps it for tokens. Each person connects their
own Google accounts (several per install, one is the default). Nothing here is shared through the shared library.

  data/google/
    client.json            the OAuth client ("Desktop app") pasted in Settings › Google; never in the code or in git
    accounts/<email>.json  refresh token, current access token, cached folder ids
    index.json             which account is the default

Scopes: drive.file (Drive: only files A.F.R.A made or the user picked), presentations, spreadsheets, openid email.
The Slides and Sheets scopes reach any deck or sheet the account can open, so a pasted Sheet link can be read; only
Drive operations (copy, export, list) are limited to A.F.R.A's own files.
"""
import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

import httpx

from .config import DATA_DIR

DIR = DATA_DIR / "google"
AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
REVOKE = "https://oauth2.googleapis.com/revoke"
USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
DRIVE = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
SLIDES = "https://slides.googleapis.com/v1/presentations"
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
SCOPES = ["openid", "email", "https://www.googleapis.com/auth/drive.file",
          "https://www.googleapis.com/auth/presentations", "https://www.googleapis.com/auth/spreadsheets"]
FOLDER = "A.F.R.A Decks"
TEMPLATES = "Templates"
MIME_FOLDER = "application/vnd.google-apps.folder"
MIME_SLIDES = "application/vnd.google-apps.presentation"
MIME_DOC = "application/vnd.google-apps.document"
MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_SHEET = "application/vnd.google-apps.spreadsheet"
MIME_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TRANSPORT = None  # tests put an httpx.MockTransport here
FLOWS: dict[str, dict] = {}  # sign-ins in progress, by OAuth state
_LOCK = threading.Lock()


class GoogleError(Exception):
    def __init__(self, msg, status=502):
        super().__init__(msg)
        self.status = status


def _http() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(120, connect=20), transport=TRANSPORT, follow_redirects=True)


def _read(f: Path) -> dict:
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(f: Path, obj):
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    if os.name != "nt":
        os.chmod(tmp, 0o600)  # tokens: readable by this user only
    tmp.replace(f)


# ---------------------------------------------------------------- the OAuth client (pasted by the user)
def load_client() -> dict:
    """data/google/client.json (pasted in Settings), or the downloaded file dropped in as data/google_client.json."""
    c = _read(DIR / "client.json")
    if not c.get("client_id"):
        j = _read(DIR.parent / "google_client.json")
        inner = j.get("installed") or {}
        if inner.get("client_id") and inner.get("client_secret"):
            c = {**c, "client_id": inner["client_id"], "client_secret": inner["client_secret"], "project_id": inner.get("project_id", "")}
    return c


def save_client(text: str = "", picker_key: str | None = None) -> dict:
    c = load_client()
    if (text or "").strip():
        try:
            j = json.loads(text)
        except ValueError:
            raise ValueError("That isn't the JSON file from Google. Open the downloaded client_secret_….json and paste all of it.")
        if "web" in j:
            raise ValueError("This is a \"Web application\" client. Create an OAuth client of type \"Desktop app\" instead (see the steps).")
        inner = j.get("installed") or j
        cid, secret = (inner.get("client_id") or "").strip(), (inner.get("client_secret") or "").strip()
        if not cid.endswith(".apps.googleusercontent.com") or not secret:
            raise ValueError("No client_id/client_secret in that JSON. Download it again from Google Cloud › Credentials.")
        c.update({"client_id": cid, "client_secret": secret, "project_id": inner.get("project_id", "")})
    if picker_key is not None:
        c["picker_api_key"] = picker_key.strip()
    if not c.get("client_id"):
        raise ValueError("Paste the client JSON first.")
    _write(DIR / "client.json", c)
    return client_status()


def client_status() -> dict:
    c = load_client()  # never hand the secret to the page
    return {"configured": bool(c.get("client_id") and c.get("client_secret")), "project_id": c.get("project_id", ""),
            "client_hint": (c.get("client_id") or "")[:12] + "…" if c.get("client_id") else "", "picker": bool(c.get("picker_api_key"))}


# ---------------------------------------------------------------- sign-in (PKCE + loopback)
def pkce() -> tuple[str, str]:
    """(verifier, S256 challenge), RFC 7636."""
    verifier = base64.urlsafe_b64encode(os.urandom(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def auth_url(client_id: str, redirect_uri: str, state: str, challenge: str, login_hint: str = "") -> str:
    q = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(SCOPES),
         "state": state, "code_challenge": challenge, "code_challenge_method": "S256", "access_type": "offline",
         "prompt": "consent select_account", "include_granted_scopes": "true"}
    if login_hint:
        q["login_hint"] = login_hint
    return AUTH + "?" + urlencode(q)


DONE_PAGE = """<!doctype html><meta charset="utf-8"><title>A.F.R.A</title><body style="font:16px system-ui;background:#14213d;
color:#f6f5f1;display:grid;place-items:center;min-height:90vh;text-align:center"><div><h2>{title}</h2><p>{msg}</p></div>"""


def start_connect(login_hint: str = "") -> dict:
    c = load_client()
    if not c.get("client_id"):
        raise ValueError("Paste your Google client JSON first (the steps are on this page).")
    verifier, challenge = pkce()
    state = secrets.token_urlsafe(16)
    flow = {"status": "waiting", "email": "", "error": "", "started": time.time()}

    class Back(BaseHTTPRequestHandler):
        def do_GET(self):
            q = parse_qs(urlparse(self.path).query)
            if q.get("state", [""])[0] != state:
                return self._page(400, "Not this sign-in", "Start again from A.F.R.A.", stop=False)
            if "error" in q:
                flow.update(status="error", error="You cancelled, or Google refused: " + q["error"][0]
                            + (". Is this account added under Test users on the OAuth consent screen?" if q["error"][0] == "access_denied" else ""))
                return self._page(200, "Not connected", flow["error"])
            try:
                flow.update(status="done", email=finish_connect(q.get("code", [""])[0], verifier, redirect, q.get("scope", [""])[0]))
                self._page(200, "A.F.R.A is connected", f"Google account {flow['email']} is ready. You can close this tab.")
            except Exception as e:
                flow.update(status="error", error=str(e))
                self._page(200, "Not connected", str(e))

        def _page(self, code, title, msg, stop=True):
            body = DONE_PAGE.format(title=title, msg=msg.replace("<", "&lt;")).encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            if stop:
                threading.Thread(target=srv.shutdown, daemon=True).start()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Back)
    redirect = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def give_up():  # nobody came back in 10 minutes
        if flow["status"] == "waiting":
            flow.update(status="error", error="The sign-in timed out. Click Connect again.")
            srv.shutdown()
    threading.Timer(600, give_up).start()
    for k in [k for k, v in FLOWS.items() if time.time() - v["started"] > 3600]:
        FLOWS.pop(k, None)
    FLOWS[state] = flow
    return {"state": state, "url": auth_url(c["client_id"], redirect, state, challenge, login_hint)}


def connect_status(state: str) -> dict:
    f = FLOWS.get(state)
    if not f:
        raise ValueError("Unknown sign-in. Click Connect again.")
    return {k: f[k] for k in ("status", "email", "error")}


def _email_from(tokens: dict) -> str:
    idt = tokens.get("id_token") or ""
    try:
        part = idt.split(".")[1]
        email = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))).get("email", "")
        if email:
            return email
    except (IndexError, ValueError):
        pass
    with _http() as h:
        r = h.get(USERINFO, headers={"Authorization": f"Bearer {tokens.get('access_token')}"})
    return r.json().get("email", "") if r.status_code == 200 else ""


def finish_connect(code: str, verifier: str, redirect: str, granted: str = "") -> str:
    c = load_client()
    with _http() as h:
        r = h.post(TOKEN, data={"code": code, "client_id": c["client_id"], "client_secret": c["client_secret"],
                                "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier})
    if r.status_code != 200:
        raise GoogleError("Google didn't accept the sign-in: " + _err_text(r))
    tok = r.json()
    email = _email_from(tok)
    if not email:
        raise GoogleError("Google didn't tell A.F.R.A which account this is. Try again.")
    scope = tok.get("scope") or granted
    missing = [s.rsplit("/", 1)[-1] for s in SCOPES[2:] if s not in scope]
    if missing:
        raise GoogleError("Some permissions were left unticked (" + ", ".join(missing) + "). Connect again and tick all boxes.")
    old = _read(_acct_file(email))
    a = {**old, "email": email, "refresh_token": tok.get("refresh_token") or old.get("refresh_token", ""),
         "access_token": tok["access_token"], "expires": time.time() + int(tok.get("expires_in", 3600)),
         "added": old.get("added") or time.time(), "needs_reconnect": False}
    if not a["refresh_token"]:
        raise GoogleError("Google sent no refresh token. Remove A.F.R.A at myaccount.google.com › Security › Third-party access, then connect again.")
    _write(_acct_file(email), a)
    if not default_email():
        set_default(email)
    return email


# ---------------------------------------------------------------- accounts and tokens
def _acct_file(email: str) -> Path:
    return DIR / "accounts" / (re.sub(r"[^a-z0-9@._-]", "_", email.lower()) + ".json")


def default_email() -> str:
    d = _read(DIR / "index.json").get("default", "")
    if d and _acct_file(d).exists():
        return d
    first = sorted((DIR / "accounts").glob("*.json")) if (DIR / "accounts").exists() else []
    return _read(first[0]).get("email", "") if first else ""


def set_default(email: str):
    if not _acct_file(email).exists():
        raise ValueError(f"{email} is not connected.")
    _write(DIR / "index.json", {"default": email})


def accounts() -> list[dict]:
    d = default_email()
    out = []
    for f in sorted((DIR / "accounts").glob("*.json")) if (DIR / "accounts").exists() else []:
        a = _read(f)
        if a.get("email"):
            out.append({"email": a["email"], "default": a["email"] == d, "needs_reconnect": bool(a.get("needs_reconnect")), "added": a.get("added", 0)})
    return out


def account(email: str | None = None) -> dict:
    email = email or default_email()
    if not email:
        raise GoogleError("Connect a Google account first (Slides › Connect Google).", 400)
    a = _read(_acct_file(email))
    if not a:
        raise GoogleError(f"{email} is not connected to A.F.R.A.", 400)
    return a


def access_token(email: str | None = None, force: bool = False) -> tuple[str, str]:
    """(email, a valid access token); refreshes a minute before expiry."""
    with _LOCK:
        a = account(email)
        if not force and a.get("access_token") and a.get("expires", 0) > time.time() + 60:
            return a["email"], a["access_token"]
        c = load_client()
        if not c.get("client_id"):
            raise GoogleError("The Google client is missing. Paste the client JSON again (Settings › Google).", 400)
        try:
            with _http() as h:
                r = h.post(TOKEN, data={"client_id": c["client_id"], "client_secret": c["client_secret"],
                                        "refresh_token": a.get("refresh_token", ""), "grant_type": "refresh_token"})
        except httpx.HTTPError as e:  # offline: not a reason to reconnect
            raise GoogleError(f"Can't reach Google ({e.__class__.__name__}). Check the internet connection.")
        if r.status_code in (400, 401) and "invalid_grant" in r.text:
            a["needs_reconnect"] = True
            _write(_acct_file(a["email"]), a)
            raise GoogleError(f"Google sign-in for {a['email']} has expired (in Testing mode this happens every 7 days). "
                              "Click Reconnect next to the account.", 401)
        if r.status_code != 200:
            raise GoogleError("Google token refresh failed: " + _err_text(r))
        t = r.json()
        a.update(access_token=t["access_token"], expires=time.time() + int(t.get("expires_in", 3600)), needs_reconnect=False)
        if t.get("refresh_token"):
            a["refresh_token"] = t["refresh_token"]
        _write(_acct_file(a["email"]), a)
        return a["email"], a["access_token"]


def disconnect(email: str) -> dict:
    a = _read(_acct_file(email))
    if a.get("refresh_token"):
        try:  # revoking the refresh token also ends its access tokens
            with _http() as h:
                h.post(REVOKE, data={"token": a["refresh_token"]})
        except httpx.HTTPError:
            pass
    _acct_file(email).unlink(missing_ok=True)
    if _read(DIR / "index.json").get("default") == email:
        (DIR / "index.json").unlink(missing_ok=True)
        if default_email():
            set_default(default_email())
    return {"accounts": accounts()}


def picker_info(email: str | None = None) -> dict:
    """What the Google Picker in the page needs; the API key is optional (without it: link or file upload)."""
    c = load_client()
    if not c.get("picker_api_key"):
        raise ValueError("Add a Picker API key in Settings › Google to pick files from Drive.")
    who, tok = access_token(email)
    return {"email": who, "token": tok, "api_key": c["picker_api_key"], "client_id": c["client_id"],
            "app_id": c["client_id"].split("-")[0]}


# ---------------------------------------------------------------- calling Google
def _err_text(r: httpx.Response) -> str:
    try:
        j = r.json()
        e = j.get("error")
        msg = (e.get("message") if isinstance(e, dict) else j.get("error_description") or e) or r.text
    except ValueError:
        msg = r.text
    return f"{msg} ({r.status_code})"[:600]


def _explain(r: httpx.Response) -> str:
    msg = _err_text(r)
    if "has not been used in project" in msg or "is disabled" in msg:
        api = "Drive" if "drive" in msg.lower() else "Slides" if "slides" in msg.lower() else "Sheets" if "sheets" in msg.lower() else "the"
        return f"Turn on the Google {api} API in your Google Cloud project (APIs & Services › Library), wait a minute, then try again."
    if r.status_code == 404 or (r.status_code == 403 and "insufficient" in msg.lower()):
        return ("Google says A.F.R.A can't open that file. A.F.R.A only reaches files it made, files you picked with "
                "\"Pick from Drive\", and decks or sheets your account can open by link. " + msg)
    return "Google: " + msg


def call(method: str, url: str, email: str | None = None, raw: bool = False, **kw):
    """One authorised request. Retries once on an expired token and once when Google is busy."""
    headers = kw.pop("headers", {})
    for attempt in range(3):
        _, tok = access_token(email, force=attempt > 0 and last == 401)
        with _http() as h:
            r = h.request(method, url, headers={"Authorization": f"Bearer {tok}", **headers}, **kw)
        last = r.status_code
        if r.status_code == 401 and attempt == 0:
            continue
        if r.status_code in (429, 500, 503) and attempt < 2:
            time.sleep(2 + 3 * attempt)
            continue
        break
    if r.status_code >= 400:
        raise GoogleError(_explain(r), 400 if r.status_code in (400, 403, 404) else 502)
    if raw:
        return r
    return r.json() if r.content and "json" in r.headers.get("content-type", "") else {}


# ---------------------------------------------------------------- Drive
def find_child(name: str, parent: str, mime: str, email=None) -> str:
    q = f"name = '{name.replace(chr(39), chr(92) + chr(39))}' and mimeType = '{mime}' and trashed = false and '{parent}' in parents"
    files = call("GET", DRIVE, email, params={"q": q, "fields": "files(id)", "spaces": "drive"}).get("files", [])
    return files[0]["id"] if files else ""


def create_folder(name: str, parent: str = "root", email=None) -> str:
    return call("POST", DRIVE, email, params={"fields": "id"}, json={"name": name, "mimeType": MIME_FOLDER, "parents": [parent]})["id"]


def ensure_folders(email=None) -> dict:
    """{"decks": id, "templates": id}: "A.F.R.A Decks" and "A.F.R.A Decks/Templates", made on first use."""
    a = account(email)
    known = a.get("folders") or {}
    if known.get("decks") and known.get("templates"):
        try:
            if not file_meta(known["decks"], a["email"]).get("trashed"):
                return known
        except GoogleError:
            pass
    decks = find_child(FOLDER, "root", MIME_FOLDER, a["email"]) or create_folder(FOLDER, "root", a["email"])
    tpl = find_child(TEMPLATES, decks, MIME_FOLDER, a["email"]) or create_folder(TEMPLATES, decks, a["email"])
    a = account(a["email"])  # re-read: the token may have been refreshed meanwhile
    a["folders"] = {"decks": decks, "templates": tpl}
    _write(_acct_file(a["email"]), a)
    return a["folders"]


FILE_FIELDS = "id,name,mimeType,webViewLink,trashed,modifiedTime"


def file_meta(file_id: str, email=None) -> dict:
    return call("GET", f"{DRIVE}/{file_id}", email, params={"fields": FILE_FIELDS})


def list_files(parent: str, email=None, mime: str = "") -> list[dict]:
    q = f"'{parent}' in parents and trashed = false" + (f" and mimeType = '{mime}'" if mime else "")
    return call("GET", DRIVE, email, params={"q": q, "fields": f"files({FILE_FIELDS})", "orderBy": "modifiedTime desc", "pageSize": 100}).get("files", [])


def copy_file(file_id: str, name: str, parent: str, email=None) -> dict:
    return call("POST", f"{DRIVE}/{file_id}/copy", email, params={"fields": FILE_FIELDS}, json={"name": name, "parents": [parent]})


def create_file(name: str, mime: str, parent: str, email=None) -> dict:
    return call("POST", DRIVE, email, params={"fields": FILE_FIELDS}, json={"name": name, "mimeType": mime, "parents": [parent]})


def multipart_body(meta: dict, data: bytes, mime: str, boundary: str) -> bytes:
    return (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode() + json.dumps(meta).encode()
            + f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n".encode() + data + f"\r\n--{boundary}--".encode())


def upload(data: bytes, name: str, mime: str, parent: str, convert_to: str = "", email=None) -> dict:
    """Upload a file; convert_to=MIME_SLIDES / MIME_SHEET turns .pptx / .xlsx / .csv into a Google file."""
    meta = {"name": name, "parents": [parent], **({"mimeType": convert_to} if convert_to else {})}
    if len(data) <= 5 * 1024 * 1024:
        b = "afra" + secrets.token_hex(8)
        return call("POST", UPLOAD, email, params={"uploadType": "multipart", "fields": FILE_FIELDS},
                    headers={"Content-Type": f"multipart/related; boundary={b}"}, content=multipart_body(meta, data, mime, b))
    r = call("POST", UPLOAD, email, raw=True, params={"uploadType": "resumable", "fields": FILE_FIELDS},
             headers={"X-Upload-Content-Type": mime, "X-Upload-Content-Length": str(len(data))}, json=meta)
    return call("PUT", r.headers["Location"], email, headers={"Content-Type": mime}, content=data)


def export(file_id: str, mime: str, email=None) -> bytes:
    return call("GET", f"{DRIVE}/{file_id}/export", email, raw=True, params={"mimeType": mime}).content


def trash(file_id: str, email=None):
    call("PATCH", f"{DRIVE}/{file_id}", email, json={"trashed": True})


# ---------------------------------------------------------------- Slides
def presentation(pid: str, email=None) -> dict:
    return call("GET", f"{SLIDES}/{pid}", email)


def batch_update(pid: str, requests: list[dict], email=None) -> list[dict]:
    replies = []
    for i in range(0, len(requests), 400):  # one deck build can be a few thousand small requests
        part = requests[i:i + 400]
        if part:
            replies += call("POST", f"{SLIDES}/{pid}:batchUpdate", email, json={"requests": part}).get("replies", [])
    return replies


def thumbnail(pid: str, page_id: str, email=None, size: str = "MEDIUM") -> bytes:
    j = call("GET", f"{SLIDES}/{pid}/pages/{page_id}/thumbnail", email,
             params={"thumbnailProperties.thumbnailSize": size, "thumbnailProperties.mimeType": "PNG"})
    with _http() as h:  # contentUrl is a short-lived public image link
        return h.get(j["contentUrl"]).content


# ---------------------------------------------------------------- Sheets
def parse_id(link: str) -> str:
    """The file id from a Google Docs/Sheets/Slides/Drive link, or the id itself."""
    link = (link or "").strip()
    m = re.search(r"/d/([a-zA-Z0-9_-]{20,})", link) or re.search(r"[?&]id=([a-zA-Z0-9_-]{20,})", link) or re.fullmatch(r"([a-zA-Z0-9_-]{20,})", link)
    if not m:
        raise ValueError("That doesn't look like a Google link. Copy it from the browser's address bar.")
    return m.group(1)


def sheet_meta(sid: str, email=None) -> dict:
    j = call("GET", f"{SHEETS}/{sid}", email, params={"fields": "properties.title,sheets(properties(sheetId,title,gridProperties),charts(chartId,spec(title)))"})
    return {"id": sid, "title": j.get("properties", {}).get("title", ""),
            "tabs": [{"title": s["properties"]["title"], "rows": s["properties"].get("gridProperties", {}).get("rowCount", 0),
                      "charts": [{"id": c["chartId"], "title": c.get("spec", {}).get("title", "") or f"Chart {c['chartId']}"} for c in s.get("charts", [])]}
                     for s in j.get("sheets", [])]}


def sheet_values(sid: str, rng: str = "", email=None) -> list[list]:
    rng = rng or "A1:Z200"
    return call("GET", f"{SHEETS}/{sid}/values/{quote(rng, safe='!:$')}", email).get("values", [])
