"""GreatPPT through the user's own signed-in browser window, offline: a scripted fake site behind a fake DevTools tab,
a mocked file server, mocked process start. Nothing real is opened, signed in to or bought."""
import contextlib
import io
import re
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import httpx

from app import cdp, designs, greatppt as g, handoff, template_import as ti
from tests.test_designs_folder import deck
from tests.test_greatppt import GP, GreatpptTestCase

CDN = "https://cdn.example-files.test/padel.pptx"
DL = g.HOME + "/?edd_action=download&payment_key=abc&file=0"
UA = "Mozilla/5.0 TestBrowser/1.0"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += max(s, 0.5)


class Steps:
    """A progress callback with the user-wait hook the import box gives it."""

    def __init__(self):
        self.steps, self.waits = [], []

    def __call__(self, text):
        self.steps.append(text)

    def user_wait(self, text=""):
        self.waits.append(text)


class FakeSite:
    """GreatPPT as far as the import can see it: template page -> checkout -> confirmation, with optional checks."""

    def __init__(self, **o):
        self.o = o
        self.logged_in = o.get("logged_in", True)
        self.url = "about:blank"
        self.gate = o.get("gate", "")           # "", "challenge" or "login": shown for the first visit to the template page
        self.gate_left = o.get("gate_polls", 3)  # state() calls the gate stays up (None = forever)
        self.gate_done = not self.gate
        self.terms_left = 2 if o.get("terms") else 0
        self.evals, self.calls, self.fronted, self.purchased, self.downloads_to = [], [], 0, False, None

    def path(self):
        return re.sub(r"^https?://[^/]+", "", self.url).split("?")[0]

    def goto(self, url):
        self.url = url
        if self.gate and not self.gate_done and "/downloads/" in url:
            self.shown_gate = True

    def state(self):
        shown = self.gate and not self.gate_done and "/downloads/" in self.url
        if shown:
            if self.o.get("gate_forever"):
                pass
            elif self.gate_left > 0:
                self.gate_left -= 1
            else:
                self.gate_done = True
                if self.gate == "login":
                    self.logged_in, self.url = True, g.HOME + "/account/"  # WordPress sends a new sign-in to the account page
                shown = False
        if shown and self.gate == "challenge":
            return {"url": self.url, "title": "Just a moment...", "text": "Checking if the site connection is secure", "ready": "complete",
                    "logged_in": False, "challenge": True, "login_form": False}
        if shown and self.gate == "login":
            return {"url": g.LOGIN_URL, "title": "Log in", "text": "", "ready": "complete", "logged_in": False, "challenge": False, "login_form": True}
        return {"url": self.url, "title": "GreatPPT", "text": "Free template", "ready": "complete", "logged_in": self.logged_in,
                "challenge": False, "login_form": False}

    def eval(self, js):
        self.evals.append(js)
        if js == g.STATE_JS:
            return self.state()
        if js == g.FORM_JS:
            return {"form": True, "price": self.o.get("price", "0.00"), "id": "21674"}
        if js == g.CLICK_DOWNLOAD_JS:
            if self.o.get("no_redirect"):
                return True
            self.url = g.HOME + "/checkout/"
            return True
        if js == g.SUBMIT_FORM_JS:
            self.url = g.HOME + "/checkout/"
            return True
        if js == g.CHECKOUT_JS:
            pending = self.terms_left > 0
            self.terms_left = max(0, self.terms_left - 1)
            return {"url": self.url, "total": self.o.get("total", "$0.00"), "items": 1, "card_fields": self.o.get("card", False),
                    "terms_pending": pending, "button": True}
        if js == g.CLICK_PURCHASE_JS:
            self.purchased = True
            self.url = g.HOME + "/purchase-confirmation/?payment_key=abc"
            return True
        if js == g.LINKS_JS:
            if "/purchase-confirmation" in self.url or self.path() in ("/account/", "/purchase-history/"):
                links = [{"href": g.HOME + "/checkout/", "text": "Checkout", "cls": ""}, {"href": g.HOME + "/?action=logout", "text": "Log out", "cls": ""},
                         {"href": g.HOME + "/downloads/other-powerpoint-template/", "text": "Other", "cls": ""}]
                if not self.o.get("no_links"):
                    links.append({"href": DL, "text": "Padel PowerPoint Template", "cls": "edd_download_file_link"})
                return links
            return []
        if js == "navigator.userAgent":
            return UA
        raise AssertionError("unexpected script: " + js[:80])

    def tab_call(self, method, params=None):
        self.calls.append(method)
        if method == "Page.navigate":
            self.goto(params["url"])
            if self.downloads_to and params["url"] == DL:
                (Path(self.downloads_to) / "padel.pptx").write_bytes(self.o["browser_file"])
        elif method == "Page.bringToFront":
            self.fronted += 1
        elif method == "Network.getCookies":
            return {"cookies": [{"name": "wordpress_logged_in_x", "value": "S3CR3T"}, {"name": "cf_clearance", "value": "cf"}]}
        elif method == "Browser.setDownloadBehavior":
            self.downloads_to = params["downloadPath"]
        elif method == "Browser.getWindowForTarget":
            return {"windowId": 7}
        return {}


