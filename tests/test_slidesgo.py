"""Free Slidesgo templates rebuilt from their public previews, offline: saved pages, mocked network, mocked GLM, mocked Claude."""
import io
import json
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import httpx

from app import designs, handoff, mcp_server, slidesgo, template_import as ti, tugas
from tests.test_designs_folder import deck, png
from tests.test_template_import import HTML, ImportTestCase, page

FREE = "https://slidesgo.com/theme/abstract-art"
PREMIUM = "https://slidesgo.com/theme/brazilian-halloween"
B = "https://media.slidesgo.com/storage/"
SPEC = {"background": "#fff", "palette": ["#1D3557", "#E63946"], "fonts": {"heading": "Montserrat", "body": "Lora"},
        "shapes": [{"type": "rect", "x": 0, "y": 0, "w": 40, "h": 100, "fill": "#1D3557"}],
        "texts": [{"role": "title", "text": "Abstract Art", "x": 45, "y": 30, "w": 50, "h": 20, "size": 40, "color": "#1D3557"}]}


class PreviewUrlsTest(unittest.TestCase):
    def test_free_and_premium_are_read_from_the_page(self):
        f = ti.page_info(page("slidesgo_theme_free.html"), FREE)
        self.assertEqual((f["kind"], f["free"], f["premium"], f["title"]), ("slidesgo", True, False, "Abstract Art"))
        p = ti.page_info(page("slidesgo_theme_premium.html"), PREMIUM)
        self.assertEqual((p["free"], p["premium"]), (False, True))

    def test_largest_variant_per_slide_in_slide_order(self):
        urls = slidesgo.preview_urls(page("slidesgo_theme_free.html"), FREE)
        self.assertEqual(urls, [
            B + "103314346/responsive-images/0-abstract-art___media_library_original_1600_900.jpg",
            B + "103314352/responsive-images/1-abstract-art___media_library_original_1600_900.jpg",
            B + "103314356/responsive-images/2-abstract-art___media_library_original_1600_900.jpg",
            B + "103314362/conversions/3-abstract-art-thumb.jpg",  # only a thumbnail in the page: that one is used
            B + "103314366/conversions/4-abstract-art-thumb.jpg"])
        # other themes' pictures (related templates, the cover) are not slides of this one
        self.assertFalse(any("merry-christmas" in u or "happy-fall" in u for u in urls))

    def test_full_size_original_beats_a_responsive_copy_and_extra_urls_merge(self):
        html = f'<img src="{B}1/conversions/0-x-y-thumb.jpg" srcset="{B}1/responsive-images/0-x-y___media_library_original_1600_900.jpg 1600w">'
        slug = "https://slidesgo.com/theme/x-y"
        self.assertEqual(slidesgo.preview_urls(html, slug), [B + "1/responsive-images/0-x-y___media_library_original_1600_900.jpg"])
        self.assertEqual(slidesgo.preview_urls(html, slug, [B + "1/conversions/0-x-y-original.jpg"]), [B + "1/conversions/0-x-y-original.jpg"])
        self.assertEqual(slidesgo.preview_urls("<p>no pictures</p>", FREE), [])

    def test_only_the_public_picture_host_is_allowed(self):
        self.assertTrue(slidesgo.safe_image_url(B + "1/conversions/0-a-thumb.jpg"))
        for bad in ("https://slidesgo.com/theme/abstract-art/download", "https://slidesgo.com/auth/login", "https://evil.test/a.jpg",
                    "http://127.0.0.1/a.jpg", "https://media.slidesgo.com/storage/1/download/a.jpg"):
            self.assertFalse(slidesgo.safe_image_url(bad), bad)

    def test_rea_missing_is_not_an_error(self):
        steps = []
        with mock.patch.object(ti, "rea_command", return_value=None):
            self.assertEqual(slidesgo.rea_image_urls(FREE, steps.append), [])
        self.assertIn("REA", steps[0])


