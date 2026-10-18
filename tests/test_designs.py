"""The design gallery: built-ins, saved designs, PowerPoint themes, template pages (a saved HTML fixture, no network)."""
import shutil
import unittest
from pathlib import Path

import httpx

from app import designs, pptx_build

FIXTURE = Path(__file__).parent / "fixtures" / "slidesgo_like.html"
PAGE = "https://templates.example.test/theme/minimal-thesis-defense"


class DesignLibraryTest(unittest.TestCase):
    def setUp(self):
        designs.FILE.unlink(missing_ok=True)
        shutil.rmtree(designs.DIR, ignore_errors=True)

    def tearDown(self):
        designs.TRANSPORT = None

    def test_builtins_are_seeded_once_with_one_default(self):
        rows = designs.all_designs()
        self.assertEqual([r["id"] for r in rows], [b["id"] for b in designs.BUILTIN])
        self.assertEqual(sum(r["default"] for r in rows), 1)
        self.assertEqual(designs.default()["id"], "afra-navy")
        designs.delete("afra-mono")
        self.assertNotIn("afra-mono", [r["id"] for r in designs.all_designs()])  # not re-seeded behind the user's back
        designs.restore_builtins()
        self.assertIn("afra-mono", [r["id"] for r in designs.all_designs()])

    def test_rename_default_delete(self):
        d = designs.from_colors("My lab", ["#0B3954", "#BFD7EA", "#FF5A5F", "#C81D25"], ["Lato", "Source Sans 3"])
        self.assertEqual(d["kind"], "colors")
        self.assertEqual(d["theme"]["dark"], "#0B3954")
        self.assertEqual((d["theme"]["heading_font"], d["theme"]["body_font"]), ("Lato", "Source Sans 3"))
        designs.update(d["id"], name="  Lab style ", make_default=True)
        rows = designs.all_designs()
        self.assertEqual(rows[0]["id"], d["id"])  # the default is listed first
        self.assertEqual(rows[0]["name"], "Lab style")
        self.assertEqual([r["id"] for r in rows if r["default"]], [d["id"]])  # only one default
        designs.delete(d["id"])
        self.assertEqual(sum(r["default"] for r in designs.all_designs()), 1)  # another one took over
        with self.assertRaises(ValueError):
            designs.get(d["id"])
        self.assertTrue(designs.FILE.exists())  # it is all in data/designs.json

    def test_pptx_upload_becomes_a_design(self):
        t = {**designs.THEME, "dark": "#102030", "accent": "#E07A5F", "accent2": "#3D405B", "heading_font": "Cambria", "body_font": "Candara"}
        data, _ = pptx_build.build_pptx([{"layout": "title", "title": "Hello"}, {"layout": "content", "title": "Two", "bullets": ["a"]}], t, "Test")
        th = designs.pptx_theme(data)
        self.assertEqual(th["slides"], 2)
        self.assertEqual((th["heading_font"], th["body_font"]), ("Cambria", "Candara"))
        self.assertEqual(th["dark"], "#102030")
        self.assertIn("#E07A5F", (th["accent"], th["accent2"]))
        d = designs.add_pptx(data, "Lab template.pptx")
        self.assertEqual((d["name"], d["kind"], d["has_pptx"]), ("Lab template", "pptx", True))
        self.assertEqual(designs.pptx_bytes(d["id"]), data)
        with self.assertRaises(ValueError):
            designs.pptx_theme(b"not a zip")

    def test_thumb_falls_back_to_a_swatch(self):
        data, mime = designs.thumb("afra-plum")
        self.assertEqual(mime, "image/svg+xml")
        self.assertIn(b"#3D1F47", data)


class TemplatePageTest(unittest.TestCase):
    def setUp(self):
        designs.FILE.unlink(missing_ok=True)
        shutil.rmtree(designs.DIR, ignore_errors=True)
        self.html = FIXTURE.read_text(encoding="utf-8")

    def tearDown(self):
        designs.TRANSPORT = None

    def test_parse_saved_page(self):
        p = designs.parse_page(self.html, PAGE)
        self.assertEqual(p["title"], "Minimal Thesis Defense")
        self.assertEqual(p["images"][0], "https://media.example-templates.test/previews/minimal-thesis-cover.jpg")
        self.assertIn("https://templates.example.test/previews/minimal-thesis-1.jpg", p["images"])  # lazy data-src, made absolute
        self.assertFalse([u for u in p["images"] if "logo" in u or "icon" in u])
        self.assertEqual(p["colors"][:5], ["#1D3557", "#457B9D", "#E63946", "#A8DADC", "#F1FAEE"])
        self.assertNotIn("#FFFFFF", p["colors"])
        self.assertEqual(p["fonts"][:2], ["Montserrat", "Lora"])
        self.assertTrue(p["google_slides"])

    def test_add_web_reads_the_page_only(self):
        seen = []

        def site(req):
            seen.append(str(req.url))
            if req.url.path.endswith(".jpg"):
                return httpx.Response(200, content=b"\xff\xd8\xff fake jpeg", headers={"content-type": "image/jpeg"})
            return httpx.Response(200, text=self.html, headers={"content-type": "text/html; charset=utf-8"})
        designs.TRANSPORT = httpx.MockTransport(site)
        d = designs.add_web(PAGE)
        self.assertEqual((d["name"], d["kind"], d["source_url"]), ("Minimal Thesis Defense", "web", PAGE))
        self.assertEqual(d["theme"]["heading_font"], "Montserrat")
        self.assertEqual(d["theme"]["dark"], "#1D3557")
        self.assertEqual(designs.thumb(d["id"])[1], "image/jpeg")  # the page's own preview picture
        self.assertFalse([u for u in seen if "login" in u or "download" in u])  # never the download behind the login

    def test_login_and_files_are_refused(self):
        designs.TRANSPORT = httpx.MockTransport(lambda req: httpx.Response(403))
        with self.assertRaisesRegex(ValueError, "login"):
            designs.fetch_page(PAGE)

        def redirect(req):
            if "login" in req.url.path:
                return httpx.Response(200, text="<form>sign in</form>", headers={"content-type": "text/html"})
            return httpx.Response(302, headers={"location": "https://templates.example.test/login?next=/x"})
        designs.TRANSPORT = httpx.MockTransport(redirect)
        with self.assertRaisesRegex(ValueError, "login"):
            designs.fetch_page(PAGE)
        designs.TRANSPORT = httpx.MockTransport(lambda req: httpx.Response(200, content=b"PK..", headers={"content-type": "application/zip"}))
        with self.assertRaisesRegex(ValueError, "file"):
            designs.fetch_page(PAGE)
        with self.assertRaises(ValueError):
            designs.fetch_page("file:///etc/passwd")

    def test_links(self):
        text = (f"{PAGE}\n  https://docs.google.com/presentation/d/1AbCdEfGhIjKlMnOpQrStUv/edit, {PAGE}\n"
                "and (https://www.slidescarnival.example/x).")
        links = designs.split_links(text)
        self.assertEqual(len(links), 3)
        self.assertEqual(links[2], "https://www.slidescarnival.example/x")
        self.assertEqual([designs.is_slides_link(u) for u in links], [False, True, False])
        self.assertEqual(len(designs.split_links(" ".join(f"https://a.test/{i}" for i in range(30)))), 12)


if __name__ == "__main__":
    unittest.main()
