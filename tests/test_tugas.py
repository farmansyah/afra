"""Tugas: folders on disk. Path safety, folder operations, metadata, Recycle Bin (send2trash is mocked)."""
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import tugas


class TugasTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.root = self.tmp / "Tugas"
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        (self.outside / "secret.txt").write_text("x")
        p = mock.patch.object(tugas, "get_settings", return_value={"tugas_dir": str(self.root)})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def names(self, path=""):
        return [i["name"] for i in tugas.list_dir(path)["items"]]

    def test_root_is_created_on_first_use(self):
        self.assertFalse(self.root.exists())
        self.assertEqual(tugas.list_dir()["items"], [])
        self.assertTrue(self.root.is_dir())

    def test_paths_that_escape_the_root_are_rejected(self):
        tugas.mkdir("", "Fisika")
        for bad in ["..", "../outside", "Fisika/../../outside", "..\\outside", "/etc", "\\Windows", "C:\\Windows", "C:/", "D:x", "a\x00b"]:
            with self.assertRaises(tugas.TugasError, msg=bad):
                tugas.resolve(bad)
        for fn in (tugas.list_dir, tugas.reveal, tugas.open_item):
            with self.assertRaises(tugas.TugasError):
                fn("../outside")
        with self.assertRaises(tugas.TugasError):
            tugas.move(["Fisika"], "../outside")
        with self.assertRaises(tugas.TugasError):
            tugas.move(["../outside/secret.txt"], "Fisika")
        with mock.patch.object(tugas, "send2trash") as st, self.assertRaises(tugas.TugasError):
            tugas.delete(["../outside/secret.txt"])
        st.assert_not_called()
        with self.assertRaises(tugas.TugasError):
            tugas.save_upload("../outside", "a.txt", io.BytesIO(b"x"))
        self.assertEqual(tugas.resolve("Fisika/./"), self.root / "Fisika")

    def test_a_link_leading_out_of_the_root_is_rejected(self):
        tugas.root()
        link = self.root / "link"
        try:
            os.symlink(self.outside, link, target_is_directory=True)
        except (OSError, NotImplementedError):  # no symlink privilege on Windows: a junction leads out just the same
            r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(self.outside)], capture_output=True)
            if r.returncode:
                self.skipTest("cannot make a symlink or junction here")
        with self.assertRaises(tugas.TugasError):
            tugas.list_dir("link")
        with self.assertRaises(tugas.TugasError):
            tugas.resolve("link/secret.txt")

    def test_mkdir_rename_and_name_clash(self):
        tugas.mkdir("", "Fisika")
        tugas.mkdir("Fisika", "Tugas 1")
        with self.assertRaises(tugas.TugasError):
            tugas.mkdir("", "Fisika")
        for bad in ["", "  ", "..", "a/b", ".afra.json"]:
            if bad == "a/b":
                continue  # separators are stripped, not an error: "ab"
            with self.assertRaises(tugas.TugasError, msg=bad):
                tugas.mkdir("", bad)
        self.assertEqual(tugas.mkdir("", "a/b")["path"], "ab")
        self.assertEqual(tugas.rename("Fisika/Tugas 1", "Laporan")["path"], "Fisika/Laporan")
        tugas.mkdir("Fisika", "Kuis")
        with self.assertRaises(tugas.TugasError):
            tugas.rename("Fisika/Kuis", "Laporan")
        self.assertEqual(tugas.rename("Fisika/Kuis", "kuis")["path"], "Fisika/kuis")  # only the case changes
        self.assertEqual(self.names("Fisika"), ["kuis", "Laporan"])

    def test_move_into_folder_avoids_overwriting_and_refuses_loops(self):
        tugas.mkdir("", "A")
        tugas.mkdir("", "B")
        tugas.save_upload("A", "x.txt", io.BytesIO(b"a"))
        tugas.save_upload("B", "x.txt", io.BytesIO(b"b"))
        self.assertEqual(tugas.move(["A/x.txt"], "B"), ["B/x (2).txt"])
        self.assertEqual(self.names("B"), ["x (2).txt", "x.txt"])
        self.assertEqual((self.root / "B" / "x.txt").read_text(), "b")
        tugas.mkdir("A", "inner")
        with self.assertRaises(tugas.TugasError):
            tugas.move(["A"], "A/inner")
        with self.assertRaises(tugas.TugasError):
            tugas.move(["A"], "A")
        self.assertEqual(tugas.move(["B"], "A"), ["A/B"])
        with self.assertRaises(tugas.TugasError):
            tugas.move(["A/B/x.txt"], "A/B/x (2).txt")  # not a folder

    def test_upload_keeps_names_and_adds_a_number_on_clash(self):
        tugas.mkdir("", "A")
        self.assertEqual(tugas.save_upload("A", "Tugas.pptx", io.BytesIO(b"1")), "A/Tugas.pptx")
        self.assertEqual(tugas.save_upload("A", "Tugas.pptx", io.BytesIO(b"2")), "A/Tugas (2).pptx")
        self.assertEqual(tugas.save_upload("A", "Tugas.pptx", io.BytesIO(b"3")), "A/Tugas (3).pptx")
        self.assertEqual(tugas.save_upload("A", "C:\\fakepath\\notes", io.BytesIO(b"4")), "A/notes")
        self.assertEqual(tugas.save_upload("A", "notes", io.BytesIO(b"5")), "A/notes (2)")
        self.assertEqual((self.root / "A" / "Tugas.pptx").read_bytes(), b"1")

    def test_listing_puts_folders_first_and_hides_dotfiles(self):
        tugas.mkdir("", "zeta")
        tugas.mkdir("", "Alpha")
        tugas.save_upload("", "b.pdf", io.BytesIO(b"12345"))
        tugas.save_upload("", "a.docx", io.BytesIO(b""))
        (self.root / ".hidden").write_text("x")
        out = tugas.list_dir()["items"]
        self.assertEqual([i["name"] for i in out], ["Alpha", "zeta", "a.docx", "b.pdf"])
        self.assertEqual([i["kind"] for i in out], ["folder", "folder", "file", "file"])
        self.assertEqual((out[3]["size"], out[3]["ext"], out[3]["path"]), (5, "pdf", "b.pdf"))
        self.assertEqual([c["name"] for c in tugas.list_dir("Alpha")["crumbs"]], ["Alpha"])
        self.assertEqual([c["name"] for c in tugas.tree()["children"]], ["Alpha", "zeta"])

    def test_metadata_round_trip_and_travels_with_the_folder(self):
        tugas.mkdir("", "Fisika")
        m = tugas.set_meta("Fisika", due="2026-11-30", note="  Bab 3 saja ", docs=["d1", "d1", "d2"], papers=["p1"])
        self.assertEqual(m, {"due": "2026-11-30", "note": "Bab 3 saja", "docs": ["d1", "d2"], "papers": ["p1"]})
        self.assertTrue((self.root / "Fisika" / ".afra.json").exists())
        row = [i for i in tugas.list_dir()["items"] if i["name"] == "Fisika"][0]
        self.assertEqual((row["due"], row["note"], row["linked"]), ("2026-11-30", "Bab 3 saja", 3))
        tugas.set_meta("Fisika", note="baru")  # fields left out stay
        self.assertEqual(tugas.read_meta(self.root / "Fisika")["due"], "2026-11-30")
        tugas.rename("Fisika", "Fisika 2")
        self.assertEqual(tugas.upcoming(), [{"name": "Fisika 2", "path": "Fisika 2", "due": "2026-11-30", "note": "baru"}])
        with self.assertRaises(tugas.TugasError):
            tugas.set_meta("Fisika 2", due="next week")
        tugas.set_meta("Fisika 2", due="", note="", docs=[], papers=[])
        self.assertFalse((self.root / "Fisika 2" / ".afra.json").exists())

    def test_links_return_titles_and_skip_deleted_items(self):
        with mock.patch.object(tugas.db, "get_document", side_effect=lambda i: {"title": "Esai", "project_id": "pr"} if i == "d1" else None), \
                mock.patch.object(tugas.db, "get_paper", side_effect=lambda i, with_fulltext=True: {"title": "A paper", "project_id": "pr"} if i == "p1" else None):
            tugas.mkdir("", "F")
            tugas.set_meta("F", docs=["d1", "gone"], papers=["p1", "gone"])
            got = tugas.list_dir("F")["links"]
        self.assertEqual([(x["type"], x["title"]) for x in got], [("doc", "Esai"), ("paper", "A paper")])

    def test_search_finds_names_anywhere_in_the_tree(self):
        tugas.mkdir("", "Fisika")
        tugas.mkdir("Fisika", "Laporan")
        tugas.save_upload("Fisika/Laporan", "Laporan akhir.pdf", io.BytesIO(b"x"))
        got = tugas.search("laporan")
        self.assertEqual([(r["path"], r["kind"]) for r in got], [("Fisika/Laporan", "folder"), ("Fisika/Laporan/Laporan akhir.pdf", "file")])
        self.assertEqual(tugas.search(""), [])

    def test_delete_goes_to_the_recycle_bin_never_for_good(self):
        tugas.mkdir("", "A")
        tugas.save_upload("A", "x.txt", io.BytesIO(b"x"))
        with mock.patch.object(tugas, "send2trash") as st:
            self.assertEqual(tugas.delete(["A/x.txt", "A", "missing"]), 2)
        self.assertEqual([c.args[0] for c in st.call_args_list], [str(self.root / "A" / "x.txt"), str(self.root / "A")])
        self.assertTrue((self.root / "A" / "x.txt").exists())  # the mock did not remove it: nothing else deletes
        with mock.patch.object(tugas, "send2trash") as st, self.assertRaises(tugas.TugasError):
            tugas.delete([""])
        st.assert_not_called()

    def test_programs_are_never_launched(self):
        tugas.save_upload("", "setup.exe", io.BytesIO(b"MZ"))
        with mock.patch.object(tugas.os, "startfile", create=True) as sf, mock.patch.object(tugas.subprocess, "Popen") as po:
            with self.assertRaises(tugas.TugasError):
                tugas.open_item("setup.exe")
        sf.assert_not_called()
        po.assert_not_called()


if __name__ == "__main__":
    unittest.main()
