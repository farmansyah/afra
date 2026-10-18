"""The PowerPoint engine: a valid .pptx for every layout, and where the file goes (Drive for desktop or the local folder)."""
import io
import os
import struct
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path

from app import designs, pptx_build

SLIDES = [
    {"layout": "title", "title": "Heat & <pressure> in refineries", "subtitle": "Thesis defense", "notes": "Hello everyone."},
    {"layout": "section", "title": "Background"},
    {"layout": "content", "title": "Why it matters", "bullets": ["One", "Two (Lee, 2020)"], "notes": "Say why."},
    {"layout": "two_column", "title": "Before and after", "left": {"heading": "Before", "bullets": ["a", "b"]}, "right": {"heading": "After", "bullets": ["c"]}},
    {"layout": "figure", "title": "A figure", "figure": {"url": "https://img.test/fig.png"}, "caption": "Figure 1."},
    {"layout": "table", "title": "Results", "table": {"header": ["Unit", "Yield"], "rows": [["CDU", "41%"], ["FCC", "52%"]]}, "caption": "Table 1."},
    {"layout": "chart", "title": "A chart", "chart": {}, "caption": "From the sheet."},
    {"layout": "references", "title": "References", "bullets": ["Lee, A. (2020). Heat. Journal."]},
    {"layout": "closing", "title": "Thank you", "subtitle": "Questions?"},
]


def png(w=4, h=3):
    raw = b"".join(b"\x00" + b"\x80\x80\x80" * w for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class BuildTest(unittest.TestCase):
    def test_every_layout_makes_a_valid_package(self):
        data, flags = pptx_build.build_pptx(SLIDES, designs.THEME, "Heat", {"https://img.test/fig.png": (png(), "png")})
        z = zipfile.ZipFile(io.BytesIO(data))
        self.assertIsNone(z.testzip())
        for n in z.namelist():
            if n.endswith((".xml", ".rels")):
                ET.fromstring(z.read(n))  # every part is well-formed XML
        names = z.namelist()
        self.assertEqual(sum(1 for n in names if n.startswith("ppt/slides/slide") and n.endswith(".xml")), len(SLIDES))
        self.assertTrue(any(n.startswith("ppt/media/") and n.endswith(".png") for n in names))
        types = z.read("[Content_Types].xml").decode()
        self.assertIn('Extension="png"', types)
        for n in names:  # every relationship points at a part that exists
            if n.endswith(".rels"):
                base = n.replace("_rels/", "").rsplit(".rels", 1)[0].rsplit("/", 1)[0]
                for r in ET.fromstring(z.read(n)):
                    if r.get("TargetMode") == "External":
                        continue
                    target = os.path.normpath(os.path.join(base, r.get("Target"))).replace("\\", "/").lstrip("/")
                    self.assertIn(target, names, f"{n} -> {r.get('Target')}")
        self.assertIn("Hello everyone.", z.read("ppt/notesSlides/notesSlide1.xml").decode())
        self.assertIn("Heat &amp; &lt;pressure&gt;", z.read("ppt/slides/slide1.xml").decode())
        self.assertIn("CDU", z.read("ppt/slides/slide6.xml").decode())
        self.assertTrue(any(f["slide"] == 7 for f in flags))  # the chart can't be drawn in the file: flagged

    def test_opens_in_powerpoint_when_installed(self):
        if os.name != "nt" or os.environ.get("AFRA_TEST_POWERPOINT") != "1":
            self.skipTest("set AFRA_TEST_POWERPOINT=1 on Windows with PowerPoint to run this")
        try:
            import win32com.client
        except ImportError:
            self.skipTest("pywin32 isn't installed")
        data, _ = pptx_build.build_pptx(SLIDES, designs.THEME, "Heat", {"https://img.test/fig.png": (png(), "png")})
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "t.pptx"
            f.write_bytes(data)
            app = win32com.client.DispatchEx("PowerPoint.Application")
            try:
                pres = app.Presentations.Open(str(f), ReadOnly=True, WithWindow=False)
                self.assertEqual(pres.Slides.Count, len(SLIDES))
                pres.Close()
            finally:
                app.Quit()


class SaveTest(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get("AFRA_GDRIVE_ROOT")

    def tearDown(self):
        os.environ["AFRA_GDRIVE_ROOT"] = self.old

    def test_into_drive_for_desktop_folder(self):
        with tempfile.TemporaryDirectory() as drive:
            os.environ["AFRA_GDRIVE_ROOT"] = drive
            a = pptx_build.save(b"one", "My: deck?")
            b = pptx_build.save(b"two", "My: deck?")
            self.assertTrue(a["in_drive"])
            self.assertEqual(Path(a["folder"]), Path(drive) / "A.F.R.A Decks")
            self.assertEqual(Path(a["path"]).name, "My_ deck_.pptx")
            self.assertEqual(Path(b["path"]).name, "My_ deck_ (2).pptx")
            self.assertEqual(Path(b["path"]).read_bytes(), b"two")
            self.assertIn("Drive", a["note"])

    def test_without_drive_it_stays_on_this_computer(self):
        from app.slides import FOLDER
        os.environ["AFRA_GDRIVE_ROOT"] = os.path.join(tempfile.gettempdir(), "afra-no-such-drive")
        r = pptx_build.save(b"x", "Local deck")
        self.assertFalse(r["in_drive"])
        self.assertEqual(Path(r["folder"]), FOLDER / "decks")
        self.assertIn("isn't installed", r["note"])
        r2 = pptx_build.save(b"x", "Local deck", to_drive=False)
        self.assertEqual(Path(r2["path"]).name, "Local deck (2).pptx")


if __name__ == "__main__":
    unittest.main()
