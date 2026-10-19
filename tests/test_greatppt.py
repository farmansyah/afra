"""GreatPPT as a template source, offline: a saved page modelled on the live one, mocked network, mocked GLM and Claude."""
import json
import unittest
from pathlib import Path
from unittest import mock

import httpx

from app import designs, greatppt, handoff, slidesgo, template_import as ti
from tests.test_designs_folder import deck, png
from tests.test_slidesgo import SPEC, SlidesgoTestCase
from tests.test_template_import import HTML, page

GP = "https://www.greatppt.com/downloads/padel-powerpoint-template/"
UP = "https://www.greatppt.com/wp-content/uploads/edd/2026/01/Free-Padel-PowerPoint-Template-and-Google-Slides-Theme-"
SLIDES = [UP + f"{n}.jpg" for n in ("", 1, 2, 3, 4, 6, 9, 12, 15)]


class PageTest(unittest.TestCase):
    def test_classification(self):
        self.assertEqual(ti.classify(GP), "greatppt")
        self.assertEqual(ti.classify("https://greatppt.com/downloads/x/"), "greatppt")
        self.assertEqual(ti.classify("https://www.greatppt.com/files/Deck.pptx"), "file")
        self.assertEqual(ti.classify("https://notgreatppt.com/downloads/x/"), "other")
        self.assertTrue(greatppt.is_template_url(GP))
        self.assertTrue(greatppt.is_template_url("https://greatppt.com/downloads/padel-powerpoint-template"))
        for no in ("https://www.greatppt.com/", "https://www.greatppt.com/downloads/", "https://www.greatppt.com/login/",
                   "https://www.greatppt.com/category/business/", "https://slidesgo.com/downloads/x/"):
            self.assertFalse(greatppt.is_template_url(no), no)
        self.assertEqual(greatppt.template_slug(GP), "padel-powerpoint-template")

    def test_free_marker_title_and_no_direct_links(self):
        i = ti.page_info(page("greatppt_template.html"), GP)
        self.assertEqual((i["kind"], i["title"], i["free"], i["premium"], i["links"]), ("greatppt", "Padel", True, False, []))
        f = greatppt.page_facts(page("greatppt_template.html"))
        self.assertEqual((f["price"], f["download_id"], f["free_word"], f["free"]), (0.0, "21674", True, True))

    def test_a_template_with_a_price_is_not_free(self):
        i = ti.page_info(page("greatppt_paid.html"), GP)
        self.assertEqual((i["free"], i["premium"]), (False, True))

    def test_a_menu_entry_called_premium_templates_does_not_make_a_free_page_premium(self):
        self.assertIn("Premium Templates", page("greatppt_template.html"))
        self.assertFalse(ti.page_info(page("greatppt_template.html"), GP)["premium"])

    def test_lazy_preview_addresses_in_slide_order_without_related_templates(self):
        urls = greatppt.preview_urls(page("greatppt_template.html"), GP)
        self.assertEqual(urls, SLIDES)  # data-src, data-lazy-src, srcset (the full size beats -768x432), cover (no number) first
        self.assertFalse(any("Rock-Festival" in u or "Crazy-Style" in u or "logo" in u or "4th-grade" in u for u in urls))
        self.assertTrue(all("?" not in u and not u.endswith(".webp") for u in urls))

    def test_only_a_resized_copy_or_webp_still_gives_a_preview(self):
        html = (f'<img src="data:image/png;base64,AA" data-src={UP}2-768x432.jpg?x1><img data-src={UP}3.jpg.webp>'
                f'<meta property="og:image" content="{UP}.jpg">')
        self.assertEqual(greatppt.preview_urls(html, GP), [UP + ".jpg", UP + "2-768x432.jpg", UP + "3.jpg"])  # cover from og:image; x.jpg.webp -> x.jpg
        self.assertEqual(greatppt.preview_urls("<p>nothing</p>", GP), [])
        self.assertEqual(greatppt.preview_urls(page("greatppt_template.html"), GP, [UP + "20.jpg"])[-1], UP + "20.jpg")  # REA adds more

    def test_only_greatppts_picture_folder_may_be_downloaded(self):
        self.assertTrue(greatppt.safe_image_url(UP + "1.jpg"))
        for bad in ("https://www.greatppt.com/checkout/", "https://www.greatppt.com/login/", "https://www.greatppt.com/downloads/x/",
                    "https://evil.test/wp-content/uploads/a.jpg", "http://127.0.0.1/wp-content/uploads/a.jpg",
                    "https://www.greatppt.com/wp-content/uploads/../checkout/"):
            self.assertFalse(greatppt.safe_image_url(bad), bad)

    def test_page_that_is_not_a_template_page_stops_before_any_request(self):
        ti.TRANSPORT = httpx.MockTransport(lambda req: self.fail("no request expected"))
        self.addCleanup(setattr, ti, "TRANSPORT", None)
        r = ti.import_link("https://www.greatppt.com/category/business/")
        self.assertEqual(r["status"], "no_file")


class GreatpptTestCase(SlidesgoTestCase):
    def setUp(self):
        super().setUp()
        for p in (mock.patch.object(greatppt, "DATA_DIR", self.data),):
            p.start()
            self.addCleanup(p.stop)

    def serve(self, chat=False, html="greatppt_template.html"):
        def handler(req):
            u = str(req.url)
            self.seen.append((req.method, u))
            if "/wp-content/uploads/edd/" in u:
                return httpx.Response(200, content=png(32, 18), headers={"content-type": "image/png"})
            if "chat/completions" in u and chat:
                return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(SPEC)}}], "usage": {"prompt_tokens": 9, "completion_tokens": 4}})
            if u == GP:
                return httpx.Response(200, text=page(html), headers=HTML)
            return httpx.Response(404)
        ti.TRANSPORT = httpx.MockTransport(handler)

    def assertOnlyPublicPictures(self):
        self.assertEqual(self.seen[0], ("GET", GP))
        for m, u in self.seen[1:]:
            if "chat/completions" in u:
                continue
            self.assertEqual(m, "GET")
            self.assertIn("/wp-content/uploads/edd/", u)
        for m, u in self.seen:
            for bad in ("checkout", "login", "cart", "edd_action", "slidesgo"):
                self.assertNotIn(bad, u.lower().replace("slides-theme", ""))