class FakeTab:
    def __init__(self, site):
        self.site = site

    def call(self, method, params=None):
        return self.site.tab_call(method, params)

    def eval(self, js):
        return self.site.eval(js)

    def close(self):
        pass


class AccountTestCase(GreatpptTestCase):
    def setUp(self):
        super().setUp()
        self.clock = Clock()
        self.site = None
        for p in (mock.patch.object(g, "_now", self.clock.now), mock.patch.object(g, "_sleep", self.clock.sleep),
                  mock.patch.object(cdp, "new_page", side_effect=lambda ep, url="about:blank": {"id": "T1", "webSocketDebuggerUrl": "ws://127.0.0.1:9/x"}),
                  mock.patch.object(cdp, "Tab", side_effect=lambda *a, **k: FakeTab(self.site)),
                  mock.patch.object(cdp, "browser_tab", side_effect=lambda ep: FakeTab(self.site)),
                  mock.patch.object(g.httpx, "get", return_value=httpx.Response(200)),
                  mock.patch.object(g, "open_session", self.open_session)):
            p.start()
            self.addCleanup(p.stop)
        self.files_seen = []

    @contextlib.contextmanager
    def open_session(self, step=lambda s: None):
        s = g.Session("http://127.0.0.1:9")  # the real Session class on the fake DevTools tab
        try:
            yield s
        finally:
            s.close()

    def serve_files(self, pptx=None, page="greatppt_template.html"):
        data = deck("Padel") if pptx is None else pptx
        pagetxt = (Path(__file__).parent / "fixtures" / page).read_text(encoding="utf-8")

        def handler(req):
            u = str(req.url)
            self.files_seen.append((req.method, u, dict(req.headers), req.content))
            if u == GP:
                return httpx.Response(200, text=pagetxt, headers={"content-type": "text/html"})
            if "/wp-content/uploads/edd/" in u:
                from tests.test_designs_folder import png
                return httpx.Response(200, content=png(32, 18), headers={"content-type": "image/png"})
            if u == DL:
                return httpx.Response(302, headers={"location": CDN})
            if u == CDN:
                return httpx.Response(200, content=data, headers={"content-type": "application/vnd.openxmlformats-officedocument.presentationml.presentation"})
            return httpx.Response(404)
        ti.TRANSPORT = httpx.MockTransport(handler)

    def connect_state(self):
        g.write_state(connected=True, port=9, pid=1)

    def run_exact(self, **site_opts):
        self.site = FakeSite(**site_opts)
        steps = Steps()
        info = {"title": "Padel", "kind": "greatppt", "free": True, "premium": False}
        try:
            return g.download_exact(GP, info, steps), steps
        except Exception as e:  # noqa: BLE001  the tests look at what was raised
            return e, steps