class SlidesgoTestCase(ImportTestCase):
    def setUp(self):
        super().setUp()
        self.data = self.tmp / "data"
        for p in (mock.patch.object(slidesgo, "DATA_DIR", self.data), mock.patch.object(ti, "DATA_DIR", self.data),
                  mock.patch.object(slidesgo, "rea_image_urls", return_value=[])):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(handoff.TASKS.clear)

    def settings(self, **kw):
        s = {"tugas_dir": str(self.tmp / "Tugas"), "designs_dir": str(self.designs_dir), **kw}
        for mod in (tugas, designs, ti, slidesgo):
            p = mock.patch.object(mod, "get_settings", return_value=s)
            p.start()
            self.addCleanup(p.stop)

    def serve(self, chat=False):
        def handler(req):
            u = str(req.url)
            self.seen.append((req.method, u))
            if u.startswith(B):
                return httpx.Response(200, content=png(32, 18), headers={"content-type": "image/png"})
            if "chat/completions" in u and chat:
                return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(SPEC)}}], "usage": {"prompt_tokens": 9, "completion_tokens": 4}})
            if u == FREE:
                return httpx.Response(200, text=page("slidesgo_theme_free.html"), headers=HTML)
            if u == PREMIUM:
                return httpx.Response(200, text=page("slidesgo_theme_premium.html"), headers=HTML)
            return httpx.Response(404)
        ti.TRANSPORT = httpx.MockTransport(handler)

    def assertNoLoginOrDownload(self):
        for m, u in self.seen:
            self.assertEqual(m, "GET" if "chat/completions" not in u else "POST")
            self.assertFalse(ti.LOGIN_RE.search(u.split("//", 1)[1].split("/", 1)[1] if "/" in u.split("//", 1)[1] else ""), u)
            self.assertNotIn("/download", u)
            self.assertNotIn("login", u)


class PremiumAndFreeTest(SlidesgoTestCase):
    def test_premium_stops_with_the_paid_message_and_fetches_nothing_else(self):
        self.settings(ai_mode="claude")
        self.serve()
        r = ti.import_link(PREMIUM)
        self.assertEqual((r["status"], r["message"]), ("premium", "This template is for paid members only, so A.F.R.A can't import it."))
        self.assertEqual(self.seen, [("GET", PREMIUM)])
        self.assertEqual(handoff.TASKS, {})

    def test_free_template_never_tries_the_download_or_a_login(self):
        self.settings(ai_mode="claude")
        self.serve()
        r = ti.import_link(FREE)
        self.assertEqual(r["status"], "claude")
        self.assertNoLoginOrDownload()
        self.assertEqual(self.seen[0], ("GET", FREE))
        self.assertTrue(all(u.startswith(B) for _, u in self.seen[1:]))  # only the public preview pictures after the page

    def test_page_without_previews_says_so(self):
        self.settings(ai_mode="claude")
        ti.TRANSPORT = httpx.MockTransport(lambda req: httpx.Response(200, text=page("slidesgo_free.html"), headers=HTML))
        self.assertEqual(ti.import_link(FREE)["status"], "no_file")