class RebuildTest(GreatpptTestCase):
    def test_claude_mode_hands_a_task_with_the_previews_to_claude(self):
        self.settings(ai_mode="claude")
        self.serve()
        r = ti.import_link(GP)
        self.assertEqual(r["status"], "claude")
        self.assertEqual(r["hint"], greatppt.HINT)
        self.assertIn("Downloads watcher", r["hint"])
        self.assertOnlyPublicPictures()
        t = handoff.TASKS[r["task"]]
        self.assertEqual((t["kind"], t["design"]["site"]), ("design", "greatppt"))
        text = t["system"] + t["prompt"]
        for need in ("GreatPPT", "ppt-master", "REA", GP, "Padel", "editable", "Do NOT sign in", "Slidesgo Free Downloader", "save_design"):
            self.assertIn(need, text)
        self.assertNotIn("Slidesgo credits slide", text)
        self.assertIn(str(self.designs_dir / "Padel (rebuilt).pptx"), t["prompt"])
        prev = [Path(line.split(". ", 1)[1]) for line in t["prompt"].splitlines() if line[:1].isdigit() and ". " in line]
        self.assertEqual(len(prev), 9)
        self.assertTrue(all(p.is_file() and p.read_bytes()[:4] == b"\x89PNG" for p in prev))

    def test_claude_hands_the_deck_back_with_the_greatppt_credit_and_badge(self):
        self.settings(ai_mode="claude")
        self.serve()
        tid = ti.import_link(GP)["task"]
        data = deck("Padel")
        res = slidesgo.finish_rebuild(tid, data, "x.pptx")
        self.assertEqual((res["site"], "GreatPPT" in res["remind"]), ("GreatPPT", True))
        f = self.designs_dir / "Padel (rebuilt).pptx"
        side = json.loads(f.with_name(f.name + ".source.json").read_text(encoding="utf-8"))
        self.assertEqual((side["source_url"], side["method"], side["site"]), (GP, "rebuilt", "GreatPPT"))
        self.assertTrue(side["attribution"].startswith("Rebuilt from GreatPPT (free template) — not the original file."))
        self.assertIn("GreatPPT's own licence", side["attribution"])
        d = next(x for x in designs.all_designs() if x["source"] == "Designs folder")
        self.assertEqual((d["rebuilt"], d["credit"]), (True, side["attribution"]))
        self.assertEqual(handoff.TASKS[tid]["status"], "done")

    def test_job_item_carries_the_hint_and_finishes_when_claude_hands_back(self):
        self.settings(ai_mode="claude")
        self.serve()
        started = ti.start(GP)
        import time
        for _ in range(200):
            j = ti.job(started["job"])
            if j["items"][0]["state"] == "claude":
                break
            time.sleep(0.05)
        it = j["items"][0]
        self.assertEqual((it["state"], it["hint"]), ("claude", greatppt.HINT))
        slidesgo.finish_rebuild(it["task"], deck("Padel"))
        j = ti.job(started["job"])
        self.assertEqual((j["items"][0]["state"], j["items"][0]["status"]), ("done", "rebuilt"))
        self.assertIn("GreatPPT", j["items"][0]["message"])

    def test_api_mode_glm_end_to_end(self):
        self.settings(ai_mode="api")
        self.serve(chat=True)
        with mock.patch.object(ti, "vision_conn", return_value=("https://openrouter.ai/api/v1", "k", "z-ai/glm-5.3-flash")), mock.patch.object(ti.db, "log_usage"):
            r = ti.import_link(GP)
        self.assertEqual((r["status"], r["name"], r["hint"]), ("rebuilt", "Padel (rebuilt)", greatppt.HINT))
        f = self.designs_dir / "Padel (rebuilt).pptx"
        self.assertEqual(designs.pptx_theme(f.read_bytes())["slides"], 9)
        side = json.loads(f.with_name(f.name + ".source.json").read_text(encoding="utf-8"))
        self.assertEqual((side["method"], side["engine"], side["source_url"], side["site"]), ("rebuilt", "glm", GP, "GreatPPT"))
        self.assertIn("not the original file", side["attribution"])
        self.assertEqual(list((self.data / "slidesgo_previews").glob("*")), [])
        self.assertEqual(len([1 for _, u in self.seen if "chat/completions" in u]), 9)

    def test_a_template_with_a_price_stops_and_fetches_only_the_page(self):
        self.settings(ai_mode="claude")
        self.serve(html="greatppt_paid.html")
        r = ti.import_link(GP)
        self.assertEqual(r["status"], "premium")
        self.assertEqual(self.seen, [("GET", GP)])
        self.assertEqual(handoff.TASKS, {})

    def test_job_reports_the_hint_in_api_mode_too(self):
        self.settings(ai_mode="api")
        self.serve(chat=True)
        with mock.patch.object(ti, "vision_conn", return_value=("https://openrouter.ai/api/v1", "k", "m")), mock.patch.object(ti.db, "log_usage"):
            res = ti.run_link(GP)
        self.assertEqual(res["status"], "rebuilt")
        self.assertIn("Saved to the Designs folder.", res["message"])
        self.assertEqual(res["hint"], greatppt.HINT)


if __name__ == "__main__":
    unittest.main()
