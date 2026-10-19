"""Templates from links, offline: saved HTML pages and mocked sites (no network, no browser, REA and the model mocked)."""
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import httpx

from app import designs, pptx_build, template_import as ti, tugas
from tests.test_designs_folder import deck, png

FIX = Path(__file__).parent / "fixtures"
page = lambda n: (FIX / n).read_text(encoding="utf-8")  # noqa: E731
HTML = {"content-type": "text/html; charset=utf-8"}
PPTX = {"content-type": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}


class PageTest(unittest.TestCase):
    def test_classify(self):
        c = ti.classify
        self.assertEqual(c("https://slidesgo.com/theme/abstract-art"), "slidesgo")
        self.assertEqual(c("https://www.slidescarnival.com/x/"), "slidescarnival")
        self.assertEqual(c("https://create.microsoft.com/en-us/templates/presentations"), "microsoft")
        self.assertEqual(c("https://powerpoint.cloud.microsoft/create/en/presentation-templates/"), "microsoft")
        self.assertEqual(c("https://docs.google.com/presentation/d/1AbC/edit"), "gslides")
        self.assertEqual(c("https://www.canva.com/templates/EAF/"), "canva")
        self.assertEqual(c("https://files.example.test/a/Deck.pptx?x=1"), "file")
        self.assertEqual(c("https://example.test/templates/1"), "other")

    def test_zip_magic_and_real_powerpoint_check(self):
        self.assertTrue(ti.is_zip_magic(b"PK\x03\x04rest"))
        self.assertFalse(ti.is_zip_magic(b"<!doctype html>"))
        self.assertTrue(ti.is_pptx(deck()))
        import io
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("a.txt", "x")
        self.assertFalse(ti.is_pptx(buf.getvalue()))  # a zip, but no presentation inside
        self.assertFalse(ti.is_pptx(b"PK\x03\x04 broken"))

    def test_only_public_hosts_are_followed(self):
        for ok in ("https://www.slidescarnival.com/download/x/y/pptx", "http://cdn.example.test/a.pptx"):
            self.assertTrue(ti.public_host(ok))
        for bad in ("http://localhost:8765/a.pptx", "http://127.0.0.1/a.pptx", "http://192.168.1.4/a.pptx", "http://10.0.0.2/a", "file:///c:/a.pptx",
                    "http://nas.local/a.pptx", "http://[::1]/a"):
            self.assertFalse(ti.public_host(bad), bad)

    def test_slidesgo_free_and_premium(self):
        free = ti.page_info(page("slidesgo_free.html"), "https://slidesgo.com/theme/abstract-art")
        self.assertEqual((free["kind"], free["title"], free["premium"], free["free"]), ("slidesgo", "Abstract Art", False, True))
        self.assertEqual(free["links"], ["https://slidesgo.com/theme/abstract-art/download"])  # only the page's own download address
        self.assertTrue(free["credit_mentioned"])
        paid = ti.page_info(page("slidesgo_premium.html"), "https://slidesgo.com/theme/brazilian-halloween")
        self.assertEqual((paid["premium"], paid["free"]), (True, False))

    def test_the_file_hidden_in_a_page_json_counts_as_a_candidate(self):
        html = ('<script>{"theme":{"file":"https:\\/\\/storage.example-slides.test\\/sg\\/x.pptx"},"other":"free"}</script>'
                '<a HREF="https://files.example.test/Big.PPTX?a=1">get it</a>')
        c = ti.candidates(html, "https://slidesgo.com/theme/abstract-art")
        self.assertEqual(c, ["https://files.example.test/Big.PPTX?a=1", "https://storage.example-slides.test/sg/x.pptx"])

    def test_slidescarnival_picks_its_own_pptx_not_the_related_ones(self):
        i = ti.page_info(page("slidescarnival_free.html"), "https://www.slidescarnival.com/minimalist-thesis-defense/")
        self.assertEqual(i["links"], ["https://www.slidescarnival.com/download/sc00332/minimalist-thesis-defense/pptx"])
        self.assertEqual((i["title"], i["free"], i["premium"]), ("Simple Minimalist Thesis Defense", True, False))  # a "Premium" tag in the menu is not a premium template

    def test_premium_wording_on_an_unknown_site(self):
        i = ti.page_info(page("paid_text.html"), "https://decks.example.test/neon")
        self.assertTrue(i["premium"])
        self.assertFalse(i["free"])

    def test_listing_page_gives_no_file(self):
        i = ti.page_info(page("microsoft_listing.html"), "https://create.microsoft.com/en-us/templates/presentations")
        self.assertEqual(i["links"], [])
        self.assertTrue(i["listing"])
        self.assertEqual(ti.candidates(page("microsoft_listing.html"), "https://x.test/")[0], "https://cdn.example-ms.test/a/t1.pptx")  # viewer link unwrapped

    def test_preview_only_page_that_says_free(self):
        i = ti.page_info(page("preview_only_free.html"), "https://decks.example.test/pastel")
        self.assertEqual((i["links"], i["free"], i["premium"]), ([], True, False))


class ImportTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.designs_dir = self.tmp / "Designs"
        settings = {"tugas_dir": str(self.tmp / "Tugas"), "designs_dir": str(self.designs_dir)}
        for p in (mock.patch.object(tugas, "get_settings", return_value=settings), mock.patch.object(designs, "get_settings", return_value=settings),
                  mock.patch.object(designs, "THUMBS", self.tmp / "thumbs"), mock.patch.object(designs, "FILE", self.tmp / "designs.json"),
                  mock.patch.object(designs, "DIR", self.tmp / "store"), mock.patch.object(ti, "get_settings", return_value=settings)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, ti, "TRANSPORT", None)
        self.seen = []

    def site(self, routes):
        def handler(req):
            self.seen.append((req.method, str(req.url)))
            r = routes.get(str(req.url))
            return r(req) if callable(r) else r or httpx.Response(404)
        ti.TRANSPORT = httpx.MockTransport(handler)

    def files(self):
        return sorted(p.name for p in self.designs_dir.glob("*")) if self.designs_dir.exists() else []


class DirectDownloadTest(ImportTestCase):
    SC = "https://www.slidescarnival.com/minimalist-thesis-defense/"
    SC_PPTX = "https://www.slidescarnival.com/download/sc00332/minimalist-thesis-defense/pptx"

    def test_free_template_is_downloaded_with_its_credit(self):
        data = deck("Thesis")
        self.site({self.SC: httpx.Response(200, text=page("slidescarnival_free.html"), headers=HTML),
                   self.SC_PPTX: httpx.Response(301, headers={"location": "https://storage.example.test/sc/thesis.pptx"}),
                   "https://storage.example.test/sc/thesis.pptx": httpx.Response(200, content=data, headers=PPTX)})
        r = ti.import_link(self.SC)
        self.assertEqual((r["status"], r["name"]), ("saved", "Simple Minimalist Thesis Defense"))
        self.assertEqual((self.designs_dir / "Simple Minimalist Thesis Defense.pptx").read_bytes(), data)  # an exact copy
        side = json.loads((self.designs_dir / "Simple Minimalist Thesis Defense.pptx.source.json").read_text(encoding="utf-8"))
        self.assertEqual(side["source_url"], self.SC)
        self.assertIn("SlidesCarnival", side["attribution"])
        self.assertIn("credits slide", side["attribution"])
        self.assertEqual([x["name"] for x in designs.all_designs() if x["source"] == "Designs folder"], ["Simple Minimalist Thesis Defense"])  # sidecar is not a design
        again = ti.import_link(self.SC)  # the same link twice never overwrites
        self.assertEqual(again["status"], "saved")
        self.assertEqual(len(list(self.designs_dir.glob("*.pptx"))), 2)

    def test_slidesgo_free_never_goes_to_the_sign_in_download(self):
        url = "https://slidesgo.com/theme/abstract-art"
        self.site({url: httpx.Response(200, text=page("slidesgo_free.html"), headers=HTML),
                   url + "/download": httpx.Response(302, headers={"location": "https://slidesgo.com/auth/login"}),
                   "https://slidesgo.com/auth/login": httpx.Response(200, text="<form>Sign in</form>", headers=HTML)})
        with mock.patch("app.slidesgo.rea_image_urls", return_value=[]):
            r = ti.import_link(url)
        self.assertEqual(r["status"], "no_file")  # free, but this page lists no preview pictures to rebuild from
        self.assertEqual(self.files(), [])
        self.assertEqual(self.seen, [("GET", url)])  # looked at the page only: no download, no login

    def test_a_gated_page_that_does_not_say_free_still_stops_with_the_signin_advice(self):
        url = "https://decks.example.test/theme/neon"
        self.site({url: httpx.Response(200, text='<html><title>Neon deck</title><body><a href="/theme/neon/download">Download</a></body></html>', headers=HTML),
                   url + "/download": httpx.Response(302, headers={"location": "https://decks.example.test/login"}),
                   "https://decks.example.test/login": httpx.Response(200, text="<form>x</form>", headers=HTML)})
        r = ti.import_link(url)
        self.assertEqual(r["status"], "signin")
        self.assertIn("never signs in", r["message"])

    def test_premium_template_is_not_imported_and_not_downloaded(self):
        url = "https://slidesgo.com/theme/brazilian-halloween"
        self.site({url: httpx.Response(200, text=page("slidesgo_premium.html"), headers=HTML)})
        r = ti.import_link(url)
        self.assertEqual(r["status"], "premium")
        self.assertEqual(r["message"], "This template is for paid members only, so A.F.R.A can't import it.")
        self.assertEqual(self.seen, [("GET", url)])  # no download attempt at all
        self.assertEqual(self.files(), [])

    def test_login_page_for_the_template_page_itself(self):
        self.site({"https://decks.example.test/t": httpx.Response(302, headers={"location": "https://decks.example.test/login?next=/t"}),
                   "https://decks.example.test/login?next=/t": httpx.Response(200, text="<form>", headers=HTML)})
        self.assertEqual(ti.import_link("https://decks.example.test/t")["status"], "signin")

    def test_a_link_that_is_html_or_a_plain_zip_is_never_saved(self):
        import io
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("readme.txt", "hi")
        for body, ctype in ((b"<html>not a file</html>", "text/html"), (buf.getvalue(), "application/zip"), (b"PK\x03\x04junk", PPTX["content-type"])):
            self.site({self.SC: httpx.Response(200, text=page("slidescarnival_free.html"), headers=HTML),
                       self.SC_PPTX: httpx.Response(200, content=body, headers={"content-type": ctype})})
            r = ti.import_link(self.SC)
            self.assertNotEqual(r["status"], "saved", ctype)
        self.assertEqual(self.files(), [])

    def test_direct_pptx_link_and_listing_page(self):
        data = deck("Direct")
        self.site({"https://files.example.test/Deck.pptx": httpx.Response(200, content=data, headers=PPTX)})
        self.assertEqual(ti.import_link("https://files.example.test/Deck.pptx")["status"], "saved")
        self.assertEqual(self.files(), ["Deck.pptx", "Deck.pptx.source.json"])
        self.site({"https://create.microsoft.com/en-us/templates/presentations": httpx.Response(200, text=page("microsoft_listing.html"), headers=HTML)})
        r = ti.import_link("https://create.microsoft.com/en-us/templates/presentations")
        self.assertEqual(r["status"], "no_file")
        self.assertIn("one template", r["message"])

    def test_public_google_slides_deck_exports_as_pptx(self):
        url = "https://docs.google.com/presentation/d/1AbCdEfGhIjKlMnOpQrStUv/edit"
        exp = "https://docs.google.com/presentation/d/1AbCdEfGhIjKlMnOpQrStUv/export/pptx"
        self.site({exp: httpx.Response(200, content=deck("G"), headers={**PPTX, "content-disposition": "attachment; filename*=UTF-8''Lab%20slides.pptx"})})
        r = ti.import_link(url)
        self.assertEqual((r["status"], r["name"]), ("saved", "Lab slides"))
        self.site({exp: httpx.Response(302, headers={"location": "https://accounts.google.com/ServiceLogin?x=1"}),
                   "https://accounts.google.com/ServiceLogin?x=1": httpx.Response(200, text="<form>", headers=HTML)})
        with mock.patch("app.google_api.accounts", return_value=[]):
            r = ti.import_link(url)
        self.assertEqual(r["status"], "signin")
        self.assertIn("Anyone with the link", r["message"])

    def test_private_addresses_are_refused(self):
        self.assertEqual(ti.import_link("http://127.0.0.1:8765/x.pptx")["status"], "error")
        self.assertEqual(ti.import_link("not a link")["status"], "error")