class ClaudeModeTest(SlidesgoTestCase):
    def test_task_payload(self):
        self.settings(ai_mode="claude")
        self.serve()
        r = ti.import_link(FREE)
        t = handoff.TASKS[r["task"]]
        self.assertEqual((t["kind"], t["status"], t["json"]), ("design", "waiting", False))
        self.assertIn("A.F.R.A task " + r["task"], r["claude_msg"])
        self.assertIn("save_design", r["claude_msg"])
        text = t["system"] + t["prompt"]
        for need in ("ppt-master", "REA", FREE, "Abstract Art", "credits slide", "editable", "Do NOT sign in", "save_design"):
            self.assertIn(need, text)
        self.assertIn(str(self.designs_dir / "Abstract Art (rebuilt).pptx"), t["prompt"])
        previews = [Path(line.split(". ", 1)[1]) for line in t["prompt"].splitlines() if line[:1].isdigit() and ". " in line]
        self.assertEqual(len(previews), 5)
        self.assertTrue(all(p.is_file() and p.read_bytes()[:4] == b"\x89PNG" for p in previews))  # downloaded for Claude to look at
        self.assertEqual(handoff.pending()[0]["id"], r["task"])

    def test_no_openrouter_key_is_needed(self):
        self.settings(ai_mode="claude", llm_api_key="", llm2_api_key="")
        self.serve()
        self.assertEqual(ti.import_link(FREE)["status"], "claude")

    def test_get_task_text_points_to_save_design(self):
        self.settings(ai_mode="claude")
        self.serve()
        r = ti.import_link(FREE)
        with mock.patch.object(mcp_server, "api") as api:
            api.return_value.json.return_value = handoff._visible(handoff.TASKS[r["task"]])
            out = mcp_server.t_get_task({"task_id": r["task"]})
        self.assertIn("save_design", out)
        self.assertNotIn("valid JSON", out)

    def test_job_waits_for_claude_then_finishes_when_the_design_arrives(self):
        self.settings(ai_mode="claude")
        self.serve()
        started = ti.start(FREE)
        for _ in range(200):
            j = ti.job(started["job"])
            if j["items"][0]["state"] == "claude":
                break
            time.sleep(0.05)
        it = j["items"][0]
        self.assertEqual((it["state"], it["step"], j["state"]), ("claude", "Waiting for Claude", "working"))
        slidesgo.finish_rebuild(it["task"], deck("Abstract Art"))
        j = ti.job(started["job"])
        self.assertEqual((j["state"], j["items"][0]["state"], j["items"][0]["status"], j["items"][0]["name"]),
                         ("done", "done", "rebuilt", "Abstract Art (rebuilt)"))

    def test_cancelled_task_ends_the_item(self):
        self.settings(ai_mode="claude")
        self.serve()
        r = ti.import_link(FREE)
        it = {"state": "claude", "task": r["task"]}
        handoff.cancel(r["task"])
        slidesgo.claude_item_state(it)
        self.assertEqual((it["state"], it["status"]), ("done", "error"))


