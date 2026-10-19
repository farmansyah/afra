"""The Designs folder: every .pptx in it is a design (lock files ignored, removed files drop out, thumbnails cached by
path + mtime + size), plus saving a deck into a Tugas folder (name clash -> "(2)")."""
import asyncio
import os
import shutil
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

from app import designs, pptx_build, tugas, tugas_decks


def png(w=16, h=9, rgb=(200, 30, 30)) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def ch(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b"")


def deck(title="Hello", thumb=None, dark="#102030") -> bytes:
    t = {**designs.THEME, "dark": dark}
    return pptx_build.build_pptx([{"layout": "title", "title": title}, {"layout": "content", "title": "Two", "bullets": ["a"]}], t, title,
                                 thumbnail=(thumb, "png") if thumb else None)[0]


class FolderTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.tugas_root = self.tmp / "Tugas"
        self.designs_dir = self.tmp / "Designs"
        settings = {"tugas_dir": str(self.tugas_root), "designs_dir": str(self.designs_dir)}
        for p in (mock.patch.object(tugas, "get_settings", return_value=settings), mock.patch.object(designs, "get_settings", return_value=settings),
                  mock.patch.object(designs, "THUMBS", self.tmp / "thumbs"), mock.patch.object(designs, "FILE", self.tmp / "designs.json"),
                  mock.patch.object(designs, "DIR", self.tmp / "store")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def listed(self):
        return {r["name"]: r for r in designs.all_designs() if r.get("source") == "Designs folder"}


class DesignsFolderTest(FolderTestCase):
    def test_default_folder_is_inside_tugas_and_created_on_first_use(self):
        with mock.patch.object(designs, "get_settings", return_value={"designs_dir": ""}):
            self.assertEqual(designs.folder(), self.tugas_root / "_Designs")
            self.assertTrue((self.tugas_root / "_Designs").is_dir())
        self.assertIn("_Designs", [i["name"] for i in tugas.list_dir()["items"]])  # a normal folder in the Tugas tree

    def test_every_pptx_is_listed_subfolders_included_lock_files_ignored(self):
        f = designs.folder()
        (f / "Blue.pptx").write_bytes(deck("Blue"))
        (f / "Old school").mkdir()
        (f / "Old school" / "Serif.pptx").write_bytes(deck("Serif", dark="#331122"))
        (f / "~$Blue.pptx").write_bytes(b"lock file")  # PowerPoint's lock file
        (f / "notes.txt").write_text("not a design")
        (f / "broken.pptx").write_bytes(b"not a zip")
        names = self.listed()
        self.assertEqual(sorted(names), ["Blue", "Serif"])
        self.assertEqual(names["Serif"]["theme"]["dark"], "#331122")
        self.assertEqual((names["Blue"]["kind"], names["Blue"]["has_pptx"], names["Blue"]["id"][:2]), ("pptx", True, "fd"))
        self.assertEqual(designs.folder_status()["count"], 3)  # Blue, Serif, broken (counted files; ~$ and .txt are not)
        self.assertEqual(designs.pptx_bytes(names["Blue"]["id"]), (f / "Blue.pptx").read_bytes())

    def test_removed_files_drop_out_and_changed_files_refresh(self):
        f = designs.folder()
        (f / "A.pptx").write_bytes(deck("A", dark="#111111"))
        (f / "B.pptx").write_bytes(deck("B"))
        self.assertEqual(sorted(self.listed()), ["A", "B"])
        (f / "B.pptx").unlink()
        self.assertEqual(sorted(self.listed()), ["A"])
        with self.assertRaises(ValueError):
            designs.get("fd" + "0" * 10)
        old = self.listed()["A"]["theme"]["dark"]
        (f / "A.pptx").write_bytes(deck("A", dark="#223344"))
        st = (f / "A.pptx").stat()
        os.utime(f / "A.pptx", ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        self.assertEqual((old, self.listed()["A"]["theme"]["dark"]), ("#111111", "#223344"))

    def test_thumbnail_is_cached_by_path_mtime_and_size_outside_the_users_folder(self):
        f = designs.folder()
        p = f / "Pic.pptx"
        p.write_bytes(deck("Pic", thumb=png()))
        d = self.listed()["Pic"]
        data, mime = designs.thumb(d["id"])
        self.assertEqual((mime, data[:4]), ("image/png", b"\x89PNG"))
        key = designs._key(p)
        self.assertTrue((designs.THUMBS / f"{key}.png").exists() and (designs.THUMBS / f"{key}.json").exists())
        self.assertEqual(sorted(x.name for x in f.iterdir()), ["Pic.pptx"])  # nothing written into the user's folder
        with mock.patch.object(designs, "pptx_theme", side_effect=AssertionError("read the file again")):
            self.assertEqual(designs.thumb(d["id"])[1], "image/png")  # served from the cache
        st = p.stat()
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 7_000_000_000))
        self.assertNotEqual(designs._key(p), key)  # a changed file gets a new key...
        self.listed()
        self.assertFalse((designs.THUMBS / f"{key}.png").exists())  # ...and the old cache entry is dropped
        self.assertEqual(designs.thumb(self.listed()["Pic"]["id"])[1], "image/png")

    def test_without_embedded_picture_the_swatch_is_used(self):
        designs.folder().joinpath("Plain.pptx").write_bytes(deck("Plain"))
        self.assertEqual(designs.thumb(self.listed()["Plain"]["id"])[1], "image/svg+xml")

    def test_upload_goes_into_the_folder_once_and_keeps_working(self):
        d = designs.add_pptx(deck("Lab"), "Lab template.pptx")
        self.assertTrue((self.designs_dir / "Lab template.pptx").exists())
        self.assertEqual([r["name"] for r in designs.all_designs() if r["kind"] == "pptx"], ["Lab template"])  # not listed twice
        self.assertEqual(designs.pptx_bytes(d["id"]), (self.designs_dir / "Lab template.pptx").read_bytes())
        d2 = designs.add_pptx(deck("Lab"), "Lab template.pptx")  # same name again
        self.assertTrue((self.designs_dir / "Lab template (2).pptx").exists())
        self.assertNotEqual(d["id"], d2["id"])

    def test_folder_design_can_be_renamed_set_as_default_and_deleted(self):
        p = designs.folder() / "Mine.pptx"
        p.write_bytes(deck("Mine"))
        did = self.listed()["Mine"]["id"]
        designs.update(did, name="Mine renamed", make_default=True)
        rows = [r for r in designs.all_designs() if r["kind"] == "pptx"]
        self.assertEqual([(r["name"], r["default"]) for r in rows], [("Mine renamed", True)])  # saved once, not twice
        self.assertTrue(designs.pptx_bytes(did))
        with mock.patch("send2trash.send2trash") as trash:
            designs.delete(did)
        trash.assert_called_once_with(str(p))  # the file goes to the Recycle Bin, never deleted for good