class ExactFileTest(AccountTestCase):
    def test_success_saves_an_exact_copy_with_the_greatppt_credit(self):
        data = deck("Padel original")
        self.settings(ai_mode="claude")
        self.serve_files(data)
        res, steps = self.run_exact()
        self.assertEqual(res["status"], "saved")
        f = self.designs_dir / "Padel.pptx"
        self.assertEqual(f.read_bytes(), data)  # the very bytes GreatPPT served
        side = designs.sidecar(f)
        self.assertEqual(side["attribution"], "Template by GreatPPT (free licence).")
        self.assertEqual((side["site"], side["method"], side["source_url"]), ("GreatPPT", "download", GP))
        self.assertTrue(self.site.purchased)
        self.assertIn("Pressing Download…", steps.steps)
        self.assertEqual(steps.waits, [])

    def test_the_session_cookies_go_to_greatppt_only_and_nothing_is_posted(self):
        self.settings(ai_mode="claude")
        self.serve_files()
        self.run_exact()
        by = {u: (m, h, body) for m, u, h, body in self.files_seen}
        self.assertEqual(by[DL][1].get("cookie"), "wordpress_logged_in_x=S3CR3T; cf_clearance=cf")
        self.assertEqual(by[DL][1].get("user-agent"), UA)
        self.assertNotIn("cookie", by[CDN][1])  # the redirect target never sees them
        for m, u, h, body in self.files_seen:
            self.assertEqual((m, body), ("GET", b""), u)
            self.assertNotIn("authorization", h)
        self.assertFalse(list((self.data / "browser").glob("*cookie*")))

    def test_a_zip_with_the_pptx_inside_is_unpacked(self):
        data = deck("Inside")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("readme.txt", "hi")
            z.writestr("Padel/Padel.pptx", data)
        self.serve_files(buf.getvalue())
        res, _ = self.run_exact()
        self.assertEqual(res["status"], "saved")
        self.assertEqual((self.designs_dir / "Padel.pptx").read_bytes(), data)

    def test_a_file_that_is_not_a_powerpoint_is_not_kept(self):
        self.serve_files(b"<html>nope</html>")
        with mock.patch.object(g.Session, "download", return_value=None):  # the browser fallback finds nothing either
            res, _ = self.run_exact()
        self.assertIsInstance(res, ti.Stop)
        self.assertEqual(self.files(), [])

    def test_the_browser_downloads_it_when_a_plain_request_is_not_accepted(self):
        data = deck("Via browser")
        self.serve_files(b"<html>blocked</html>")
        res, _ = self.run_exact(browser_file=data)
        self.assertEqual(res["status"], "saved")
        self.assertEqual((self.designs_dir / "Padel.pptx").read_bytes(), data)
        self.assertIn("Browser.setDownloadBehavior", self.site.calls)

    def test_no_download_link_after_the_order_says_so(self):
        self.serve_files()
        res, _ = self.run_exact(no_links=True)
        self.assertIsInstance(res, ti.Stop)
        self.assertIn("Downloads watcher", res.message)

    def test_pick_download_links(self):
        links = [{"href": "https://www.greatppt.com/checkout/", "text": "", "cls": ""}, {"href": "https://www.greatppt.com/?action=logout", "text": "", "cls": ""},
                 {"href": "https://www.greatppt.com/downloads/x/", "text": "", "cls": ""}, {"href": "http://127.0.0.1/a.pptx", "text": "", "cls": ""},
                 {"href": "https://www.greatppt.com/?edd_action=download&k=1", "text": "", "cls": ""},
                 {"href": "/wp-content/uploads/edd/2026/01/Pack.zip", "text": "", "cls": ""}, {"href": "https://www.greatppt.com/a", "text": "", "cls": "edd_download_file_link"}]
        self.assertEqual(g.pick_download_links(links, g.HOME + "/purchase-confirmation/"),
                         ["https://www.greatppt.com/?edd_action=download&k=1", "https://www.greatppt.com/wp-content/uploads/edd/2026/01/Pack.zip", "https://www.greatppt.com/a"])