class SaveDesignTest(SlidesgoTestCase):
    def task(self):
        self.settings(ai_mode="claude")
        self.serve()
        return ti.import_link(FREE)["task"]

    def test_rejects_things_that_are_not_a_pptx(self):
        tid = self.task()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("a.txt", "x")
        for bad in (b"<html>hi</html>", b"PK\x03\x04 broken", buf.getvalue(), b""):
            with self.assertRaises(ValueError):
                slidesgo.finish_rebuild(tid, bad)
        self.assertEqual(handoff.TASKS[tid]["status"], "waiting")  # still open: Claude can send a good one
        self.assertEqual(self.files(), [])
        with self.assertRaises(ValueError):
            slidesgo.finish_rebuild("nope", deck())

    def test_good_file_lands_in_designs_with_sidecar_and_badge(self):
        tid = self.task()
        data = deck("Abstract Art")
        res = slidesgo.finish_rebuild(tid, data, "x.pptx")
        self.assertEqual(res["slides"], 2)
        f = self.designs_dir / "Abstract Art (rebuilt).pptx"
        self.assertEqual(f.read_bytes(), data)
        side = json.loads(f.with_name(f.name + ".source.json").read_text(encoding="utf-8"))
        self.assertEqual((side["source_url"], side["method"]), (FREE, "rebuilt"))
        self.assertEqual(side["attribution"], "Rebuilt from Slidesgo (free template) — not the original file. Template by Slidesgo / Freepik: keep the credits slide.")
        d = next(x for x in designs.all_designs() if x["source"] == "Designs folder")
        self.assertEqual((d["name"], d["rebuilt"], d["source_url"]), ("Abstract Art (rebuilt)", True, FREE))
        self.assertEqual(handoff.TASKS[tid]["status"], "done")
        with self.assertRaises(ValueError):  # a finished task takes no second file
            slidesgo.finish_rebuild(tid, data)

    def test_file_claude_already_wrote_at_the_target_is_kept_not_duplicated(self):
        tid = self.task()
        data = deck("Abstract Art")
        (self.designs_dir / "Abstract Art (rebuilt).pptx").write_bytes(data)
        slidesgo.finish_rebuild(tid, data)
        self.assertEqual(self.files(), ["Abstract Art (rebuilt).pptx", "Abstract Art (rebuilt).pptx.source.json"])

    def test_mcp_tool_checks_the_file_before_sending(self):
        tmp = self.tmp / "out"
        tmp.mkdir()
        (tmp / "a.pptx").write_bytes(b"not a zip at all")
        (tmp / "b.txt").write_bytes(deck())
        for name in ("missing.pptx", "a.pptx", "b.txt"):
            with mock.patch.object(mcp_server, "api") as api, self.assertRaises(RuntimeError):
                mcp_server.t_save_design({"task_id": "sg1", "pptx_path": str(tmp / name)})
            api.assert_not_called()
        (tmp / "ok.pptx").write_bytes(deck())
        with mock.patch.object(mcp_server, "api") as api:
            api.return_value.json.return_value = {"name": "Abstract Art (rebuilt)", "slides": 2}
            out = mcp_server.t_save_design({"task_id": "sg1", "pptx_path": str(tmp / "ok.pptx")})
        self.assertEqual(api.call_args[0][:2], ("POST", "/api/designs/rebuilt/sg1"))
        self.assertIn("Rebuilt", out)
        self.assertIn("save_design", mcp_server.TOOLS)


class ApiModeTest(SlidesgoTestCase):
    def test_end_to_end_with_a_mocked_glm(self):
        self.settings(ai_mode="api")
        self.serve(chat=True)
        steps = []
        with mock.patch.object(ti, "vision_conn", return_value=("https://openrouter.ai/api/v1", "k", "z-ai/glm-5.3-flash")), mock.patch.object(ti.db, "log_usage"):
            r = ti.import_link(FREE, steps.append)
        self.assertEqual((r["status"], r["name"]), ("rebuilt", "Abstract Art (rebuilt)"))
        f = self.designs_dir / "Abstract Art (rebuilt).pptx"
        self.assertEqual(designs.pptx_theme(f.read_bytes())["slides"], 5)
        side = json.loads(f.with_name(f.name + ".source.json").read_text(encoding="utf-8"))
        self.assertEqual((side["method"], side["engine"], side["source_url"]), ("rebuilt", "glm", FREE))
        self.assertIn("keep the credits slide", side["attribution"])
        self.assertTrue(any("Rebuilding (GLM)" in s for s in steps))
        self.assertTrue(any("Collecting previews" in s for s in steps))
        chats = [u for _, u in self.seen if "chat/completions" in u]
        self.assertEqual(len(chats), 5)
        self.assertTrue(all(u.startswith(B) or "chat/completions" in u or u == FREE for _, u in self.seen))
        self.assertEqual(handoff.TASKS, {})
        self.assertEqual(list((self.data / "slidesgo_previews").glob("*")), [])  # temp previews cleaned up

    def test_missing_key_is_said_before_anything_is_downloaded(self):
        self.settings(ai_mode="api")
        self.serve()
        with mock.patch.object(ti, "vision_conn", side_effect=ti.Stop("missing", "needs an OpenRouter key")):
            r = ti.import_link(FREE)
        self.assertEqual(r["status"], "missing")
        self.assertIn("OpenRouter", r["message"])
        self.assertEqual([u for _, u in self.seen if u.startswith(B)], [])


if __name__ == "__main__":
    unittest.main()