class DeckIntoTugasFolderTest(FolderTestCase):
    def make(self, **kw):
        args = dict(folder="Fisika", design_id="afra-navy", title="Hasil praktikum", source=None, slides=None)
        args.update(kw)
        return asyncio.run(tugas_decks.create_deck(**args))

    def test_saved_into_the_folder_and_name_clash_gets_a_number(self):
        tugas.mkdir("", "Fisika")
        first = self.make()
        self.assertEqual(first["path"], "Fisika/Hasil praktikum.pptx")
        second = self.make()
        self.assertEqual(second["path"], "Fisika/Hasil praktikum (2).pptx")
        self.assertEqual(sorted(i["name"] for i in tugas.list_dir("Fisika")["items"]), ["Hasil praktikum (2).pptx", "Hasil praktikum.pptx"])
        data = (self.tugas_root / "Fisika" / "Hasil praktikum.pptx").read_bytes()
        self.assertEqual(data[:2], b"PK")
        self.assertEqual(designs.pptx_theme(data)["slides"], 3)  # title, one content slide, closing
        self.assertEqual(first["slides"], 3)

    def test_ready_made_slides_and_design_colours(self):
        tugas.mkdir("", "Fisika")
        slides = [{"layout": "title", "title": "Hukum Newton"}, {"layout": "content", "title": "Isi", "bullets": ["Satu", "Dua"]}]
        r = self.make(design_id="afra-plum", slides=slides, title="Newton")
        th = designs.pptx_theme((self.tugas_root / r["path"]).read_bytes())
        self.assertEqual(th["slides"], 2)
        self.assertEqual(th["dark"], "#3D1F47")  # the Plum design

    def test_folder_design_works_as_a_design(self):
        tugas.mkdir("", "Fisika")
        (designs.folder() / "Navy-ish.pptx").write_bytes(deck("x", dark="#0A1B2C"))
        did = self.listed()["Navy-ish"]["id"]
        r = self.make(design_id=did)
        self.assertEqual(designs.pptx_theme((self.tugas_root / r["path"]).read_bytes())["dark"], "#0A1B2C")

    def test_bad_folder_or_design_fails_before_anything_is_written(self):
        with self.assertRaises(tugas.TugasError):
            self.make(folder="Nope")
        with self.assertRaises(tugas.TugasError):
            self.make(folder="../outside")
        tugas.mkdir("", "Fisika")
        with self.assertRaises(ValueError):
            self.make(design_id="gone")
        self.assertEqual(tugas.list_dir("Fisika")["items"], [])

    def test_source_body_from_a_document(self):
        with mock.patch.object(tugas_decks.db, "get_document", return_value={"id": "d1", "title": "Laporan", "project_id": "p1"}):
            b = tugas_decks.source_body("Judul", {"type": "doc", "id": "d1"})
        self.assertEqual((b["document_id"], b["project_id"], b["topic"]), ("d1", "p1", "Laporan"))
        with mock.patch.object(tugas_decks.db, "get_document", return_value=None), self.assertRaises(ValueError):
            tugas_decks.source_body("Judul", {"type": "doc", "id": "gone"})
        self.assertEqual(tugas_decks.source_body("Judul", {"type": "blank"}), {"title": "Judul"})


if __name__ == "__main__":
    unittest.main()
