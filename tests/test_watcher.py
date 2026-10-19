"""The Downloads watcher, offline: temp folders, a moved clock and a mocked Zone.Identifier read."""
import io
import os
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from app import designs, template_import as ti, tugas, watcher
from tests.test_designs_folder import deck

ZONE_GREATPPT = ("[ZoneTransfer]\r\nZoneId=3\r\nReferrerUrl=https://www.greatppt.com/downloads/padel-powerpoint-template/\r\n"
                 "HostUrl=https://www.greatppt.com/?edd_action=download&token=x\r\n")


class WatcherTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=Path(__file__).parent)).resolve()
        self.dl = self.tmp / "Downloads"
        self.dl.mkdir()
        self.designs_dir = self.tmp / "Designs"
        self.set = {"tugas_dir": str(self.tmp / "Tugas"), "designs_dir": str(self.designs_dir), "downloads_dir": str(self.dl),
                    "downloads_watch": True, "downloads_action": "move"}
        for mod in (tugas, designs, watcher):
            p = mock.patch.object(mod, "get_settings", side_effect=lambda: self.set)
            p.start()
            self.addCleanup(p.stop)
        for p in (mock.patch.object(designs, "THUMBS", self.tmp / "thumbs"), mock.patch.object(designs, "FILE", self.tmp / "designs.json"),
                  mock.patch.object(designs, "DIR", self.tmp / "store")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.offset = 0.0
        p = mock.patch.object(watcher, "_now", side_effect=lambda: time.time() + self.offset)
        p.start()
        self.addCleanup(p.stop)
        self.zone = ""
        p = mock.patch.object(watcher, "_read_ads", side_effect=lambda path: self.zone)
        p.start()
        self.addCleanup(p.stop)
        self.w = watcher.Watcher()

    def advance(self, secs=watcher.STABLE_SECS + 1):
        self.offset += secs

    def run_ticks(self, n=3):
        """Tick like the app does, with the clock moving past the stable time between ticks."""
        out = []
        for _ in range(n):
            out += self.w.tick()
            self.advance()
        return out

    def start(self):
        self.assertEqual(self.w.tick(), [])  # the first look only learns what is already there
        time.sleep(0.01)

    def designs_files(self):
        return sorted(p.name for p in self.designs_dir.glob("*")) if self.designs_dir.exists() else []


class WhichFilesTest(WatcherTestCase):
    def test_files_that_were_already_there_are_never_touched(self):
        (self.dl / "Old.pptx").write_bytes(deck("Old"))
        self.start()
        (self.dl / "Old.pptx").write_bytes(deck("Old edited"))  # even if the user saves over it later
        self.assertEqual(self.run_ticks(), [])
        self.assertTrue((self.dl / "Old.pptx").exists())
        self.assertEqual(self.designs_files(), [])

    def test_a_new_file_is_added_after_it_is_stable(self):
        self.start()
        (self.dl / "Padel Presentation.pptx").write_bytes(deck("Padel"))
        self.assertEqual(self.w.tick(), [])  # first sight: not yet known to be stable
        self.assertEqual(self.w.tick(), [])  # same moment: not stable long enough
        self.advance()
        ev = self.w.tick()
        self.assertEqual([e["name"] for e in ev], ["Padel Presentation"])
        self.assertFalse((self.dl / "Padel Presentation.pptx").exists())  # moved
        self.assertEqual(self.designs_files(), ["Padel Presentation.pptx", "Padel Presentation.pptx.source.json"])
        self.assertEqual([e["name"] for e in self.w.events_since(0)], ["Padel Presentation"])
        self.assertEqual(self.w.events_since(time.time() + 100), [])

    def test_a_file_that_keeps_growing_waits(self):
        self.start()
        f = self.dl / "Big.pptx"
        data = deck("Big")
        f.write_bytes(data[:200])
        self.w.tick()
        self.advance()
        f.write_bytes(data[:600])  # still being written
        self.assertEqual(self.w.tick(), [])
        self.advance()
        f.write_bytes(data)
        self.assertEqual(self.w.tick(), [])  # changed again: the clock starts over
        self.assertEqual([e["name"] for e in self.run_ticks(2)], ["Big"])

    def test_partial_downloads_and_lock_files_are_ignored(self):
        self.start()
        (self.dl / "Half.pptx.crdownload").write_bytes(deck("Half"))
        (self.dl / "x.tmp").write_bytes(deck("Tmp"))
        (self.dl / "~$Open.pptx").write_bytes(deck("Lock"))
        (self.dl / ".hidden.pptx").write_bytes(deck("Hidden"))
        (self.dl / "Wait.pptx").write_bytes(deck("Wait"))
        (self.dl / "Wait.pptx.crdownload").write_bytes(b"")  # the browser's marker is still there
        self.assertEqual(self.run_ticks(), [])
        self.assertEqual(self.designs_files(), [])
        (self.dl / "Wait.pptx.crdownload").unlink()  # download finished
        self.assertEqual([e["name"] for e in self.run_ticks(3)], ["Wait"])
        for n in ("Half.pptx.crdownload", "x.tmp", "~$Open.pptx", ".hidden.pptx"):
            self.assertTrue((self.dl / n).exists(), n)

    def test_things_that_are_not_a_real_powerpoint_stay_where_they_are(self):
        self.start()
        (self.dl / "fake.pptx").write_bytes(b"<html>not a deck</html>")
        with zipfile.ZipFile(self.dl / "zip.pptx", "w") as z:
            z.writestr("a.txt", "x")
        (self.dl / "broken.pptx").write_bytes(b"PK\x03\x04 broken")
        for n in ("paper.pdf", "notes.docx", "data.xlsx", "readme.txt", "slides.ppt", "deck.zip"):
            (self.dl / n).write_bytes(b"whatever")
        before = {p.name: p.read_bytes() for p in self.dl.iterdir()}
        self.assertEqual(self.run_ticks(4), [])
        self.assertEqual({p.name: p.read_bytes() for p in self.dl.iterdir()}, before)
        self.assertEqual(self.designs_files(), [])

    def test_off_means_nothing_and_turning_it_on_forgets_the_past(self):
        self.set["downloads_watch"] = "off"
        (self.dl / "A.pptx").write_bytes(deck("A"))
        self.assertEqual(self.run_ticks(), [])
        self.set["downloads_watch"] = "on"
        self.assertEqual(self.run_ticks(), [])  # A.pptx was there before it was switched on
        self.assertTrue((self.dl / "A.pptx").exists())
        (self.dl / "B.pptx").write_bytes(deck("B"))
        t = time.time() + self.offset  # the test clock has moved on: a file written now carries that time
        os.utime(self.dl / "B.pptx", (t, t))
        self.assertEqual([e["name"] for e in self.run_ticks()], ["B"])

    def test_a_deleted_name_that_comes_back_is_a_new_file(self):
        (self.dl / "Again.pptx").write_bytes(deck("one"))
        self.start()
        (self.dl / "Again.pptx").unlink()
        self.w.tick()
        (self.dl / "Again.pptx").write_bytes(deck("two"))
        self.assertEqual([e["name"] for e in self.run_ticks()], ["Again"])

    def test_the_designs_folder_is_never_watched(self):
        self.set["downloads_dir"] = str(self.designs_dir)
        self.designs_dir.mkdir()
        self.start()
        (self.designs_dir / "Mine.pptx").write_bytes(deck("Mine"))
        self.assertEqual(self.run_ticks(), [])
        self.assertEqual(self.designs_files(), ["Mine.pptx"])

    def test_missing_folder_is_not_an_error(self):
        self.set["downloads_dir"] = str(self.tmp / "nope")
        self.assertEqual(self.run_ticks(), [])
        self.assertFalse(self.w.status()["exists"])


class MoveOrCopyTest(WatcherTestCase):
    def test_move_removes_the_original_after_the_copy_is_complete(self):
        self.start()
        data = deck("M")
        (self.dl / "M.pptx").write_bytes(data)
        self.run_ticks()
        self.assertEqual((self.designs_dir / "M.pptx").read_bytes(), data)
        self.assertFalse((self.dl / "M.pptx").exists())

    def test_copy_keeps_the_original(self):
        self.set["downloads_action"] = "copy"
        self.start()
        data = deck("C")
        (self.dl / "C.pptx").write_bytes(data)
        ev = self.run_ticks()
        self.assertEqual(len(ev), 1)  # added once, not again on every tick
        self.assertEqual(self.run_ticks(3), [])
        self.assertEqual((self.dl / "C.pptx").read_bytes(), data)
        self.assertEqual((self.designs_dir / "C.pptx").read_bytes(), data)
        side = designs.sidecar(self.designs_dir / "C.pptx")
        self.assertEqual((side["method"], side["action"], side["original_name"]), ("watcher", "copy", "C.pptx"))

    def test_a_name_that_is_taken_gets_a_number(self):
        self.designs_dir.mkdir()
        (self.designs_dir / "Same.pptx").write_bytes(deck("existing"))
        self.start()
        (self.dl / "Same.pptx").write_bytes(deck("new"))
        self.run_ticks()
        self.assertEqual(self.designs_files(), ["Same (2).pptx", "Same (2).pptx.source.json", "Same.pptx"])

    def test_the_new_design_shows_up_in_the_designs_list(self):
        self.start()
        (self.dl / "Listed.pptx").write_bytes(deck("Listed"))
        self.run_ticks()
        self.assertIn("Listed", [d["name"] for d in designs.all_designs() if d["source"] == "Designs folder"])

    def test_potx_becomes_a_normal_deck(self):
        self.start()
        z = zipfile.ZipFile(io.BytesIO(deck("T")))
        ct = z.read("[Content_Types].xml").decode()
        self.assertIn(watcher.PPTX_TYPE, ct)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as o:
            for i in z.infolist():
                o.writestr(i, ct.replace(watcher.PPTX_TYPE, watcher.POTX_TYPE).encode() if i.filename == "[Content_Types].xml" else z.read(i.filename))
        (self.dl / "Tpl.potx").write_bytes(buf.getvalue())
        ev = self.run_ticks()
        self.assertEqual([e["name"] for e in ev], ["Tpl"])
        out = (self.designs_dir / "Tpl.pptx").read_bytes()
        self.assertNotIn(watcher.POTX_TYPE, zipfile.ZipFile(io.BytesIO(out)).read("[Content_Types].xml").decode())
        self.assertTrue(ti.is_pptx(out))
        self.assertFalse((self.dl / "Tpl.potx").exists())


class SourceTest(WatcherTestCase):
    def test_zone_identifier_text_is_parsed(self):
        z = watcher.parse_zone(ZONE_GREATPPT)
        self.assertEqual(z["ReferrerUrl"], "https://www.greatppt.com/downloads/padel-powerpoint-template/")
        self.assertTrue(z["HostUrl"].startswith("https://www.greatppt.com/"))
        self.assertEqual(watcher.parse_zone(""), {})
        self.assertEqual(watcher.parse_zone("garbage\nZoneId=3"), {})

    def test_site_and_credit_from_the_origin(self):
        s = watcher.source_of(watcher.parse_zone(ZONE_GREATPPT))
        self.assertEqual((s["kind"], s["site"], s["attribution"]), ("greatppt", "GreatPPT", "Template by GreatPPT (free licence)."))
        sg = watcher.source_of({"HostUrl": "https://slidesgo.com/theme/abstract-art/download", "ReferrerUrl": ""})
        self.assertEqual((sg["kind"], sg["site"]), ("slidesgo", "Slidesgo"))
        self.assertIn("credits slide", sg["attribution"])
        sc = watcher.source_of({"ReferrerUrl": "https://www.slidescarnival.com/x/", "HostUrl": "https://www.slidescarnival.com/download/a/b/pptx"})
        self.assertEqual(sc["kind"], "slidescarnival")
        # the file's own address ends in .pptx: still read by its host
        self.assertEqual(watcher.source_of({"HostUrl": "https://cdn.greatppt.com/files/Padel.pptx"})["kind"], "greatppt")
        # the page wins over the file server
        both = watcher.source_of({"ReferrerUrl": "https://slidesgo.com/theme/x", "HostUrl": "https://storage.example.test/x.pptx"})
        self.assertEqual(both["kind"], "slidesgo")
        other = watcher.source_of({"HostUrl": "https://files.example.test/deck.pptx"})
        self.assertEqual((other["kind"], other["site"], other["attribution"]), ("", "files.example.test", ""))
        self.assertEqual(watcher.source_of({})["site"], "")
        self.assertEqual(watcher.source_of({"HostUrl": "file:///c:/x.pptx"})["site"], "")

    def test_greatppt_download_gets_its_credit_and_a_toast_text(self):
        self.zone = ZONE_GREATPPT
        self.start()
        (self.dl / "Padel Presentation.pptx").write_bytes(deck("Padel"))
        ev = self.run_ticks()
        self.assertEqual(ev[0]["message"], "Added 'Padel Presentation' from GreatPPT to your designs")
        self.assertEqual(ev[0]["kind"], "design_added")
        side = designs.sidecar(self.designs_dir / "Padel Presentation.pptx")
        self.assertEqual((side["site"], side["attribution"]), ("GreatPPT", "Template by GreatPPT (free licence)."))
        self.assertTrue(side["source_url"].startswith("https://www.greatppt.com/downloads/padel"))
        d = next(x for x in designs.all_designs() if x["source"] == "Designs folder")
        self.assertEqual(d["credit"], "Template by GreatPPT (free licence).")

    def test_unknown_origin_still_works_without_a_credit(self):
        self.start()
        (self.dl / "Mystery.pptx").write_bytes(deck("Mystery"))
        ev = self.run_ticks()
        self.assertEqual(ev[0]["message"], "Added 'Mystery' to your designs")
        self.assertEqual(designs.sidecar(self.designs_dir / "Mystery.pptx")["attribution"], "")


@unittest.skipUnless(sys.platform == "win32", "alternate data streams are a Windows feature")
class RealStreamTest(unittest.TestCase):
    def test_the_real_zone_identifier_stream_is_read(self):
        tmp = Path(tempfile.mkdtemp(dir=Path(__file__).parent))
        self.addCleanup(shutil.rmtree, tmp, True)
        f = tmp / "Real.pptx"
        f.write_bytes(b"x")
        try:
            with open(str(f) + ":Zone.Identifier", "w", encoding="utf-8") as z:
                z.write(ZONE_GREATPPT)
        except OSError:
            self.skipTest("this drive has no alternate data streams")
        self.assertEqual(watcher.source_of(watcher.parse_zone(watcher._read_ads(f)))["kind"], "greatppt")
        self.assertEqual(watcher._read_ads(tmp / "none.pptx"), "")


class SettingsTest(unittest.TestCase):
    def test_defaults_and_flags(self):
        from app.config import DEFAULTS
        self.assertEqual((DEFAULTS["downloads_watch"], DEFAULTS["downloads_dir"], DEFAULTS["downloads_action"]), (True, "", "move"))
        for v, want in ((True, True), ("on", True), ("off", False), (False, False), ("false", False), ("true", True)):
            self.assertEqual(watcher._on(v), want, v)

    def test_downloads_folder_default_and_override(self):
        with mock.patch.object(watcher, "get_settings", return_value={"downloads_dir": ""}), mock.patch.object(watcher, "known_downloads", return_value=Path("X:/Dl")):
            self.assertEqual(watcher.folder(), Path("X:/Dl"))
        with mock.patch.object(watcher, "get_settings", return_value={"downloads_dir": "D:/Mine"}):
            self.assertEqual(watcher.folder(), Path("D:/Mine"))

    def test_known_downloads_is_a_real_folder_path(self):
        self.assertEqual(watcher.known_downloads().name.lower(), "downloads")

    def test_other_systems_use_home_downloads(self):
        with mock.patch.object(watcher.sys, "platform", "linux"):
            self.assertEqual(watcher.known_downloads(), Path.home() / "Downloads")


if __name__ == "__main__":
    unittest.main()