class MissingToolsTest(ImportTestCase):
    def test_preview_only_page_says_what_is_missing_and_run_link_keeps_the_colours_fallback(self):
        url = "https://decks.example.test/pastel"
        html = page("preview_only_free.html")
        self.site({url: httpx.Response(200, text=html, headers=HTML)})
        with mock.patch.object(ti, "rea_command", return_value=None), mock.patch.object(ti, "find_browsers", return_value=[]):
            r = ti.import_link(url)
        self.assertEqual(r["status"], "missing")
        for word in ("REA", "Edge or Google Chrome", "OpenRouter"):
            self.assertIn(word, r["message"])
        with mock.patch.object(ti, "rea_command", return_value=None), mock.patch.object(ti, "find_browsers", return_value=[]), \
                mock.patch.object(designs, "add_web", return_value={"name": "Pastel"}) as aw:
            r = ti.run_link(url)
        aw.assert_called_once_with(url)
        self.assertIn("colours and fonts only", r["message"])

    def test_unsure_pages_are_not_rebuilt(self):
        self.site({"https://decks.example.test/x": httpx.Response(200, text="<html><title>My deck</title><body>hello</body></html>", headers=HTML)})
        r = ti.import_link("https://decks.example.test/x")
        self.assertEqual(r["status"], "unclear")


