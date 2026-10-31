"""Video summary: links, captions, chunking, summary validation, frames, cleanup, exports, several materials, clarify questions,
Claude-mode hand-off. Offline: yt-dlp, ffmpeg, the network and the AI are mocked."""
import asyncio
import io
import json
import shutil
import struct
import tempfile
import time
import unittest
import zlib
import zipfile
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import db, handoff, tugas, video
from app import google_api as g
from app.main import app
from app import llm

REAL_COMPLETE = llm.complete  # the hand-off tests need the real one, not the fake AI

VTT = """WEBVTT
Kind: captions
Language: en

00:00:01.000 --> 00:00:03.000 align:start position:0%
Hello and welcome<00:00:01.500><c> to the course</c>

00:00:03.000 --> 00:00:03.010 align:start position:0%
Hello and welcome to the course

00:00:03.010 --> 00:00:05.000 align:start position:0%
Hello and welcome to the course
today we talk about heat

00:00:05.000 --> 00:00:05.010 align:start position:0%
today we talk about heat

00:00:05.010 --> 00:00:08.000 align:start position:0%
today we talk about heat
exchangers &amp; pumps
"""


def png() -> bytes:
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))


def segs(n=40, step=10):
    return [[i * step, f"line {i} about heat exchangers and pumps in a refinery"] for i in range(n)]


def captions(vid, title=None, dur=400):
    return {"meta": {"title": title or f"Video {vid}", "channel": "Chan", "duration": dur, "language": "en", "url": "https://www.youtube.com/watch?v=" + vid},
            "lang": "en", "kind": "manual", "segments": segs(40)}


SINGLE = {"tldr": "Heat is moved.", "takeaways": [{"text": f"Point {i}", "t": 10 * i} for i in range(1, 6)],
          "sections": [{"title": "Intro", "start": 0, "notes": "- hello", "frame_at": 5}, {"title": "Pumps", "start": 100, "notes": "- pumps", "frame_at": 120},
                       {"title": "Wrap", "start": 300, "notes": "bye", "frame_at": None}]}
COMBINED = {"title": "Heat and pumps", "tldr": "Both sources agree.", "takeaways": [{"text": "A point", "src": "S1", "t": 30}, {"text": "B point", "src": "S2", "t": 60}],
            "sections": [{"title": "Theme one", "notes": "- one", "src": "S1", "start": 20, "also": [{"src": "S2", "t": 50}], "frame_at": 25},
                         {"title": "Theme two", "notes": "- two", "src": "S2", "start": 70, "also": [], "frame_at": 75}]}