class MoneyStopsTest(AccountTestCase):
    def assertNothingBought(self, res):
        self.assertIsInstance(res, ti.Stop)
        self.assertEqual(res.status, "paid")
        self.assertFalse(self.site.purchased)
        self.assertNotIn(g.CLICK_PURCHASE_JS, self.site.evals)
        self.assertEqual(self.files(), [])

    def test_a_cart_total_that_is_not_zero_stops_before_buying(self):
        self.serve_files()
        res, _ = self.run_exact(total="$9.00")
        self.assertNothingBought(res)
        self.assertIn("Nothing was bought", res.message)

    def test_payment_details_asked_for_stop_it(self):
        self.serve_files()
        res, _ = self.run_exact(card=True)
        self.assertNothingBought(res)

    def test_an_unreadable_total_stops_it(self):
        self.serve_files()
        res, _ = self.run_exact(total=None)
        self.assertNothingBought(res)

    def test_a_price_on_the_download_form_stops_before_pressing_it(self):
        self.serve_files()
        res, _ = self.run_exact(price="19.00")
        self.assertIsInstance(res, ti.Stop)
        self.assertEqual(res.status, "paid")
        self.assertNotIn(g.CLICK_DOWNLOAD_JS, self.site.evals)

    def test_money_parsing(self):
        for v, want in (("$0.00", 0.0), ("Free", 0.0), ("0", 0.0), ("Rp 0", 0.0), ("$9.00", 9.0), ("1,299.50", 1299.5), ("12,5", 12.5), ("", None), (None, None), ("n/a", None)):
            self.assertEqual(g.money(v), want, v)

    def test_through_the_import_a_paid_cart_ends_the_import_without_a_rebuild(self):
        self.settings(ai_mode="claude")
        self.serve_files()
        self.connect_state()
        self.site = FakeSite(total="$9.00")
        r = ti.import_link(GP)
        self.assertEqual(r["status"], "paid")
        self.assertEqual(handoff.TASKS, {})
        self.assertEqual(self.files(), [])