class VisionSpecTest(ImportTestCase):
    SPEC = {"background": "#fff", "palette": ["#1D3557", "nope", "#E63946"], "fonts": {"heading": "Montserrat", "body": "Lora"},
            "shapes": [{"type": "rect", "x": 0, "y": 0, "w": 40, "h": 100, "fill": "#1D3557"}, {"type": "blob", "x": 90, "y": 90, "w": 50, "h": 50, "fill": "red"},
                       {"x": "bad"}],
            "texts": [{"role": "title", "text": "Thesis defense", "x": 45, "y": 30, "w": 60, "h": 20, "font": "Montserrat", "size": 500, "color": "1d3557",
                       "bold": 1, "align": "middle", "anchor": "bottom"}, {"text": "  ", "x": 1, "y": 1, "w": 5, "h": 5}]}

    def test_validation_clamps_and_drops_junk(self):
        v = ti.validate_spec(self.SPEC)
        self.assertEqual(v["background"], "#FFFFFF")
        self.assertEqual(v["palette"], ["#1D3557", "#E63946"])
        self.assertEqual([s["type"] for s in v["shapes"]], ["rect", "rect"])  # unknown shape type -> rect; the broken one is gone
        self.assertEqual((v["shapes"][1]["w"], v["shapes"][1]["h"], v["shapes"][1]["fill"]), (10, 10, None))  # kept inside the slide; bad colour dropped
        t = v["texts"][0]
        self.assertEqual((len(v["texts"]), t["size"], t["color"], t["align"], t["anchor"], t["w"], t["bold"]), (1, 96, "#1D3557", "left", "bottom", 55, True))
        for bad in (None, [], {}, {"texts": [{"text": ""}], "shapes": []}, "x"):
            with self.assertRaises(ValueError):
                ti.validate_spec(bad)

    def test_renders_an_editable_pptx_with_real_text_boxes(self):
        v = ti.validate_spec(self.SPEC)
        d = ti.build_deck_spec([v, v], "Thesis", (4, 3))
        self.assertEqual(d["size"], (720, 540))
        self.assertEqual((d["theme"]["heading_font"], d["theme"]["body_font"]), ("Montserrat", "Lora"))
        data = pptx_build.build_from_spec(d, "Thesis", (png(), "png"))
        z = zipfile.ZipFile(__import__("io").BytesIO(data))
        sld = z.read("ppt/slides/slide1.xml").decode("utf-8")
        self.assertIn("<a:t>Thesis defense</a:t>", sld)
        self.assertIn('txBox="1"', sld)  # a text box, not a picture
        self.assertNotIn("<p:pic>", sld)
        self.assertIn('<a:ext cx="%d" cy="%d"/>' % (720 * 12700 * 55 // 100, 540 * 12700 * 20 // 100), sld)
        self.assertIn('sz="9600"', sld)
        self.assertIn('algn="l"', sld)
        self.assertIn('<p:sldSz cx="9144000" cy="6858000"/>', z.read("ppt/presentation.xml").decode("utf-8"))
        self.assertEqual(len([n for n in z.namelist() if n.startswith("ppt/slides/slide")]), 2)
        th = designs.pptx_theme(data)  # the Designs folder can read it back, including the preview picture
        self.assertEqual((th["slides"], th["thumbnail"][0]), (2, "png"))

    def llm(self, content, status=200):
        def handler(req):
            self.seen.append(json.loads(req.content))
            return httpx.Response(status, json={"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
        ti.TRANSPORT = httpx.MockTransport(handler)

    def test_read_slide_sends_the_picture_and_parses_fenced_json(self):
        self.llm("Here you go:\n```json\n" + json.dumps(self.SPEC) + "\n```")
        with mock.patch.object(ti.db, "log_usage"):
            v = ti.read_slide(png(), "image/png", 2, 5, ("https://openrouter.ai/api/v1", "sk-test", "z-ai/glm-4.5v"))
        body = self.seen[0]
        self.assertEqual(body["model"], "z-ai/glm-4.5v")
        img = body["messages"][1]["content"][1]["image_url"]["url"]
        self.assertTrue(img.startswith("data:image/png;base64,"))
        self.assertIn("Slide 2 of 5", body["messages"][1]["content"][0]["text"])
        self.assertEqual(v["texts"][0]["text"], "Thesis defense")

    def test_read_slide_errors_are_plain(self):
        conn = ("https://openrouter.ai/api/v1", "sk-test", "z-ai/glm-4.5v")
        self.llm("sorry, no json here")
        with mock.patch.object(ti.db, "log_usage"), self.assertRaises(ValueError):
            ti.read_slide(png(), "image/png", 1, 1, conn)
        self.llm("", 401)
        with self.assertRaises(ti.Stop) as cm:
            ti.read_slide(png(), "image/png", 1, 1, conn)
        self.assertIn("401", cm.exception.message)

    def test_recommended_vision_model_is_the_newest_glm_flash(self):
        live = [{"id": i, "in": 0.1, "out": 0.2} for i in ("z-ai/glm-4.6v", "z-ai/glm-5.3-flash", "z-ai/glm-4.9-flash", "google/gemini-3-flash")]
        with mock.patch.object(ti.models.db, "cache_get", return_value=live):
            self.assertEqual(ti.models.vision_default(), "z-ai/glm-5.3-flash")
        with mock.patch.object(ti.models.db, "cache_get", return_value=None):
            self.assertEqual(ti.models.vision_default(), ti.models.VISION_FALLBACK)

    def test_vision_connection_comes_from_settings(self):
        base = {"llm_provider": "openai", "llm_base_url": "https://openrouter.ai/api/v1", "llm_api_key": "sk-or", "llm2_base_url": "", "llm2_api_key": ""}
        with mock.patch.object(ti, "get_settings", return_value=base), \
                mock.patch.object(ti.models, "vision_default", return_value="z-ai/glm-5.3-flash"):
            self.assertEqual(ti.vision_conn(), ("https://openrouter.ai/api/v1", "sk-or", "z-ai/glm-5.3-flash"))  # the recommended model
        with mock.patch.object(ti, "get_settings", return_value={**base, "template_vision_model": "z-ai/glm-5v"}):
            self.assertEqual(ti.vision_conn()[2], "z-ai/glm-5v")
        with mock.patch.object(ti, "get_settings", return_value={**base, "llm_api_key": ""}), self.assertRaises(ti.Stop) as cm:
            ti.vision_conn()
        self.assertEqual(cm.exception.status, "missing")
        with mock.patch.object(ti, "get_settings", return_value={**base, "llm_base_url": "https://api.openai.com/v1"}), self.assertRaises(ti.Stop):
            ti.vision_conn()  # a GLM model name means nothing to the OpenAI API

    def test_rebuild_end_to_end_with_everything_mocked(self):
        self.llm(json.dumps(self.SPEC))
        info = {"title": "Pastel pitch deck", "kind": "other", "images": [], "credit_mentioned": False}
        with mock.patch.object(ti, "rea_command", return_value=["rea"]), mock.patch.object(ti, "find_browsers", return_value=["edge"]), \
                mock.patch.object(ti, "rea_ready", return_value=True), mock.patch.object(ti, "capture_previews", return_value=[(png(), "image/png")] * 2), \
                mock.patch.object(ti, "vision_conn", return_value=("https://openrouter.ai/api/v1", "k", "z-ai/glm-4.5v")), mock.patch.object(ti.db, "log_usage"):
            r = ti.rebuild_from_preview("https://decks.example.test/pastel", info)
        self.assertEqual((r["status"], r["name"]), ("rebuilt", "Pastel pitch deck (rebuilt)"))
        saved = self.designs_dir / "Pastel pitch deck (rebuilt).pptx"
        self.assertEqual(designs.pptx_theme(saved.read_bytes())["slides"], 2)
        side = json.loads(saved.with_name(saved.name + ".source.json").read_text(encoding="utf-8"))
        self.assertEqual((side["method"], side["source_url"]), ("rebuilt", "https://decks.example.test/pastel"))
        self.assertIn("not an exact copy", side["note"])


class ReaTest(unittest.TestCase):
    def test_command_uses_node_and_the_script_next_to_the_rea_shim(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        script = tmp / "node_modules" / "rea-agents" / "scripts" / "rea.mjs"
        script.parent.mkdir(parents=True)
        script.write_text("//")
        which = {"rea": str(tmp / "rea.cmd"), "node": "C:/node/node.exe", "npm": None}
        (tmp / "rea.cmd").write_text("")
        with mock.patch.object(ti.shutil, "which", side_effect=lambda n: which.get(n)):
            self.assertEqual(ti.rea_command(), ["C:/node/node.exe", str(script)])

    def test_command_found_through_npm_root(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        script = tmp / "rea-agents" / "scripts" / "rea.mjs"
        script.parent.mkdir(parents=True)
        script.write_text("//")
        which = {"rea": None, "node": "node", "npm": "npm"}
        with mock.patch.object(ti.shutil, "which", side_effect=lambda n: which.get(n)), \
                mock.patch.object(ti.subprocess, "run", return_value=mock.Mock(stdout=str(tmp) + "\n")) as run:
            self.assertEqual(ti.rea_command(), ["node", str(script)])
        self.assertEqual(run.call_args[0][0], ["npm", "root", "-g"])

    def test_not_installed(self):
        with mock.patch.object(ti.shutil, "which", return_value=None):
            self.assertIsNone(ti.rea_command())

    def test_rea_run_builds_the_command_and_reads_json(self):
        with mock.patch.object(ti.subprocess, "run", return_value=mock.Mock(returncode=0, stdout='{"normalized_result": {"targets": []}}', stderr="")) as run:
            out = ti.rea_run(["node", "rea.mjs"], ["list-browser-targets", "http://127.0.0.1:9333", "--allowed-origins", "https://a.test"])
        self.assertEqual(run.call_args[0][0], ["node", "rea.mjs", "list-browser-targets", "http://127.0.0.1:9333", "--allowed-origins", "https://a.test",
                                              "--format", "json"])
        self.assertEqual(out["normalized_result"]["targets"], [])
        with mock.patch.object(ti.subprocess, "run", return_value=mock.Mock(returncode=1, stdout="", stderr="boom")), self.assertRaises(ti.Stop):
            ti.rea_run(["rea"], ["doctor"])
        unhealthy = mock.Mock(returncode=1, stdout='{"scope_checks": [{"name": "node", "ok": true}]}', stderr="")  # rea doctor exits 1 but answers
        with mock.patch.object(ti.subprocess, "run", return_value=unhealthy):
            with self.assertRaises(ti.Stop):
                ti.rea_run(["rea"], ["inspect-web-page"])
            self.assertTrue(ti.rea_run(["rea"], ["doctor"], allow_fail=True)["scope_checks"])

    def test_a_stuck_rea_is_stopped_with_a_plain_message(self):
        with mock.patch.object(ti.subprocess, "run", side_effect=ti.subprocess.TimeoutExpired("rea", 5)), self.assertRaisesRegex(ti.Stop, "too long"):
            ti.rea_run(["rea"], ["inspect-web-page"], 5)

    def test_doctor_only_needs_node(self):
        ok = {"scope_checks": [{"name": "node", "ok": True}, {"name": "ghidra", "ok": False}]}
        with mock.patch.object(ti, "rea_run", return_value=ok) as run:
            self.assertTrue(ti.rea_ready(["rea"]))
        self.assertEqual(run.call_args[0][1][0], "doctor")
        with mock.patch.object(ti, "rea_run", return_value={"scope_checks": [{"name": "node", "ok": False}]}):
            self.assertFalse(ti.rea_ready(["rea"]))
        with mock.patch.object(ti, "rea_run", side_effect=ti.Stop("error", "x")):
            self.assertFalse(ti.rea_ready(["rea"]))

    def test_capture_runs_list_inspect_screenshot_in_order_and_stops_the_browser(self):
        calls = []
        shot = __import__("base64").b64encode(png()).decode()

        def rea(cmd, args, timeout=120):
            calls.append(args)
            if args[0] == "list-browser-targets":
                return {"normalized_result": {"targets": [{"target_id": "T1", "type": "page", "url": "https://decks.example.test/pastel"}]}}
            if args[0] == "inspect-web-page":
                return {"normalized_result": {"resources": [{"url": "https://img.example-previews.test/pastel/1.png", "type": "Image",
                                                             "mime_type": "image/png", "content_size": 40000}]}}
            return {"normalized_result": {"artifact": {"data_base64": shot}}}
        entered = []

        class FakeBrowser:
            endpoint = "http://127.0.0.1:9333"

            def __init__(self, exe, url):
                entered.append((exe, url))

            def start(self):
                return self

            def __enter__(self):
                return self

            def __exit__(self, *a):
                entered.append("stopped")
        big = png(400, 300)
        ti.TRANSPORT = httpx.MockTransport(lambda req: httpx.Response(200, content=big + req.url.path.encode() * 6000, headers={"content-type": "image/png"}))
        self.addCleanup(setattr, ti, "TRANSPORT", None)
        with mock.patch.object(ti, "rea_run", side_effect=rea), mock.patch.object(ti, "Browser", FakeBrowser), mock.patch.object(ti.time, "sleep"):
            out = ti.capture_previews(["rea"], "edge", "https://decks.example.test/pastel", ["https://img.example-previews.test/pastel/cover.png"])
        self.assertEqual([c[0] for c in calls], ["list-browser-targets", "inspect-web-page", "capture-web-screenshot"])
        self.assertEqual(calls[1][1:3], ["http://127.0.0.1:9333", "T1"])
        self.assertIn("--allowed-origins", calls[1])
        self.assertIn("https://img.example-previews.test", calls[1])  # the picture host is allowed too
        self.assertEqual(entered, [("edge", "https://decks.example.test/pastel"), "stopped"])
        self.assertEqual(len(out), 2)  # the inspected picture and the page's own preview picture
        self.assertEqual(out[0][1], "image/png")

    def test_previews_are_collected_in_slide_order_not_alphabetical(self):
        def rea(cmd, args, timeout=120):
            if args[0] == "list-browser-targets":
                return {"normalized_result": {"targets": [{"target_id": "T1", "type": "page", "url": "https://decks.example.test/pastel"}]}}
            if args[0] == "inspect-web-page":
                return {"normalized_result": {"resources": [
                    {"url": "https://img.example-previews.test/pastel/preview-10.png", "type": "Image", "mime_type": "image/png", "content_size": 40000},
                    {"url": "https://img.example-previews.test/pastel/preview-2.png", "type": "Image", "mime_type": "image/png", "content_size": 40000},
                    {"url": "https://img.example-previews.test/pastel/preview-1.png", "type": "Image", "mime_type": "image/png", "content_size": 40000}]}}
            return {"normalized_result": {"artifact": {}}}  # no screenshot bytes: the pictures alone are enough

        class FakeBrowser:
            endpoint = "http://127.0.0.1:9333"

            def __init__(self, exe, url):
                pass

            def start(self):
                return self

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass
        big = png(400, 300)
        from urllib.parse import urlparse as up
        expected = ["cover.png", "preview-1.png", "preview-2.png", "preview-10.png"]
        ti.TRANSPORT = httpx.MockTransport(lambda req: httpx.Response(200, content=big + req.url.path.encode() * 6000, headers={"content-type": "image/png"}))
        self.addCleanup(setattr, ti, "TRANSPORT", None)
        with mock.patch.object(ti, "rea_run", side_effect=rea), mock.patch.object(ti, "Browser", FakeBrowser), mock.patch.object(ti.time, "sleep"):
            out = ti.capture_previews(["rea"], "edge", "https://decks.example.test/pastel", ["https://img.example-previews.test/pastel/cover.png"])
        self.assertEqual([len(b) for b, _ in out], [len(big + ("/pastel/" + n).encode() * 6000) for n in expected])

    def test_browser_is_stopped_by_its_own_pid_and_its_profile_removed(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        proc = mock.Mock(pid=4242)
        proc.poll.side_effect = [None, None]
        with mock.patch.object(ti, "DATA_DIR", tmp), mock.patch.object(ti.subprocess, "Popen", return_value=proc) as popen, \
                mock.patch.object(ti.subprocess, "run") as run, mock.patch.object(ti.httpx, "get", return_value=mock.Mock(status_code=200)), \
                mock.patch.object(ti.sys, "platform", "win32"):
            with ti.Browser("C:/edge.exe", "https://a.test/") as b:
                self.assertTrue(b.profile.is_dir())
                args = popen.call_args[0][0]
                self.assertEqual(args[0], "C:/edge.exe")
                self.assertIn("--headless=new", args)
                self.assertIn(f"--remote-debugging-port={b.port}", args)
                self.assertIn(f"--user-data-dir={b.profile}", args)
                self.assertEqual(args[-1], "https://a.test/")
            self.assertFalse(b.profile.exists())
        self.assertEqual(run.call_args[0][0], ["taskkill", "/PID", "4242", "/T", "/F"])


if __name__ == "__main__":
    unittest.main()