class FakeAI:
    """Answers by the kind of question in the prompt; remembers every prompt."""

    def __init__(self, questions=None, single=None, combined=None):
        self.calls, self.questions = [], questions or []
        self.single, self.combined = single or SINGLE, combined or COMBINED

    async def __call__(self, prompt, system="", **kw):
        self.calls.append(prompt)
        if "genuinely need answers" in prompt:
            return json.dumps({"questions": self.questions})
        if "Write ONE combined" in prompt:
            return json.dumps(self.combined)
        if "Rewrite ONE section" in prompt:
            return json.dumps({"title": "New title", "notes": "- rewritten"})
        return json.dumps(self.single)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=Path(__file__).resolve().parent.parent)).resolve()
        self.settings = {"tugas_dir": str(self.tmp / "Tugas"), "ai_mode": "api", "llm_model": "m", "llm_api_key": "k"}
        for p in (mock.patch.object(video, "DIR", self.tmp / "video"), mock.patch.object(tugas, "get_settings", return_value=self.settings),
                  mock.patch.object(video, "get_settings", return_value=self.settings), mock.patch("app.llm.get_settings", return_value=self.settings),
                  mock.patch.object(g, "DIR", self.tmp / "google")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, g, "TRANSPORT", None)
        self.pid = db.create_project("Video tests")
        (self.tmp / "Tugas" / "Fisika").mkdir(parents=True)
        self.ai = FakeAI()
        for p in (mock.patch("app.llm.complete", self.ai), mock.patch.object(video, "ffmpeg_exe", return_value="ffmpeg"),
                  mock.patch.object(video, "_download", side_effect=self.fake_download), mock.patch.object(video, "extract_frame", side_effect=self.fake_extract),
                  mock.patch.object(video, "thumb", side_effect=lambda p: bytes((i * 13 + int(Path(p).stem.split("-")[1]) * 40 + int(Path(p).stem.split("-")[2][:2])) % 256 for i in range(256))),
                  mock.patch.object(video, "scene_times", return_value=[12.0, 90.0, 200.0])):
            p.start()
            self.addCleanup(p.stop)
        self.downloads = []

    def fake_download(self, video_id, folder, step=lambda s: None):
        folder.mkdir(parents=True, exist_ok=True)
        f = folder / "video.mp4"
        f.write_bytes(b"fakevideo")
        self.downloads.append(video_id)
        return f

    def fake_extract(self, v, t, out):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(png() + bytes([int(t) % 256, int(t) // 256]) * 8)  # a different picture per second
        return True

    def caps(self, bad=()):
        def f(video_id, step=lambda s: None):
            if video_id in bad:
                raise ValueError(video.NO_CAPTIONS)
            return captions(video_id)
        return mock.patch.object(video, "get_captions", side_effect=f)

    def draft(self, links="https://youtu.be/aaaaaaaaaaa", **kw) -> dict:
        body = {"url_text": links, "project_id": self.pid, "options": {"frames": "ai"}, **kw}
        return video.new(body)[0]

    def run_pipeline(self, vid):
        return asyncio.run(video.pipeline(vid))


class LinksTest(unittest.TestCase):
    def test_every_youtube_form(self):
        ok = ["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube.com/watch?v=dQw4w9WgXcQ&t=30s", "https://youtu.be/dQw4w9WgXcQ?t=5",
              "https://www.youtube.com/shorts/dQw4w9WgXcQ", "https://m.youtube.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/embed/dQw4w9WgXcQ",
              "https://www.youtube.com/live/dQw4w9WgXcQ?feature=share", "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabc"]
        for u in ok:
            self.assertEqual(video.parse_url(u), "dQw4w9WgXcQ", u)

    def test_playlists_and_other_links_are_refused_plainly(self):
        for u in ("https://www.youtube.com/playlist?list=PLabc123", "https://www.youtube.com/@somechannel"):
            with self.assertRaisesRegex(ValueError, "[Pp]laylist"):
                video.parse_url(u)
        for u in ("", "https://example.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/watch?v=short"):
            with self.assertRaises(ValueError):
                video.parse_url(u)

    def test_many_links_one_per_line_are_deduped_and_bad_ones_reported(self):
        ids, problems = video.parse_links("""My list:
        https://youtu.be/aaaaaaaaaaa
        https://www.youtube.com/watch?v=bbbbbbbbbbb
        https://youtu.be/aaaaaaaaaaa?t=9
        https://www.youtube.com/playlist?list=PL123
        """)
        self.assertEqual(ids, ["aaaaaaaaaaa", "bbbbbbbbbbb"])
        self.assertEqual(len(problems), 1)
        self.assertIn("Playlists", problems[0])


class CaptionsTest(unittest.TestCase):
    def test_vtt_rolling_duplicates_are_dropped(self):
        got = video.parse_vtt(VTT)
        self.assertEqual([x[1] for x in got], ["Hello and welcome to the course", "today we talk about heat", "exchangers & pumps"])
        self.assertEqual(got[0][0], 1.0)

    def test_json3(self):
        j = {"events": [{"tStartMs": 0, "segs": [{"utf8": "Hi "}, {"utf8": "there"}]}, {"tStartMs": 500, "segs": [{"utf8": "\n"}]},
                        {"tStartMs": 2500, "segs": [{"utf8": "Second"}]}, {"tStartMs": 4000}]}
        self.assertEqual(video.parse_json3(json.dumps(j)), [[0.0, "Hi there"], [2.5, "Second"]])
        self.assertEqual(video.parse_json3("not json"), [])

    def test_caption_choice_prefers_manual_then_the_videos_own_language(self):
        self.assertEqual(video.pick_caption({"language": "en", "subtitles": {"en": [1], "fr": [1]}, "automatic_captions": {"en-orig": [1]}}), ("en", "manual"))
        self.assertEqual(video.pick_caption({"language": "id", "subtitles": {"en": [1]}, "automatic_captions": {"id-orig": [1], "en": [1]}}), ("id-orig", "auto"))
        self.assertIsNone(video.pick_caption({"language": "en", "subtitles": {}, "automatic_captions": {}}))

    def test_no_captions_gives_the_plain_message(self):
        with mock.patch.object(video, "_extract_info", return_value={"title": "T", "duration": 100, "subtitles": {}, "automatic_captions": {}}):
            with self.assertRaisesRegex(ValueError, "no captions"):
                video.get_captions("aaaaaaaaaaa")

    def test_private_video_message(self):
        self.assertIn("private", video.friendly_error("ERROR: Private video. Sign in if you've been granted access"))

    def test_chunks_keep_markers_and_cover_everything(self):
        ch = video.chunk_transcript(segs(100), 1500)
        self.assertGreater(len(ch), 2)
        self.assertTrue(ch[0]["text"].startswith("[00:00]"))
        self.assertEqual(sum(c["text"].count("\n") + 1 for c in ch), len(video.transcript_lines(segs(100))))


class FfmpegTest(unittest.TestCase):
    def test_scene_times_parse_showinfo_and_keep_a_gap(self):
        class P:
            returncode, stdout = 0, b""
            stderr = b"n:0 pts_time:12.5 x\n n:1 pts_time:14 x\n n:2 pts_time:40.2 x"
        with mock.patch.object(video, "_run", return_value=P), mock.patch.object(video, "ffmpeg_exe", return_value="ffmpeg"):
            self.assertEqual(video.scene_times("v.mp4"), [12.5, 40.2])

    def test_ffmpeg_comes_from_the_package_or_the_path(self):
        with mock.patch.dict("sys.modules", {"imageio_ffmpeg": None}), mock.patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            self.assertEqual(video.ffmpeg_exe(), "/usr/bin/ffmpeg")
            with mock.patch("shutil.which", return_value=None):
                with self.assertRaisesRegex(ValueError, "ffmpeg"):
                    video.ffmpeg_exe()
        self.assertTrue(video.ffmpeg_exe())


class SummaryTest(Base):
    def test_validation_clamps_sorts_and_drops_frames_outside_their_section(self):
        raw = {"tldr": "x", "takeaways": [{"text": "a", "t": 99999}], "sections": [
            {"title": "B", "start": 100, "notes": "n", "frame_at": 50}, {"title": "A", "start": "0", "notes": "n", "frame_at": 10}, {"title": "", "start": 5}]}
        v = video.validate_summary(raw, 300)
        self.assertEqual([s["title"] for s in v["sections"]], ["A", "B"])
        self.assertEqual(v["takeaways"][0]["t"], 299)
        self.assertEqual(v["sections"][0]["frame_at"], 10)
        self.assertIsNone(v["sections"][1]["frame_at"])
        for bad in ({}, {"tldr": "x", "sections": []}, {"sections": [{"title": "a", "start": 0}], "tldr": ""}, []):
            with self.assertRaises(ValueError):
                video.validate_summary(bad, 300)

    def test_invalid_json_is_retried_once(self):
        answers = ["not json at all", json.dumps(SINGLE)]
        calls = []

        async def complete(prompt, system="", **kw):
            calls.append(prompt)
            return answers.pop(0)
        with mock.patch("app.llm.complete", complete):
            with self.caps():
                d = self.draft(options={"frames": "none", "ask": False})
                self.run_pipeline(d["id"])
        self.assertEqual(len(calls), 2)
        self.assertIn("could not be used", calls[1])
        self.assertEqual(video.load(d["id"])["step"], "review")

    def test_long_transcript_is_read_in_parts_then_merged(self):
        long = [[i * 10, "word " * 40] for i in range(400)]
        d = self.draft(options={"frames": "none", "ask": False})
        part = {"points": [{"text": "p", "t": 10}], "sections": [{"title": "S", "start": 10, "notes": "n", "frame_at": None}]}
        merged = {"tldr": "all", "takeaways": [{"text": "p", "t": 10}]}
        seen = []

        async def complete(prompt, system="", **kw):
            seen.append(prompt)
            if "Write the sections for this part" in prompt:
                return json.dumps({**part, "sections": [{**part["sections"][0], "start": 100 * len(seen)}]})
            return json.dumps(merged)
        got = captions("aaaaaaaaaaa", dur=4000)
        got["segments"] = long
        with mock.patch("app.llm.complete", complete), mock.patch.object(video, "get_captions", return_value=got):
            self.run_pipeline(d["id"])
        st = video.load(d["id"])
        self.assertGreaterEqual(len(seen), 3)
        self.assertIn("important points", seen[-1])
        self.assertGreater(len(st["summary"]["sections"]), 1)
        self.assertEqual(st["summary"]["tldr"], "all")

    def test_deep_links_in_the_markdown(self):
        with self.caps():
            d = self.draft(options={"frames": "none", "ask": False})
            self.run_pipeline(d["id"])
        md = video.to_markdown(video.load(d["id"]))
        self.assertIn("https://youtu.be/aaaaaaaaaaa?t=30", md)
        self.assertIn("[02:00]", video.to_markdown(video.load(d["id"])).replace("[01:40]", "[02:00]"))
        st = video.load(d["id"])
        st["options"]["links"] = False
        self.assertNotIn("youtu.be/aaaaaaaaaaa?t=", video.to_markdown(st))


class FramesTest(Base):
    def done(self, mode="ai", **o):
        with self.caps():
            d = self.draft(options={"frames": mode, "ask": False, **o})
            self.run_pipeline(d["id"])
        return video.load(d["id"])

    def test_ai_frames_land_under_their_sections_and_video_is_cached(self):
        st = self.done()
        secs = st["summary"]["sections"]
        self.assertEqual([len(s["frames"]) for s in secs], [1, 1, 0])
        self.assertEqual(secs[1]["frames"][0]["file"], "frame-02-00.jpg")
        self.assertTrue((self.tmp / "video" / st["id"] / "frames" / "frame-02-00.jpg").exists())
        self.assertTrue(st["video_cached"])
        self.assertTrue(video.find_video(self.tmp / "video" / st["id"] / "cache" / "m1"))

    def test_scene_and_interval_modes_and_the_cap(self):
        st = self.done("scenes", max_frames=2)
        self.assertEqual(sum(len(s["frames"]) for s in st["summary"]["sections"]), 2)
        st = self.done("interval", every=2, max_frames=10)
        self.assertEqual(sorted(f["t"] for s in st["summary"]["sections"] for f in s["frames"]), [3, 120, 240, 360])

    def test_plan_thins_evenly(self):
        self.assertEqual(video.thin(list(range(10)), 3), [0, 4, 9])
        self.assertEqual(video.thin([1, 2], 5), [1, 2])

    def test_ffmpeg_commands(self):
        c = video.frame_cmd("ff", "v.mp4", 12.5, "o.jpg")
        self.assertEqual(c[:2], ["ff", "-v"])
        self.assertIn("12.50", c)
        self.assertIn("scale='min(1280,iw)':-2", c)
        self.assertEqual(c[-1], "o.jpg")
        self.assertIn("select='gt(scene,0.3)',showinfo", video.scene_cmd("ff", "v.mp4")[6])

    def test_a_failing_frame_is_skipped_and_the_rest_stay(self):
        calls = []

        def flaky(v, t, out):
            calls.append(t)
            if t == 5:
                return False
            return self.fake_extract(v, t, out)
        with mock.patch.object(video, "extract_frame", side_effect=flaky):
            st = self.done()
        self.assertEqual(st["frames_skipped"], 1)
        self.assertEqual(sum(len(s["frames"]) for s in st["summary"]["sections"]), 1)

    def test_near_duplicates_and_black_frames_are_dropped(self):
        same = bytes([100 + (i % 7) for i in range(256)])
        with mock.patch.object(video, "thumb", side_effect=lambda p: same):
            st = self.done()
        self.assertEqual(sum(len(s["frames"]) for s in st["summary"]["sections"]), 1)
        self.assertTrue(video.is_blank(bytes([2] * 256)))
        self.assertFalse(video.is_blank(bytes(range(256))))

    def test_pick_another_moment_uses_the_cached_video(self):
        st = self.done()
        sec = st["summary"]["sections"][1]
        fr = video.pick_frame(st["id"], sec["id"], 150, sec["frames"][0]["id"])
        cur = video.load(st["id"])
        self.assertEqual(cur["summary"]["sections"][1]["frames"][0]["file"], "frame-02-30.jpg")
        self.assertFalse((self.tmp / "video" / st["id"] / "frames" / "frame-02-00.jpg").exists())
        self.assertEqual(fr["t"], 150)

    def test_the_video_is_deleted_on_finish_after_30_minutes_and_with_the_draft(self):
        st = self.done()
        cache = self.tmp / "video" / st["id"] / "cache"
        self.assertTrue(cache.exists())
        video.finish(st["id"])
        self.assertFalse(cache.exists())
        self.assertTrue((self.tmp / "video" / st["id"] / "frames").exists())  # the screenshots stay
        with self.assertRaisesRegex(ValueError, "removed to save space"):
            video.pick_frame(st["id"], st["summary"]["sections"][0]["id"], 10)
        # time runs out
        st = self.done()
        cur = video.load(st["id"])
        cur["video_expires"] = time.time() - 5
        video.save(cur)
        video.sweep()
        self.assertFalse((self.tmp / "video" / st["id"] / "cache").exists())
        self.assertFalse(video.load(st["id"])["video_cached"])
        # deleting the draft removes everything
        st = self.done()
        video.delete(st["id"])
        self.assertFalse((self.tmp / "video" / st["id"]).exists())

    def test_no_frames_mode_never_downloads(self):
        self.done("none")
        self.assertEqual(self.downloads, [])


class ExportTest(Base):
    def made(self):
        with self.caps():
            d = self.draft(options={"frames": "ai", "ask": False})
            self.run_pipeline(d["id"])
        return video.load(d["id"])

    def test_docx_has_headings_images_links_and_the_source(self):
        st = self.made()
        data = video.build_docx(st)
        z = zipfile.ZipFile(io.BytesIO(data))
        xml = z.read("word/document.xml").decode()
        for h in ("Summary", "Key takeaways", "Notes", "Intro", "Pumps"):
            self.assertIn(h, xml)
        self.assertEqual(xml.count('w:val="Heading2"'), 3)
        self.assertEqual(len([n for n in z.namelist() if n.startswith("word/media/")]), 2)
        rels = z.read("word/_rels/document.xml.rels").decode()
        self.assertIn("https://www.youtube.com/watch?v=aaaaaaaaaaa", rels)
        self.assertIn("https://youtu.be/aaaaaaaaaaa?t=100", rels)
        self.assertIn("Video aaaaaaaaaaa", xml)
        self.assertIn("Chan", xml)

    def test_removed_frames_are_not_exported(self):
        st = self.made()
        secs = [{"id": s["id"], "frames": [{"id": f["id"], "keep": False} for f in s["frames"]]} for s in st["summary"]["sections"]]
        video.patch(st["id"], {"sections": secs})
        z = zipfile.ZipFile(io.BytesIO(video.build_docx(video.load(st["id"]))))
        self.assertEqual([n for n in z.namelist() if n.startswith("word/media/")], [])

    def test_markdown_folder_layout(self):
        st = self.made()
        z = zipfile.ZipFile(io.BytesIO(video.build_zip(st)))
        names = z.namelist()
        self.assertIn("Video aaaaaaaaaaa/note.md", names)
        self.assertIn("Video aaaaaaaaaaa/frames/frame-02-00.jpg", names)
        note = z.read("Video aaaaaaaaaaa/note.md").decode()
        self.assertIn("![Frame at 02:00](frames/frame-02-00.jpg)", note)
        self.assertTrue(note.startswith("# Video aaaaaaaaaaa"))

    def test_saving_into_tugas_never_overwrites(self):
        st = self.made()
        a = video.save_to_tugas(st["id"], "Fisika", "docx")["path"]
        b = video.save_to_tugas(st["id"], "Fisika", "docx")["path"]
        self.assertEqual((a, b), ("Fisika/Video aaaaaaaaaaa.docx", "Fisika/Video aaaaaaaaaaa (2).docx"))
        f1 = video.save_to_tugas(st["id"], "Fisika", "md")["path"]
        f2 = video.save_to_tugas(st["id"], "Fisika", "md")["path"]
        self.assertEqual((f1, f2), ("Fisika/Video aaaaaaaaaaa", "Fisika/Video aaaaaaaaaaa (2)"))
        self.assertTrue((self.tmp / "Tugas" / "Fisika" / "Video aaaaaaaaaaa (2)" / "frames" / "frame-02-00.jpg").exists())
        with self.assertRaises(tugas.TugasError):
            video.save_to_tugas(st["id"], "Nope", "docx")

    def test_afra_document_is_created_once_and_updated(self):
        st = self.made()
        d1 = video.save_document(st["id"])
        d2 = video.save_document(st["id"])
        self.assertEqual(d1["doc_id"], d2["doc_id"])
        doc = db.get_document(d1["doc_id"])
        self.assertIn("https://youtu.be/aaaaaaaaaaa?t=", doc["content"])
        self.assertEqual(doc["meta"]["video"]["ids"], ["aaaaaaaaaaa"])

    def test_google_doc_needs_an_account_and_uploads_the_docx(self):
        st = self.made()
        with mock.patch.object(g, "accounts", return_value=[]):
            with self.assertRaisesRegex(ValueError, "Connect a Google account"):
                video.export_google_doc(st["id"])
        with mock.patch.object(g, "accounts", return_value=[{"email": "a@b.c"}]), mock.patch.object(g, "access_token", return_value=("a@b.c", "t")), \
                mock.patch.object(g, "ensure_folders", return_value={"decks": "D"}), \
                mock.patch.object(g, "upload", return_value={"id": "X", "name": "n", "webViewLink": "https://docs.google.com/document/d/X/edit"}) as up:
            r = video.export_google_doc(st["id"])
        self.assertEqual(r["id"], "X")
        self.assertEqual(up.call_args[0][2], g.MIME_DOCX)
        self.assertEqual(up.call_args[0][4], g.MIME_DOC)
        self.assertTrue(up.call_args[0][0].startswith(b"PK"))


class JobTest(Base):
    def test_job_saves_the_draft_and_resumes_where_it_stopped(self):
        d = self.draft(options={"frames": "ai", "ask": False})
        with self.caps() as cap, mock.patch("app.llm.complete", side_effect=ValueError("AI down")):
            with self.assertRaises(ValueError):
                self.run_pipeline(d["id"])
        mid = video.load(d["id"])
        self.assertEqual(mid["materials"][0]["status"], "ok")
        self.assertIsNone(mid["summary"])
        n = len(self.ai.calls)
        with mock.patch.object(video, "get_captions", side_effect=AssertionError("captions are already in the draft")):
            self.run_pipeline(d["id"])
        self.assertEqual(video.load(d["id"])["step"], "review")
        self.assertGreater(len(self.ai.calls), n)

    def test_start_runs_in_the_background_with_progress(self):
        d = self.draft(options={"frames": "none", "ask": False})
        with self.caps():
            j = video.start(d["id"])
            for _ in range(100):
                if video.job(j["job"])["state"] != "working":
                    break
                time.sleep(0.05)
        self.assertEqual(video.job(j["job"])["state"], "done")
        self.assertEqual(video.load(d["id"])["step"], "review")

    def test_a_caption_less_video_stops_with_the_plain_message(self):
        d = self.draft(options={"ask": False})
        with self.caps(bad=("aaaaaaaaaaa",)):
            with self.assertRaisesRegex(ValueError, "no captions"):
                self.run_pipeline(d["id"])

    def test_rewrite_one_section(self):
        with self.caps():
            d = self.draft(options={"frames": "none", "ask": False})
            self.run_pipeline(d["id"])
        r = asyncio.run(video.rewrite_section(d["id"], "s2", "shorter"))
        self.assertEqual(r["title"], "New title")
        self.assertEqual(video.load(d["id"])["summary"]["sections"][1]["notes"], "- rewritten")

    def test_reorder_and_edit(self):
        with self.caps():
            d = self.draft(options={"frames": "none", "ask": False})
            self.run_pipeline(d["id"])
        st = video.patch(d["id"], {"tldr": "mine", "sections": [{"id": "s3", "title": "Z"}, {"id": "s1"}]})
        self.assertEqual([s["id"] for s in st["summary"]["sections"]], ["s3", "s1"])
        self.assertEqual(st["summary"]["tldr"], "mine")


class ManyMaterialsTest(Base):
    LINKS = "https://youtu.be/aaaaaaaaaaa\nhttps://youtu.be/bbbbbbbbbbb\nhttps://youtu.be/aaaaaaaaaaa"

    def test_cap_and_dedupe(self):
        d = self.draft(self.LINKS)
        self.assertEqual([m["video_id"] for m in d["materials"]], ["aaaaaaaaaaa", "bbbbbbbbbbb"])
        links = "\n".join(f"https://youtu.be/{chr(97 + i) * 11}" for i in range(11))
        with self.assertRaisesRegex(ValueError, "up to 10"):
            video.new({"url_text": links, "project_id": self.pid})
        with self.assertRaisesRegex(ValueError, "[Pp]laylist"):
            video.new({"url_text": "https://www.youtube.com/playlist?list=PL1", "project_id": self.pid})

    def test_documents_and_uploads_are_materials(self):
        doc = db.create_document(self.pid, "My notes", "doc", "Some notes about heat.", {})
        d = video.new({"url_text": "https://youtu.be/aaaaaaaaaaa", "doc_ids": [doc["id"]], "project_id": self.pid}, [("extra.md", b"# Extra\nmore text here")])[0]
        self.assertEqual([m["kind"] for m in d["materials"]], ["video", "doc", "doc"])
        self.assertEqual(d["materials"][2]["label"], "extra.md")
        with self.assertRaisesRegex(ValueError, "upload a"):
            video.new({"project_id": self.pid}, [("x.exe", b"x")])

    def test_one_failing_video_does_not_stop_the_others(self):
        d = self.draft(self.LINKS, options={"frames": "ai", "ask": False})
        with self.caps(bad=("aaaaaaaaaaa",)):
            self.run_pipeline(d["id"])
        st = video.load(d["id"])
        a, b = st["materials"]
        self.assertEqual((a["status"], b["status"]), ("failed", "ok"))
        self.assertIn("no captions", a["note"])
        self.assertIn("Captions found", b["note"])
        self.assertEqual(st["step"], "review")
        self.assertEqual(self.downloads, ["bbbbbbbbbbb"])
        self.assertNotIn("Video aaaaaaaaaaa", video.to_markdown(st))
        # everything failing is an error that lists every reason
        d2 = self.draft(self.LINKS)
        with self.caps(bad=("aaaaaaaaaaa", "bbbbbbbbbbb")):
            with self.assertRaisesRegex(ValueError, "Nothing could be read"):
                self.run_pipeline(d2["id"])

    def combined(self, **o):
        d = self.draft(self.LINKS, options={"frames": "ai", "ask": False, **o})
        with self.caps():
            self.run_pipeline(d["id"])
        return video.load(d["id"])

    def test_combined_gives_one_summary_grouped_by_theme_with_source_tags_and_right_frames(self):
        st = self.combined()
        self.assertEqual(st["mode"], "combined")
        self.assertEqual([s["title"] for s in st["summary"]["sections"]], ["Theme one", "Theme two"])
        self.assertEqual(st["meta"]["title"], "Heat and pumps")
        self.assertEqual([s["m"] for s in st["summary"]["sections"]], ["m1", "m2"])
        self.assertEqual([r["m"] for r in st["summary"]["sections"][0]["refs"]], ["m1", "m2"])
        self.assertEqual([f["m"] for s in st["summary"]["sections"] for f in s["frames"]], ["m1", "m2"])
        self.assertEqual(sorted(self.downloads), ["aaaaaaaaaaa", "bbbbbbbbbbb"])
        md = video.to_markdown(st)
        self.assertIn("[Video aaaaaaaaaaa 00:30](https://youtu.be/aaaaaaaaaaa?t=30)", md)
        self.assertIn("[Video bbbbbbbbbbb 01:00](https://youtu.be/bbbbbbbbbbb?t=60)", md)
        self.assertIn("## Sources", md)
        self.assertIn("1. Video aaaaaaaaaaa (Chan) – https://www.youtube.com/watch?v=aaaaaaaaaaa", md)
        # one prompt with both transcripts
        self.assertEqual(len([c for c in self.ai.calls if "Write ONE combined" in c]), 1)
        # exports work for the combined result
        z = zipfile.ZipFile(io.BytesIO(video.build_docx(st)))
        self.assertEqual(len([n for n in z.namelist() if n.startswith("word/media/")]), 2)
        self.assertIn("Sources", z.read("word/document.xml").decode())
        self.assertIn("Heat and pumps/note.md", zipfile.ZipFile(io.BytesIO(video.build_zip(st))).namelist())
        self.assertEqual(db.get_document(video.save_document(st["id"])["doc_id"])["meta"]["video"]["ids"], ["aaaaaaaaaaa", "bbbbbbbbbbb"])

    def test_long_inputs_are_digested_per_material_first(self):
        big = captions("x")
        big["segments"] = [[i * 10, "word " * 60] for i in range(400)]
        d = self.draft(self.LINKS, options={"frames": "none", "ask": False})
        with mock.patch.object(video, "get_captions", return_value=big):
            with mock.patch.object(video, "m_text", return_value="x" * 60000):
                self.run_pipeline(d["id"])
        digests = [c for c in self.ai.calls if "Write ONE combined" not in c and "genuinely need" not in c]
        self.assertGreaterEqual(len(digests), 2)
        self.assertTrue(any("its own summary" in c for c in self.ai.calls if "Write ONE combined" in c))

    def test_separate_makes_one_draft_per_material(self):
        ds = video.new({"url_text": self.LINKS, "project_id": self.pid, "mode": "separate", "options": {"frames": "none"}})
        self.assertEqual([len(d["materials"]) for d in ds], [1, 1])
        self.assertTrue(all(d["mode"] == "separate" and d["clarify_done"] for d in ds))
        with self.caps():
            for d in ds:
                self.run_pipeline(d["id"])
        titles = [video.load(d["id"])["meta"]["title"] for d in ds]
        self.assertEqual(titles, ["Video aaaaaaaaaaa", "Video bbbbbbbbbbb"])
        self.assertNotIn("## Sources", video.to_markdown(video.load(ds[0]["id"])))

    def test_batch_runs_the_drafts_one_after_the_other_and_a_failure_stays_local(self):
        ds = video.new({"url_text": self.LINKS, "project_id": self.pid, "mode": "separate", "options": {"frames": "none"}})
        with self.caps(bad=("aaaaaaaaaaa",)):
            j = video.start(ds[0]["id"], batch=[ds[1]["id"]])
            for _ in range(200):
                if all(video.job(x)["state"] not in ("working", "queued") for x in j["jobs"].values()):
                    break
                time.sleep(0.05)
        self.assertEqual(video.job(j["jobs"][ds[0]["id"]])["state"], "error")
        self.assertIn("no captions", video.job(j["jobs"][ds[0]["id"]])["error"])
        self.assertEqual(video.job(j["jobs"][ds[1]["id"]])["state"], "done")


class ClarifyTest(Base):
    def test_zero_questions_means_no_pause(self):
        d = self.draft(options={"frames": "none"})
        with self.caps():
            self.assertIsNone(self.run_pipeline(d["id"]))
        st = video.load(d["id"])
        self.assertEqual(st["step"], "review")
        self.assertTrue(st["clarify_done"])
        self.assertEqual(sum("genuinely need answers" in c for c in self.ai.calls), 1)

    def test_questions_pause_the_job_and_the_answers_reach_the_summary_prompt(self):
        self.ai.questions = [{"question": "What are the notes for?", "choices": ["Exam", "A talk", "Research", "x", "too many"]},
                             {"question": "Skip the intro?", "choices": ["Yes", "No"]}]
        d = self.draft(options={"frames": "none"})
        with self.caps():
            self.assertEqual(self.run_pipeline(d["id"]), "questions")
            st = video.load(d["id"])
            self.assertEqual(st["step"], "questions")
            self.assertIsNone(st["summary"])
            self.assertEqual([q["id"] for q in st["questions"]], ["q1", "q2"])
            self.assertEqual(st["questions"][0]["choices"], ["Exam", "A talk", "Research", "x"])
            self.assertEqual(len(self.ai.calls), 1)  # nothing expensive ran yet
            video.answer(d["id"], {"q1": "Exam", "q2": "You decide", "q9": "ignored"})
            self.assertEqual(list(video.load(d["id"])["answers"]), ["q1"])
            self.assertIsNone(self.run_pipeline(d["id"]))
        self.assertEqual(sum("genuinely need answers" in c for c in self.ai.calls), 1)  # one round only
        summary_prompt = self.ai.calls[-1]
        self.assertIn("What are the notes for? -> Exam", summary_prompt)
        self.assertEqual(video.load(d["id"])["step"], "review")

    def test_skipping_all_questions_is_allowed(self):
        self.ai.questions = [{"question": "Focus?", "choices": ["A", "B"]}]
        d = self.draft(options={"frames": "none"})
        with self.caps():
            self.run_pipeline(d["id"])
            video.answer(d["id"], {})
            self.run_pipeline(d["id"])
        self.assertNotIn("What the reader said", self.ai.calls[-1])
        self.assertEqual(video.load(d["id"])["step"], "review")

    def test_the_job_state_is_questions_while_waiting(self):
        self.ai.questions = [{"question": "Focus?", "choices": ["A", "B"]}]
        d = self.draft(options={"frames": "none"})
        with self.caps():
            j = video.start(d["id"])
            for _ in range(100):
                if video.job(j["job"])["state"] != "working":
                    break
                time.sleep(0.05)
        self.assertEqual(video.job(j["job"])["state"], "questions")
        self.assertIn("Waiting for your answers", video.job(j["job"])["step"])

    def test_ask_off_skips_clarify(self):
        d = self.draft(options={"frames": "none", "ask": False})
        with self.caps():
            self.run_pipeline(d["id"])
        self.assertFalse(any("genuinely need answers" in c for c in self.ai.calls))

    def test_claude_mode_clarify_is_a_handoff_task(self):
        self.settings["ai_mode"] = "claude"
        p = mock.patch("app.llm.complete", REAL_COMPLETE)
        p.start()
        self.addCleanup(p.stop)
        d = self.draft(options={"frames": "none"})
        st = video.load(d["id"])
        st["materials"][0].update(status="ok", meta={"title": "T", "channel": "C", "duration": 400}, segments=segs(40))
        video.save(st)

        async def go():
            token = handoff.CURRENT.set("tk1")
            try:
                t = asyncio.create_task(video.clarify(video.load(d["id"])))
                for _ in range(50):
                    if "tk1" in handoff.TASKS:
                        break
                    await asyncio.sleep(0.05)
                task = await handoff.get("tk1")
                self.assertTrue(task["json"])
                self.assertIn("genuinely need answers", task["prompt"])
                handoff.finish("tk1", json.dumps({"questions": [{"question": "Why?", "choices": ["a", "b"]}]}))
                return await t
            finally:
                handoff.CURRENT.reset(token)
        qs = asyncio.run(go())
        self.assertEqual(qs[0]["question"], "Why?")
        handoff.TASKS.pop("tk1", None)

    def test_claude_mode_text_steps_of_the_summary_use_the_handoff(self):
        self.settings["ai_mode"] = "claude"
        p = mock.patch("app.llm.complete", REAL_COMPLETE)
        p.start()
        self.addCleanup(p.stop)
        d = self.draft(options={"frames": "none", "ask": False})
        st = video.load(d["id"])
        st["materials"][0].update(status="ok", meta={"title": "T", "channel": "C", "duration": 400}, segments=segs(40))
        video.save(st)

        async def go():
            token = handoff.CURRENT.set("tk2")
            try:
                t = asyncio.create_task(video.summarize(video.load(d["id"])))
                for _ in range(50):
                    if "tk2" in handoff.TASKS:
                        break
                    await asyncio.sleep(0.05)
                await handoff.get("tk2")
                handoff.finish("tk2", json.dumps(SINGLE))
                return await t
            finally:
                handoff.CURRENT.reset(token)
        got = asyncio.run(go())
        self.assertEqual(len(got["sections"]), 3)
        handoff.TASKS.pop("tk2", None)


class RoutesTest(Base):
    def test_routes_end_to_end(self):
        c = TestClient(app)
        with mock.patch("app.video.DIR", self.tmp / "video"), self.caps():
            r = c.post("/api/video", json={"url_text": "https://youtu.be/aaaaaaaaaaa", "project_id": self.pid, "options": {"frames": "none", "ask": False}})
            self.assertEqual(r.status_code, 200, r.text)
            vid = r.json()["drafts"][0]["id"]
            j = c.post(f"/api/video/{vid}/start", json={}).json()["job"]
            for _ in range(100):
                if c.get(f"/api/video/jobs/{j}").json()["state"] != "working":
                    break
                time.sleep(0.05)
            st = c.get(f"/api/video/{vid}").json()
            self.assertEqual(st["step"], "review")
            self.assertNotIn("segments", json.dumps(st))
            self.assertEqual(c.get(f"/api/video/{vid}/export?fmt=docx").content[:2], b"PK")
            self.assertEqual(c.get(f"/api/video/{vid}/export?fmt=zip").content[:2], b"PK")
            self.assertEqual(c.post("/api/video", json={"url_text": "nope", "project_id": self.pid}).status_code, 422)
            r = c.post("/api/video", data={"data": json.dumps({"project_id": self.pid, "options": {"frames": "none"}})}, files=[("files", ("n.md", b"# Notes\ntext here", "text/markdown"))])
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["drafts"][0]["materials"][0]["kind"], "doc")
            self.assertEqual(c.delete(f"/api/video/{vid}").status_code, 200)


if __name__ == "__main__":
    unittest.main()