class ChecksTest(AccountTestCase):
    def test_gate_detection(self):
        self.assertEqual(g.gate({"url": g.HOME + "/x", "title": "Just a moment...", "text": ""}), "challenge")
        self.assertEqual(g.gate({"url": g.HOME + "/x", "title": "Attention Required! | Cloudflare", "text": ""}), "challenge")
        self.assertEqual(g.gate({"url": g.HOME + "/x", "title": "GreatPPT", "text": "", "challenge": True}), "challenge")
        self.assertEqual(g.gate({"url": g.HOME + "/cdn-cgi/challenge-platform/h/b", "title": "", "text": ""}), "challenge")
        self.assertEqual(g.gate({"url": g.LOGIN_URL, "title": "Log in", "text": ""}), "login")
        self.assertEqual(g.gate({"url": g.HOME + "/x", "title": "x", "text": "", "login_form": True, "logged_in": False}), "login")
        self.assertEqual(g.gate({"url": g.HOME + "/x", "title": "x", "text": "", "login_form": True, "logged_in": True}), "")
        self.assertEqual(g.gate({"url": GP, "title": "Free Padel PowerPoint Template", "text": "Free template. Download", "logged_in": True}), "")

    def test_a_check_brings_the_window_forward_and_the_import_waits_for_the_user(self):
        self.serve_files()
        res, steps = self.run_exact(gate="challenge", gate_polls=4)
        self.assertEqual(res["status"], "saved")  # continued once the page was past the check
        self.assertEqual(steps.waits[0], "Waiting for you in the GreatPPT window")
        self.assertEqual(steps.waits[-1], "")
        self.assertGreaterEqual(self.site.fronted, 1)
        # while the check was up nothing was pressed
        first_click = self.site.evals.index(g.CLICK_DOWNLOAD_JS)
        self.assertGreaterEqual(self.site.evals[:first_click].count(g.STATE_JS), 4)

    def test_a_check_that_never_clears_times_out_with_a_clear_message_and_solves_nothing(self):
        self.serve_files()
        res, steps = self.run_exact(gate="challenge", gate_forever=True)
        self.assertIsInstance(res, ti.Stop)
        self.assertEqual(res.status, "waiting")
        self.assertIn("waiting for you", res.message)
        self.assertGreaterEqual(self.clock.t - 1000, g.GATE_TIMEOUT)
        self.assertNotIn(g.CLICK_DOWNLOAD_JS, self.site.evals)
        self.assertEqual(steps.waits[-1], "")
        self.assertEqual(self.files(), [])

    def test_sign_in_page_waits_for_the_user_then_goes_back_to_the_template(self):
        self.serve_files()
        res, steps = self.run_exact(gate="login", gate_polls=3)
        self.assertEqual(res["status"], "saved")
        self.assertEqual(steps.waits[0], "Waiting for you in the GreatPPT window")
        self.assertGreaterEqual(self.site.fronted, 1)

    def test_not_signed_in_any_more_is_not_connected(self):
        self.serve_files()
        res, _ = self.run_exact(logged_in=False)
        self.assertIsInstance(res, g.NotConnected)

    def test_agreeing_to_terms_is_left_to_the_user(self):
        self.serve_files()
        res, steps = self.run_exact(terms=True)
        self.assertEqual(res["status"], "saved")
        self.assertTrue(steps.waits[0].startswith("Waiting for you in the GreatPPT window"))
        self.assertGreaterEqual(self.site.fronted, 1)

    def test_a_download_form_that_does_not_redirect_is_submitted_plainly_once(self):
        self.serve_files()
        self.site = FakeSite(no_redirect=True)
        steps = Steps()
        res = g.download_exact(GP, {"title": "Padel", "kind": "greatppt", "free": True}, steps)
        self.assertEqual(res["status"], "saved")
        self.assertEqual(self.site.evals.count(g.SUBMIT_FORM_JS), 1)

    def test_step_marks_a_job_item_as_waiting_for_the_user(self):
        it = {"state": "working", "step": ""}
        st = ti.Step(it)
        st("Opening the GreatPPT page…")
        self.assertEqual((it["state"], it["step"]), ("working", "Opening the GreatPPT page…"))
        st.user_wait("Waiting for you in the GreatPPT window")
        self.assertEqual((it["state"], it["step"]), ("user", "Waiting for you in the GreatPPT window"))
        st.user_wait("")
        self.assertEqual(it["state"], "working")


class OrderOfWaysTest(AccountTestCase):
    def test_connected_gets_the_exact_file_and_never_the_rebuild(self):
        data = deck("Exact")
        self.settings(ai_mode="claude")
        self.serve_files(data)
        self.connect_state()
        self.site = FakeSite()
        r = ti.import_link(GP)
        self.assertEqual(r["status"], "saved")
        self.assertEqual((self.designs_dir / "Padel.pptx").read_bytes(), data)
        self.assertEqual(handoff.TASKS, {})
        self.assertFalse([u for _, u, _, _ in self.files_seen if "/wp-content/uploads/edd/" in u])  # no preview pictures fetched

    def test_not_connected_rebuilds_with_the_hint_and_opens_no_browser(self):
        self.settings(ai_mode="claude")
        self.serve_files()
        with mock.patch.object(g, "open_session", side_effect=AssertionError("no browser expected")):
            r = ti.import_link(GP)
        self.assertEqual((r["status"], r["hint"]), ("claude", g.HINT))

    def test_a_ended_sign_in_falls_back_to_the_rebuild_and_forgets_it(self):
        self.settings(ai_mode="claude")
        self.serve_files()
        self.connect_state()
        self.site = FakeSite(logged_in=False)
        r = ti.import_link(GP)
        self.assertEqual((r["status"], r["hint"]), ("claude", g.HINT_RECONNECT))
        self.assertFalse(g.read_state().get("connected"))

    def test_a_waiting_timeout_ends_the_link_with_its_own_message(self):
        self.settings(ai_mode="claude")
        self.serve_files()
        self.connect_state()
        self.site = FakeSite(gate="challenge", gate_forever=True)
        r = ti.import_link(GP)
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(handoff.TASKS, {})

    def test_job_item_shows_the_wait_state_while_a_check_is_up(self):
        self.settings(ai_mode="claude")
        self.serve_files()
        self.connect_state()
        self.site = FakeSite(gate="challenge", gate_forever=True)
        seen = []
        orig = g.Session.front

        def front(sess):
            seen.append(dict(item))
            return orig(sess)
        item = {"url": GP, "state": "working", "step": "", "status": "", "message": "", "name": ""}
        with mock.patch.object(g.Session, "front", front):
            res = ti.run_link(GP, ti.Step(item))
        self.assertEqual(seen[0]["state"], "user")
        self.assertEqual(seen[0]["step"], "Waiting for you in the GreatPPT window")
        self.assertEqual(res["status"], "waiting")


