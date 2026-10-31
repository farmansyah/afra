"""The PC relay (app/relay.py) and the pasted-transcript way out (video.add_transcript). Offline: the "PC" is a thread."""
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import db, relay, video
from app.main import app

SETTINGS = {"relay_token": "secret-token", "relay_url": ""}
H = {"x-relay-token": "secret-token"}


def captions(vid):
    return {"meta": {"title": "T", "channel": "C", "duration": 100, "language": "en", "url": "https://www.youtube.com/watch?v=" + vid},
            "lang": "en", "kind": "auto", "segments": [[0, "hello there everyone"], [5, "today we talk about heat"]]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=Path(__file__).resolve().parent.parent)).resolve()
        self.settings = dict(SETTINGS)
        for p in (mock.patch.object(relay, "DIR", self.tmp / "relay"), mock.patch.object(relay, "get_settings", side_effect=lambda: self.settings),
                  mock.patch.object(video, "DIR", self.tmp / "video"), mock.patch.object(relay, "PICKUP_SECS", 2)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        relay.REQS.clear()
        relay.seen()
        self.stop = False
        self.addCleanup(setattr, self, "stop", True)

    def pc(self, fn):
        """A fake PC: answers every request with fn(job)."""
        def run():
            while not self.stop:
                relay.seen()
                job = relay.claim()
                if job:
                    fn(job)
                time.sleep(0.05)
        t = threading.Thread(target=run, daemon=True)
        t.start()


class AskTest(Base):
    def test_captions_round_trip(self):
        self.pc(lambda j: relay.finish(j["id"], captions(j["video_id"])))
        got = relay.ask("captions", "dQw4w9WgXcQ")
        self.assertEqual(got["meta"]["title"], "T")
        self.assertEqual(relay.REQS, {})

    def test_progress_is_passed_on(self):
        seen = []

        def pc(j):
            relay.progress(j["id"], "Reading the captions…")
            time.sleep(1.2)
            relay.finish(j["id"], captions("x"))
        self.pc(pc)
        relay.ask("captions", "dQw4w9WgXcQ", step=seen.append)
        self.assertTrue(any("Reading the captions" in s for s in seen))

    def test_the_pc_reports_a_problem(self):
        self.pc(lambda j: relay.finish(j["id"], error="This video is private."))
        with self.assertRaisesRegex(ValueError, "private"):
            relay.ask("captions", "dQw4w9WgXcQ")

    def test_video_file_is_moved_into_the_folder(self):
        def pc(j):
            p = relay.receive_path(j["id"], "mp4")
            p.write_bytes(b"movie")
            relay.finish(j["id"], data={"size": 5})
        self.pc(pc)
        out = relay.ask("video", "dQw4w9WgXcQ", self.tmp / "cache")
        self.assertEqual(out, self.tmp / "cache" / "video.mp4")
        self.assertEqual(out.read_bytes(), b"movie")
        self.assertFalse((self.tmp / "relay").exists() and any((self.tmp / "relay").iterdir()))

    def test_pc_offline(self):
        relay._SEEN[0] = 0
        with self.assertRaisesRegex(ValueError, "isn't connected"):
            relay.ask("captions", "dQw4w9WgXcQ")

    def test_nobody_picks_it_up(self):
        t = time.time()
        with self.assertRaisesRegex(ValueError, "isn't connected"):
            relay.ask("captions", "dQw4w9WgXcQ")
        self.assertLess(time.time() - t, 6)

    def test_a_pc_is_never_a_server(self):
        self.settings["relay_url"] = "https://afra.farmansyah.cloud"
        self.assertFalse(relay.enabled())
        with self.assertRaisesRegex(ValueError, "isn't set up"):
            relay.ask("captions", "dQw4w9WgXcQ")
        self.settings.update(relay_url="", relay_token="")
        self.assertFalse(relay.enabled())

    def test_token_check(self):
        self.assertTrue(relay.token_ok("secret-token"))
        self.assertFalse(relay.token_ok("nope"))
        self.assertFalse(relay.token_ok(None))
        self.assertFalse(relay.token_ok("sécret"))
        self.settings["relay_token"] = ""
        self.assertFalse(relay.token_ok(""))


class FallbackTest(Base):
    BLOCK = video.BLOCKED + ", so it blocked A.F.R.A."

    def test_a_blocked_server_asks_the_pc(self):
        with mock.patch.object(video, "local_captions", side_effect=ValueError(self.BLOCK)), mock.patch.object(relay, "ask", return_value=captions("x")) as ask:
            self.assertEqual(video.get_captions("dQw4w9WgXcQ")["lang"], "en")
        ask.assert_called_once()

    def test_other_errors_are_not_relayed(self):
        with mock.patch.object(video, "local_captions", side_effect=ValueError("This video is private.")), mock.patch.object(relay, "ask") as ask:
            with self.assertRaisesRegex(ValueError, "private"):
                video.get_captions("dQw4w9WgXcQ")
        ask.assert_not_called()

    def test_without_a_relay_the_message_is_unchanged(self):
        self.settings["relay_token"] = ""
        with mock.patch.object(video, "local_captions", side_effect=ValueError(self.BLOCK)):
            with self.assertRaises(ValueError) as e:
                video.get_captions("dQw4w9WgXcQ")
        self.assertEqual(str(e.exception), self.BLOCK)

    def test_when_the_pc_cannot_help_both_reasons_show(self):
        relay._SEEN[0] = 0
        with mock.patch.object(video, "local_captions", side_effect=ValueError(self.BLOCK)):
            with self.assertRaises(ValueError) as e:
                video.get_captions("dQw4w9WgXcQ")
        self.assertIn("robot", str(e.exception))
        self.assertIn("isn't connected", str(e.exception))

    def test_download_goes_through_the_pc_too(self):
        with mock.patch.object(video, "local_download", side_effect=ValueError(self.BLOCK)), mock.patch.object(relay, "ask", return_value=self.tmp / "v.mp4") as ask:
            self.assertEqual(video._download("dQw4w9WgXcQ", self.tmp / "c"), self.tmp / "v.mp4")
        self.assertEqual(ask.call_args.args[:2], ("video", "dQw4w9WgXcQ"))


class RoutesTest(Base):
    def setUp(self):
        super().setUp()
        self.c = TestClient(app)

    def test_every_pc_route_needs_the_token(self):
        for method, url in (("get", "/api/relay/next?wait=0"), ("post", "/api/relay/abc/progress"), ("post", "/api/relay/abc/result"), ("put", "/api/relay/abc/file")):
            r = getattr(self.c, method)(url, headers={"x-relay-token": "wrong"}, **({"json": {}} if method == "post" else {}))
            self.assertEqual(r.status_code, 403, url)

    def test_next_is_empty_then_hands_out_a_request_once(self):
        self.assertEqual(self.c.get("/api/relay/next?wait=0", headers=H).json(), {})
        relay.REQS["r1"] = {"id": "r1", "kind": "captions", "video_id": "dQw4w9WgXcQ", "state": "pending", "step": "", "created": time.time(), "claimed": 0, "error": "", "data": None}
        self.assertEqual(self.c.get("/api/relay/next?wait=0", headers=H).json()["id"], "r1")
        self.assertEqual(self.c.get("/api/relay/next?wait=0", headers=H).json(), {})

    def test_a_real_round_trip_over_http(self):
        out = {}

        def asker():
            try:
                out["got"] = relay.ask("video", "dQw4w9WgXcQ", self.tmp / "cache")
            except ValueError as e:
                out["err"] = str(e)
        t = threading.Thread(target=asker)
        t.start()
        job = {}
        for _ in range(50):
            job = self.c.get("/api/relay/next?wait=0", headers=H).json()
            if job:
                break
            time.sleep(0.1)
        self.assertEqual(job["kind"], "video")
        r = self.c.put(f"/api/relay/{job['id']}/file?ext=mp4", headers=H, content=b"x" * 5000)
        self.assertEqual(r.json()["size"], 5000)
        t.join(10)
        self.assertEqual(out["got"].read_bytes(), b"x" * 5000)

    def test_status_is_open_but_has_no_secrets(self):
        r = self.c.get("/api/relay/status").json()
        self.assertTrue(r["enabled"])
        self.assertNotIn("secret-token", str(r))


class TranscriptTest(Base):
    def test_youtube_panel_format(self):
        segs = video.parse_pasted("0:00\nWelcome to the lecture\n0:12\nToday we cover heat\nand how it moves\n1:02:03\nThe end of it")
        self.assertEqual(segs, [[0, "Welcome to the lecture"], [12, "Today we cover heat and how it moves"], [3723, "The end of it"]])

    def test_time_and_words_on_one_line(self):
        self.assertEqual(video.parse_pasted("0:05 hello there\n0:09 and more"), [[5, "hello there"], [9, "and more"]])

    def test_vtt_and_srt(self):
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nhello world\n\n00:00:04.000 --> 00:00:06.000\nsecond line here\n"
        srt = "1\n00:00:01,000 --> 00:00:03,000\nhello world\n\n2\n00:00:04,500 --> 00:00:06,000\nsecond line here\n"
        self.assertEqual([s[1] for s in video.parse_pasted(vtt)], ["hello world", "second line here"])
        self.assertEqual(video.parse_pasted(srt), [[1.0, "hello world"], [4.5, "second line here"]])

    def test_plain_text_without_times(self):
        self.assertEqual(video.parse_pasted("just some words\nmore words"), [[0, "just some words more words"]])

    def make(self):
        pid = db.create_project("Relay tests")
        return video.new({"project_id": pid, "url_text": "https://youtu.be/dQw4w9WgXcQ", "options": {"ask": False}})[0]

    def test_a_pasted_transcript_makes_the_source_readable(self):
        st = self.make()
        m = st["materials"][0]
        text = "0:00\nWelcome to the lecture on heat\n0:12\nToday we cover how heat moves around\n"
        with mock.patch.object(video, "oembed", return_value=("Heat lecture", "Prof")):
            out = video.add_transcript(st["id"], m["id"], text)
        m = out["materials"][0]
        self.assertEqual((m["status"], m["label"], m["manual"]), ("ok", "Heat lecture", True))
        self.assertEqual(m["meta"]["duration"], 17)
        self.assertEqual(len(video.load(st["id"])["materials"][0]["segments"]), 2)

    def test_junk_is_refused(self):
        st = self.make()
        with self.assertRaisesRegex(ValueError, "doesn't look like a transcript"):
            video.add_transcript(st["id"], st["materials"][0]["id"], "hi")

    def test_route(self):
        st = self.make()
        with mock.patch.object(video, "oembed", return_value=("", "")):
            r = TestClient(app).post(f"/api/video/{st['id']}/transcript", json={"m": st["materials"][0]["id"], "text": "0:00\n" + "words " * 20})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["materials"][0]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
