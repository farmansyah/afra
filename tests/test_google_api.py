"""Google sign-in and tokens, with Google replaced by httpx.MockTransport (no network)."""
import base64
import hashlib
import json
import shutil
import time
import unittest
from urllib.parse import parse_qs, urlparse

import httpx

from app import google_api as g

CLIENT = {"installed": {"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "s3cret", "project_id": "afra-test"}}


def id_token(email):
    part = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).rstrip(b"=").decode()
    return f"x.{part}.y"


class Google:
    """A tiny fake of Google's token, revoke and Drive endpoints."""

    def __init__(self):
        self.calls, self.refresh_ok, self.api_401 = [], True, 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        form = parse_qs(req.content.decode()) if req.method == "POST" and b"=" in req.content else {}
        self.calls.append((req.method, url.split("?")[0], {k: v[0] for k, v in form.items()}))
        if url.startswith(g.TOKEN):
            if form.get("grant_type") == ["authorization_code"]:
                return httpx.Response(200, json={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600,
                                                 "scope": " ".join(g.SCOPES), "id_token": id_token("ana@example.com")})
            if not self.refresh_ok:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
            return httpx.Response(200, json={"access_token": f"at-{len(self.calls)}", "expires_in": 3600})
        if url.startswith(g.REVOKE):
            return httpx.Response(200)
        if url.startswith(g.DRIVE):
            if self.api_401:
                self.api_401 -= 1
                return httpx.Response(401, json={"error": {"message": "Invalid Credentials"}})
            return httpx.Response(200, json={"files": [{"id": "f1"}]})
        return httpx.Response(404)


class GoogleApiTest(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(g.DIR, ignore_errors=True)
        (g.DIR.parent / "google_client.json").unlink(missing_ok=True)
        self.fake = Google()
        g.TRANSPORT = httpx.MockTransport(self.fake)

    def tearDown(self):
        g.TRANSPORT = None
        shutil.rmtree(g.DIR, ignore_errors=True)
        (g.DIR.parent / "google_client.json").unlink(missing_ok=True)

    def connect(self):
        g.save_client(json.dumps(CLIENT))
        return g.finish_connect("code-1", "verifier", "http://127.0.0.1:5000")

    def test_pkce_and_auth_url(self):
        verifier, challenge = g.pkce()
        self.assertGreaterEqual(len(verifier), 43)
        self.assertEqual(challenge, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode())
        q = parse_qs(urlparse(g.auth_url("cid", "http://127.0.0.1:5000", "st", challenge, "ana@example.com")).query)
        self.assertEqual(q["code_challenge_method"], ["S256"])
        self.assertEqual(q["access_type"], ["offline"])
        self.assertEqual(q["login_hint"], ["ana@example.com"])
        for scope in ("drive.file", "presentations", "spreadsheets"):
            self.assertIn(scope, q["scope"][0])

    def test_client_from_settings_or_file_and_never_shown(self):
        self.assertFalse(g.client_status()["configured"])
        with self.assertRaises(ValueError):
            g.save_client(json.dumps({"web": CLIENT["installed"]}))  # a Web client, not a Desktop one
        with self.assertRaises(ValueError):
            g.save_client("not json")
        (g.DIR.parent / "google_client.json").write_text(json.dumps(CLIENT), encoding="utf-8")  # the downloaded file dropped in
        st = g.client_status()
        self.assertTrue(st["configured"])
        self.assertNotIn("s3cret", json.dumps(st))
        self.assertEqual(st["project_id"], "afra-test")

    def test_connect_refresh_and_reconnect(self):
        self.assertEqual(self.connect(), "ana@example.com")
        self.assertEqual(g.accounts(), [{"email": "ana@example.com", "default": True, "needs_reconnect": False, "added": g.accounts()[0]["added"]}])
        sent = self.fake.calls[0][2]
        self.assertEqual(sent["code_verifier"], "verifier")
        self.assertEqual(g.access_token()[1], "at-1")  # still fresh: no call to Google
        n = len(self.fake.calls)
        a = g.account()
        a["expires"] = time.time() - 5
        g._write(g._acct_file(a["email"]), a)
        email, tok = g.access_token()  # silent refresh
        self.assertEqual(len(self.fake.calls), n + 1)
        self.assertNotEqual(tok, "at-1")
        self.assertEqual(self.fake.calls[-1][2]["grant_type"], "refresh_token")
        self.fake.refresh_ok = False  # Testing mode: the refresh token died after 7 days
        with self.assertRaises(g.GoogleError) as cm:
            g.access_token(force=True)
        self.assertEqual(cm.exception.status, 401)
        self.assertTrue(g.accounts()[0]["needs_reconnect"])
        self.fake.refresh_ok = True
        self.connect()  # Reconnect clears the flag
        self.assertFalse(g.accounts()[0]["needs_reconnect"])

    def test_call_retries_once_after_401(self):
        self.connect()
        self.fake.api_401 = 1
        self.assertEqual(g.call("GET", g.DRIVE, params={"q": "x"})["files"][0]["id"], "f1")
        self.assertEqual([c[1] for c in self.fake.calls[-3:]], [g.DRIVE, g.TOKEN, g.DRIVE])

    def test_offline_refresh_is_not_a_reconnect(self):
        self.connect()
        g.TRANSPORT = httpx.MockTransport(lambda req: (_ for _ in ()).throw(httpx.ConnectError("offline", request=req)))
        with self.assertRaises(g.GoogleError) as cm:
            g.access_token(force=True)
        self.assertNotEqual(cm.exception.status, 401)
        self.assertFalse(g.accounts()[0]["needs_reconnect"])

    def test_several_accounts_default_and_disconnect(self):
        self.connect()
        b = {**g.account(), "email": "budi@example.com"}
        g._write(g._acct_file("budi@example.com"), b)
        self.assertEqual([a["email"] for a in g.accounts()], ["ana@example.com", "budi@example.com"])
        g.set_default("budi@example.com")
        self.assertEqual(g.default_email(), "budi@example.com")
        g.disconnect("budi@example.com")
        self.assertIn((("POST", g.REVOKE)), [(c[0], c[1]) for c in self.fake.calls])
        self.assertEqual([(a["email"], a["default"]) for a in g.accounts()], [("ana@example.com", True)])
        with self.assertRaises(ValueError):
            g.set_default("nobody@example.com")

    def test_parse_id(self):
        pid = "1AbC_d-9xxxxxxxxxxxxxxxxxxxx"
        self.assertEqual(g.parse_id(f"https://docs.google.com/presentation/d/{pid}/edit#slide=id.p"), pid)
        self.assertEqual(g.parse_id(f"https://docs.google.com/spreadsheets/d/{pid}/edit?gid=0"), pid)
        with self.assertRaises(ValueError):
            g.parse_id("https://example.com/not-google")


if __name__ == "__main__":
    unittest.main()