class ConnectionTest(AccountTestCase):
    def test_connect_opens_a_visible_window_at_the_login_page_on_its_own_profile(self):
        started = []
        proc = mock.Mock(pid=4242)
        proc.poll.return_value = None
        alive = iter([True])
        with mock.patch.object(ti, "find_browsers", return_value=["C:/Edge/msedge.exe"]), \
                mock.patch.object(g.subprocess, "Popen", side_effect=lambda args, **k: (started.append(args), proc)[1]), \
                mock.patch.object(cdp, "alive", side_effect=lambda ep: next(alive, True)), \
                mock.patch.object(ti, "free_port", return_value=51234), \
                mock.patch.object(g, "_logged_in", return_value=None):
            st = g.connect()
        args = started[0]
        self.assertEqual(args[0], "C:/Edge/msedge.exe")
        self.assertEqual(args[-1], g.LOGIN_URL)
        self.assertIn("--remote-debugging-port=51234", args)
        self.assertIn("--remote-debugging-address=127.0.0.1", args)
        self.assertIn(f"--user-data-dir={self.data / 'browser' / 'greatppt'}", args)
        self.assertFalse([a for a in args if "headless" in a or "minimized" in a])  # a normal, visible window
        self.assertEqual(g.read_state()["port"], 51234)
        self.assertFalse(st["connected"])

    def test_a_minimised_window_for_the_import_itself(self):
        started = []
        proc = mock.Mock(pid=1)
        proc.poll.return_value = None
        with mock.patch.object(ti, "find_browsers", return_value=["x"]), mock.patch.object(g.subprocess, "Popen", side_effect=lambda a, **k: (started.append(a), proc)[1]), \
                mock.patch.object(cdp, "alive", side_effect=[False, True]), mock.patch.object(ti, "free_port", return_value=5):
            g.ensure_window(g.HOME + "/", minimized=True)
        self.assertIn("--start-minimized", started[0])
        self.assertFalse([a for a in started[0] if "headless" in a])

    def test_no_browser_says_what_is_missing(self):
        with mock.patch.object(ti, "find_browsers", return_value=[]), mock.patch.object(cdp, "alive", return_value=False):
            with self.assertRaises(ti.Stop) as c:
                g.ensure_window()
        self.assertEqual(c.exception.status, "missing")

    def test_connected_is_shown_once_a_greatppt_page_is_seen_signed_in(self):
        (self.data / "browser" / "greatppt").mkdir(parents=True)
        g.write_state(port=9, pid=1)
        with mock.patch.object(cdp, "alive", return_value=True), mock.patch.object(g, "_logged_in", return_value=False):
            self.assertFalse(g.status()["connected"])
        with mock.patch.object(cdp, "alive", return_value=True), mock.patch.object(g, "_logged_in", return_value=True):
            st = g.status()
        self.assertEqual((st["connected"], st["open"]), (True, True))
        with mock.patch.object(cdp, "alive", return_value=False):  # window closed: the last seen answer stays
            self.assertTrue(g.status()["connected"])
        with mock.patch.object(cdp, "alive", return_value=True), mock.patch.object(g, "_logged_in", return_value=False):  # signed out in the window
            self.assertFalse(g.status()["connected"])

    def test_nothing_secret_is_kept_in_the_state_file(self):
        self.connect_state()
        self.assertEqual(set(g.read_state()), {"connected", "port", "pid"})

    def test_disconnect_deletes_the_profile_and_the_state(self):
        prof = self.data / "browser" / "greatppt"
        (prof / "Default").mkdir(parents=True)
        (prof / "Default" / "Cookies").write_bytes(b"cookie jar")
        self.connect_state()
        with mock.patch.object(cdp, "alive", return_value=False), mock.patch.object(g.subprocess, "run") as run:
            st = g.disconnect()
        self.assertFalse(prof.exists())
        self.assertFalse(g.state_file().exists())
        self.assertEqual(st["connected"], False)
        run.assert_not_called()  # nothing to kill: the window was already closed

    def test_disconnect_closes_the_window_it_started_by_its_own_process_id_only(self):
        prof = self.data / "browser" / "greatppt"
        prof.mkdir(parents=True)
        g.write_state(connected=True, port=9, pid=4242)
        calls = []
        tab = mock.Mock()
        with mock.patch.object(cdp, "alive", return_value=True), mock.patch.object(cdp, "browser_tab", return_value=tab), \
                mock.patch.object(g.time, "sleep"), mock.patch.object(g.subprocess, "run", side_effect=lambda a, **k: calls.append(a)):
            g.disconnect()
        tab.call.assert_called_with("Browser.close")
        if calls:
            self.assertEqual(calls[0][:3], ["taskkill", "/PID", "4242"])
            self.assertNotIn("/IM", calls[0])
        self.assertFalse(prof.exists())


class NoCredentialsTest(AccountTestCase):
    SRC = [Path(g.__file__), Path(cdp.__file__)]

    def test_no_script_or_devtools_call_types_into_a_page(self):
        self.serve_files()
        res, _ = self.run_exact(gate="login", gate_polls=2)
        self.assertEqual(res["status"], "saved")
        for js in self.site.evals:
            self.assertNotRegex(js, r"\.value\s*=[^=]", js[:60])
            for bad in (".focus(", "dispatchEvent", "setAttribute('value'", "insertText", "execCommand", "type=password", "type=\"password\""):
                self.assertNotIn(bad, js)
        allowed = {"Page.navigate", "Page.bringToFront", "Network.getCookies", "Browser.getWindowForTarget", "Browser.setWindowBounds", "Browser.setDownloadBehavior"}
        self.assertTrue(set(self.site.calls) <= allowed, set(self.site.calls) - allowed)

    def test_the_code_has_no_way_to_type_or_to_hold_a_password(self):
        for p in self.SRC:
            text = p.read_text(encoding="utf-8")
            for bad in ("Input.insertText", "Input.dispatchKeyEvent", "Input.dispatchMouseEvent", "Input.emulate", "send_keys", "getpass", "keyring"):
                self.assertNotIn(bad, text, f"{bad} in {p.name}")
            self.assertNotRegex(text, r"(?i)[\"']password[\"']\s*[:=]", p.name)   # no password field or setting
            self.assertNotRegex(text, r"(?i)\bpassword\s*=", p.name)
        from app.config import DEFAULTS
        self.assertFalse([k for k in DEFAULTS if "greatppt" in k and ("pass" in k or "pwd" in k or "token" in k)])

    def test_nothing_is_ever_posted_to_greatppt(self):
        for p in self.SRC:
            text = p.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"\.post\(|\.put\(\s*[^e]|method=[\"']POST|data=|json=|files=", p.name)

    def test_settings_never_carry_greatppt_secrets(self):
        self.connect_state()
        import json
        st = json.dumps(g.read_state())
        for bad in ("password", "cookie", "token", "S3CR3T"):
            self.assertNotIn(bad, st.lower())


if __name__ == "__main__":
    unittest.main()
