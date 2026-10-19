"""Create into PPT: the mockup contract, every layout in the .pptx, the checks, saved drafts, the essay approval gate,
Google Doc export, uploads analysis and the PDF design reader. Offline: the AI and Google are mocked."""
import asyncio
import io
import json
import re
import shutil
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import httpx
from fastapi.testclient import TestClient

from app import db, designs, google_api as g, gslides, pdf_design, simple_pdf, tugas, tugas_decks, wizard
from app.main import app

ALL_LAYOUTS = {
    "title": {"title": "Deck title", "subtitle": "A subtitle"},
    "section": {"title": "Part one"},
    "content": {"title": "Bullets slide", "bullets": ["First bullet", "Second bullet"], "source": "Lee (2020)"},
    "two_column": {"title": "Compare", "left": {"heading": "Before", "bullets": ["old way"]}, "right": {"heading": "After", "bullets": ["new way"]}},
    "figure": {"title": "A figure", "figure": {"description": "the plant layout", "caption": "Figure 1"}},
    "image_left": {"title": "Image left", "bullets": ["beside the picture"], "figure": {"description": "left picture"}},
    "image_right": {"title": "Image right", "bullets": ["next to it"], "figure": {"description": "right picture", "caption": "Cap right"}},
    "table": {"title": "Results", "table": {"header": ["Unit", "Yield"], "rows": [["CDU", "41%"], ["FCC", "52%"]]}},
    "chart": {"title": "Throughput", "chart": {"labels": ["Q1", "Q2", "Q3"], "values": [10, 25.5, 7], "label": "kbd"}},
    "quote": {"title": "", "quote": {"text": "Measure what matters.", "by": "A. Teacher"}},
    "references": {"title": "References", "bullets": ["Lee, A. (2020). Heat. Journal."]},
    "closing": {"title": "Thank you", "subtitle": "Questions?"},
}


def mockup(layouts=None) -> dict:
    return wizard.validate({"title": "Test deck", "slides": [{"layout": k, **v} for k, v in (layouts or ALL_LAYOUTS).items()]})


def slide_xml(data: bytes, n: int) -> str:
    return zipfile.ZipFile(io.BytesIO(data)).read(f"ppt/slides/slide{n}.xml").decode()


def texts(xml: str) -> list[str]:
    return re.findall(r"<a:t>(.*?)</a:t>", xml)


class Base(unittest.TestCase):
    """Temporary Tugas, Designs and wizard folders; no AI, no Google."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.settings = {"tugas_dir": str(self.tmp / "Tugas"), "designs_dir": str(self.tmp / "Designs"), "ai_mode": "claude"}
        for p in (mock.patch.object(tugas, "get_settings", return_value=self.settings), mock.patch.object(designs, "get_settings", return_value=self.settings),
                  mock.patch.object(wizard, "DIR", self.tmp / "wizard"), mock.patch.object(designs, "THUMBS", self.tmp / "thumbs"),
                  mock.patch.object(designs, "FILE", self.tmp / "designs.json"), mock.patch.object(designs, "DIR", self.tmp / "store"),
                  mock.patch.object(g, "DIR", self.tmp / "google")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, g, "TRANSPORT", None)
        self.pid = db.create_project("Wizard tests")
        (self.tmp / "Tugas" / "Fisika").mkdir(parents=True)

    def draft(self, mode="blank", **kw) -> dict:
        return wizard.new({"mode": mode, "project_id": self.pid, **kw})


class MockupSchemaTest(Base):
    def test_a_good_mockup_is_cleaned_and_every_slide_gets_an_id(self):
        m = mockup()
        self.assertEqual([s["layout"] for s in m["slides"]], list(ALL_LAYOUTS))
        ids = [s["id"] for s in m["slides"]]
        self.assertEqual(len(set(ids)), len(ids))
        chart = next(s for s in m["slides"] if s["layout"] == "chart")["chart"]
        self.assertEqual(chart["values"], [10.0, 25.5, 7.0])
        self.assertEqual(wizard.validate(m)["slides"][2]["id"], m["slides"][2]["id"])  # ids survive a second pass

    def test_bad_mockups_get_plain_messages(self):
        bad = [({}, "list of slides"), ({"slides": []}, "between 1 and"), ({"slides": ["x"]}, "not a slide"),
               ({"slides": [{"layout": "carousel"}]}, "not a layout"), ({"slides": [{"layout": "content", "title": 3}]}, "title must be text"),
               ({"slides": [{"layout": "content", "bullets": "a"}]}, "bullets"), ({"slides": [{"layout": "table", "table": {"rows": ["a"]}}]}, "list of rows"),
               ({"slides": [{"layout": "chart", "chart": {"labels": ["a"], "values": ["x"]}}]}, "numbers"),
               ({"slides": [{"layout": "content"}] * (wizard.MAX_SLIDES + 1)}, "between 1 and")]
        for m, msg in bad:
            with self.assertRaises(ValueError, msg=str(m)[:60]) as cm:
                wizard.validate(m)
            self.assertIn(msg, str(cm.exception))

    def test_duplicate_ids_are_replaced(self):
        m = wizard.validate({"slides": [{"layout": "content", "id": "same"}, {"layout": "content", "id": "same"}]})
        self.assertNotEqual(m["slides"][0]["id"], m["slides"][1]["id"])


class EveryLayoutRendersTest(Base):
    def test_every_layout_becomes_real_editable_text_in_the_pptx(self):
        m = mockup()
        data, flags = tugas_decks.render_deck(m["slides"], "", "Test deck")
        z = zipfile.ZipFile(io.BytesIO(data))
        self.assertIsNone(z.testzip())
        self.assertEqual(sum(1 for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), len(ALL_LAYOUTS))
        want = {"title": ["Deck title", "A subtitle"], "section": ["Part one"], "content": ["Bullets slide", "First bullet", "Second bullet", "Source: Lee (2020)"],
                "two_column": ["Compare", "Before", "old way", "After", "new way"], "figure": ["A figure", "Picture: the plant layout", "Figure 1"],
                "image_left": ["Image left", "beside the picture", "Picture: left picture"], "image_right": ["next to it", "Picture: right picture", "Cap right"],
                "table": ["Results", "Unit", "Yield", "CDU", "41%", "FCC", "52%"], "chart": ["Throughput", "Q1", "Q2", "Q3", "10", "25.5", "7", "kbd"],
                "quote": ["Measure what matters.", "\u2014 A. Teacher"], "references": ["References", "Lee, A. (2020). Heat. Journal."],
                "closing": ["Thank you", "Questions?"]}
        for n, layout in enumerate(ALL_LAYOUTS, 1):
            xml = slide_xml(data, n)
            got = texts(xml)
            for w in want[layout]:
                self.assertIn(w, got, f"{layout}: {w}")
            self.assertIn('txBox="1"', xml, layout)  # text is in real text boxes, not pictures
        self.assertIn("<a:tbl>", slide_xml(data, 8))
        self.assertEqual(slide_xml(data, 9).count('name="Value'), 3)  # one value label per bar
        self.assertGreaterEqual(slide_xml(data, 9).count("<p:sp>"), 3 + 3 + 3)  # bars, values, category names
        self.assertIn('i="1"', slide_xml(data, 10))  # the quote is set in italic
        issues = [f["issue"] for f in flags]
        self.assertEqual(sum("no picture yet" in i or "no figure" in i for i in issues), 3)  # figure + two image slides left empty

    def test_a_picture_fills_an_image_slot(self):
        from app import pptx_build
        from tests.test_pptx_build import png
        s = wizard.validate({"slides": [{"layout": "image_right", "title": "T", "bullets": ["x"], "figure": {"url": "https://img.test/a.png", "description": "d"}}]})["slides"]
        data, flags = pptx_build.build_pptx(s, designs.THEME, "T", {"https://img.test/a.png": (png(), "png")})
        self.assertTrue(any(n.startswith("ppt/media/") for n in zipfile.ZipFile(io.BytesIO(data)).namelist()))
        self.assertNotIn("Picture:", slide_xml(data, 1))
        self.assertEqual(flags, [])

    def test_long_text_gets_a_smaller_font_not_a_cut(self):
        bullets = [("word " * 30).strip() for _ in range(6)]
        s = wizard.validate({"slides": [{"layout": "content", "title": "Long", "bullets": bullets}]})["slides"]
        xml = slide_xml(tugas_decks.render_deck(s, "", "T")[0], 1)
        self.assertEqual(sum(t.startswith("word") for t in texts(xml)), 6)  # nothing dropped
        self.assertNotIn('sz="1800"', xml.split('name="Body')[1].split("</p:sp>")[0])


class OperationsTest(Base):
    def setUp(self):
        super().setUp()
        self.m = wizard.validate({"slides": [
            {"layout": "content", "title": "A", "bullets": ["a1", "a2"], "notes": "na", "source": "S1"},
            {"layout": "content", "title": "B", "bullets": ["b1"], "notes": "nb", "source": "S2"},
            {"layout": "table", "title": "T1", "table": {"header": ["x", "y"], "rows": [["1", "2"]]}},
            {"layout": "table", "title": "T2", "table": {"header": ["x", "y"], "rows": [["3", "4"]]}},
            {"layout": "quote", "title": "Q", "quote": {"text": "Be brief", "by": "Me"}}]})

    def titles(self, m):
        return [s["title"] for s in m["slides"]]

    def test_move_add_delete_duplicate(self):
        m = wizard.apply_op(self.m, {"op": "move", "from": 0, "to": 2})
        self.assertEqual(self.titles(m), ["B", "T1", "A", "T2", "Q"])
        m = wizard.apply_op(m, {"op": "add", "at": 1, "layout": "two_column"})
        self.assertEqual((m["slides"][1]["layout"], len(m["slides"])), ("two_column", 6))
        m = wizard.apply_op(m, {"op": "delete", "index": 1})
        self.assertEqual(len(m["slides"]), 5)
        m = wizard.apply_op(m, {"op": "duplicate", "index": 0})
        self.assertEqual(self.titles(m)[:2], ["B", "B"])
        self.assertNotEqual(m["slides"][0]["id"], m["slides"][1]["id"])
        with self.assertRaises(ValueError):
            wizard.apply_op(self.m, {"op": "delete", "index": 99})
        with self.assertRaises(ValueError):
            wizard.apply_op({"slides": [{"layout": "content"}]}, {"op": "delete", "index": 0})  # the last slide stays
        with self.assertRaises(ValueError):
            wizard.apply_op(self.m, {"op": "explode"})

    def test_merge_joins_text_notes_and_sources_and_tables(self):
        m = wizard.apply_op(self.m, {"op": "merge", "index": 0})
        s = m["slides"][0]
        self.assertEqual((s["title"], s["bullets"], s["notes"], s["source"]), ("A", ["a1", "a2", "b1"], "na\nnb", "S1; S2"))
        self.assertEqual(len(m["slides"]), 4)
        m = wizard.apply_op(m, {"op": "merge", "index": 1})
        self.assertEqual(m["slides"][1]["table"]["rows"], [["1", "2"], ["3", "4"]])  # same columns: one table
        m = wizard.apply_op(m, {"op": "merge", "index": 1})  # a table with a quote: text of the quote joins as a bullet
        self.assertIn("Be brief", wizard.items_of(m["slides"][1]))
        with self.assertRaises(ValueError):
            wizard.apply_op(m, {"op": "merge", "index": len(m["slides"]) - 1})

    def test_swapping_layouts_keeps_the_text(self):
        m = wizard.apply_op(self.m, {"op": "layout", "index": 0, "layout": "two_column"})
        s = m["slides"][0]
        self.assertEqual((s["left"]["bullets"], s["right"]["bullets"]), (["a1"], ["a2"]))
        m = wizard.apply_op(m, {"op": "layout", "index": 0, "layout": "content"})
        self.assertEqual(m["slides"][0]["bullets"], ["a1", "a2"])  # round trip
        m = wizard.apply_op(m, {"op": "layout", "index": 0, "layout": "quote"})
        self.assertEqual(m["slides"][0]["quote"]["text"], "a1")
        m = wizard.apply_op(m, {"op": "layout", "index": 0, "layout": "table"})
        self.assertTrue(m["slides"][0]["table"]["rows"])
        with self.assertRaises(ValueError):
            wizard.apply_op(m, {"op": "layout", "index": 0, "layout": "nope"})


class ChecksTest(Base):
    def kinds(self, slides):
        m = wizard.validate({"slides": slides})
        return [(f["slide"], f["kind"]) for f in wizard.check(m)]

    def test_too_much_text(self):
        long = " ".join(["word"] * 100)
        k = self.kinds([{"layout": "content", "title": "T", "bullets": [long], "source": "x"},
                        {"layout": "content", "title": "T", "bullets": [f"point {i}" for i in range(9)], "source": "x"},
                        {"layout": "content", "title": "T", "bullets": ["short", "short"], "source": "x"}])
        self.assertIn((1, "too_much_text"), k)
        self.assertIn((2, "too_much_text"), k)
        self.assertNotIn((3, "too_much_text"), k)

    def test_missing_source_is_found_unless_cited_or_given(self):
        k = self.kinds([{"layout": "content", "title": "A", "bullets": ["Costs fell 30%"]},
                        {"layout": "content", "title": "B", "bullets": ["Costs fell 30% [@lee2020]"]},
                        {"layout": "content", "title": "C", "bullets": ["Costs fell 30% (Lee, 2020)"]},
                        {"layout": "content", "title": "D", "bullets": ["Costs fell"], "source": "Annual report"},
                        {"layout": "title", "title": "Deck"}, {"layout": "references", "title": "References", "bullets": ["Lee"]}])
        self.assertEqual([x for x in k if x[1] == "no_source"], [(1, "no_source")])

    def test_empty_placeholders(self):
        k = self.kinds([{"layout": "image_left", "title": "A", "bullets": ["x [@k]"]}, {"layout": "chart", "title": "B"},
                        {"layout": "table", "title": "C"}, {"layout": "content", "title": "D"},
                        {"layout": "quote", "title": "E"}, {"layout": "content", "title": "Slide title", "bullets": ["First point [@k]"]},
                        {"layout": "content", "title": "", "bullets": ["fine [@k]"]}])
        empties = [s for s, kind in k if kind == "empty_placeholder"]
        self.assertEqual(sorted(set(empties)), [1, 2, 3, 4, 5, 6, 7])


class DraftTest(Base):
    def test_state_is_saved_and_resumes_after_a_restart(self):
        st = self.draft("upload", title="My deck", folder="Fisika")
        wid = st["id"]
        wizard.add_material(wid, "notes.txt", b"Refineries turn crude into fuels. " * 5)
        wizard.patch(wid, {"options": {"slides": 7, "audience": "class", "notes": False, "tone": "punchy"}, "design": "d1", "step": "design",
                           "output": {"google": True, "folder": "Fisika"}})
        wizard.save_mockup(wid, mockup({"title": ALL_LAYOUTS["title"], "content": ALL_LAYOUTS["content"]}))
        # a "restart": nothing in memory, only the file
        again = wizard.public(wizard.load(wid))
        self.assertEqual((again["step"], again["design"], again["options"]["slides"], again["options"]["notes"], again["options"]["tone"]), ("design", "d1", 7, False, "punchy"))
        self.assertEqual(again["output"], {"pptx": True, "google": True, "folder": "Fisika", "account": ""})
        self.assertEqual([m["name"] for m in again["materials"]], ["notes.txt"])
        self.assertNotIn("text", again["materials"][0])  # the page never gets the uploaded text back
        self.assertEqual(len(again["mockup"]["slides"]), 2)
        row = next(r for r in wizard.listing() if r["id"] == wid)
        self.assertEqual((row["title"], row["step"], row["slides"], row["done"]), ("My deck", "design", 2, False))
        wizard.delete(wid)
        self.assertEqual(wizard.listing(), [])
        with self.assertRaises(ValueError):
            wizard.load(wid)

    def test_bad_ids_and_modes(self):
        for bad in ("../x", "", "zz", "a" * 11):
            with self.assertRaises(ValueError):
                wizard.load(bad)
        with self.assertRaises(ValueError):
            wizard.new({"mode": "magic", "project_id": self.pid})
        with self.assertRaises(ValueError):
            wizard.new({"mode": "blank", "project_id": "nope"})
        with self.assertRaises(ValueError):
            wizard.new({"mode": "doc", "project_id": self.pid, "doc_id": "missing"})

    def test_a_document_or_reading_starts_a_draft_with_its_title(self):
        d = db.create_document(self.pid, "Heat integration", "doc", "# Heat\n\nText")
        st = self.draft("doc", doc_id=d["id"])
        self.assertEqual((st["title"], st["source"]), ("Heat integration", {"type": "doc", "id": d["id"]}))


FAKE_OUTLINE = {"title": "From the essay", "subtitle": "", "slides": [{"layout": "title", "title": "From the essay", "goal": "open"},
                                                                       {"layout": "quote", "title": "A thought", "goal": "quote"},
                                                                       {"layout": "closing", "title": "Thanks", "goal": "end"}]}
FAKE_SLIDES = {"slides": [{"layout": "title", "title": "From the essay", "subtitle": "Sub"},
                          {"layout": "quote", "title": "A thought", "quote": {"text": "Brevity is the soul of wit", "by": "Shakespeare"}},
                          {"layout": "closing", "title": "Thanks", "subtitle": "Questions?"}]}


class FakeAI:
    """Stands in for gslides.complete_json: answers by the kind of prompt and remembers them."""

    def __init__(self, analysis=None):
        self.prompts, self.analysis = [], analysis or {}

    async def __call__(self, prompt, system="", **kw):
        self.prompts.append((kw.get("task"), prompt))
        task = kw.get("task")
        if task == "slides-outline":
            return FAKE_OUTLINE
        if task == "slides-content":
            return FAKE_SLIDES
        if task == "wizard-analyze":
            return self.analysis
        if task == "slides-redo":
            return {"layout": "content", "title": "Rewritten", "bullets": ["new point"]}
        raise AssertionError("unexpected AI call " + str(task))


class EssayGateTest(Base):
    """An essay the wizard wrote must be approved before anything is made from it, and the API enforces it."""

    def setUp(self):
        super().setUp()
        self.ai = FakeAI()
        p = mock.patch.object(gslides, "complete_json", self.ai)
        p.start()
        self.addCleanup(p.stop)
        self.c = TestClient(app)
        self.doc = db.create_document(self.pid, "Essay: heat", "doc", "# Heat\n\n" + "Heat integration saves energy in refineries. " * 20)

    def wizard_with_essay(self):
        st = self.c.post("/api/wizard", json={"mode": "topic", "project_id": self.pid, "topic": "Heat integration in refineries"}).json()
        r = self.c.post(f"/api/wizard/{st['id']}/essay/attach", json={"doc_id": self.doc["id"]})
        self.assertEqual(r.status_code, 200)
        return st["id"]

    def test_nothing_can_be_made_from_an_unapproved_essay(self):
        wid = self.wizard_with_essay()
        for method, path, body in (("post", f"/api/wizard/{wid}/mockup", None), ("post", f"/api/wizard/{wid}/create", None),
                                   ("get", f"/api/wizard/{wid}/review", None), ("put", f"/api/wizard/{wid}/mockup", mockup()),
                                   ("post", f"/api/wizard/{wid}/mockup/op", {"op": "add", "mockup": mockup()})):
            r = getattr(self.c, method)(path, **({"json": body} if body else {}))
            self.assertEqual(r.status_code, 422, path)
            self.assertIn("Approve the essay first", r.json()["detail"], path)
        self.assertEqual(self.ai.prompts, [])  # the AI was never asked

    def test_approving_unlocks_it_and_editing_the_essay_locks_it_again(self):
        wid = self.wizard_with_essay()
        info = self.c.post(f"/api/wizard/{wid}/essay/approve", json={"approved": True}).json()["essay_info"]
        self.assertTrue(info["approved"])
        r = self.c.post(f"/api/wizard/{wid}/mockup")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual([s["layout"] for s in r.json()["mockup"]["slides"]], ["title", "quote", "closing"])
        self.assertEqual(r.json()["mockup"]["slides"][1]["quote"]["by"], "Shakespeare")
        self.assertEqual([t for t, _ in self.ai.prompts], ["slides-outline", "slides-content"])
        outline_prompt = self.ai.prompts[0][1]
        self.assertIn("Heat integration saves energy", outline_prompt)  # the approved essay is the material
        self.assertIn("quote:", outline_prompt)  # the wizard's layouts, including quote and image slides, were offered
        # the person edits the essay after approving it
        self.c.put(f"/api/documents/{self.doc['id']}", json={"content": "# Heat\n\nA different text that was never approved."})
        info = self.c.get(f"/api/wizard/{wid}").json()["essay_info"]
        self.assertEqual((info["approved"], info["changed_after_approval"]), (False, True))
        self.assertEqual(self.c.post(f"/api/wizard/{wid}/create").status_code, 422)
        self.assertEqual(self.c.post(f"/api/wizard/{wid}/mockup").status_code, 422)
        self.c.post(f"/api/wizard/{wid}/essay/approve", json={"approved": True})
        self.assertEqual(self.c.post(f"/api/wizard/{wid}/mockup").status_code, 200)

    def test_the_full_path_makes_a_pptx_in_the_chosen_folder(self):
        wid = self.wizard_with_essay()
        self.c.post(f"/api/wizard/{wid}/essay/approve", json={"approved": True})
        self.c.post(f"/api/wizard/{wid}/mockup")
        self.c.put(f"/api/wizard/{wid}", json={"output": {"pptx": True, "folder": "Fisika"}, "step": "review"})
        rev = self.c.get(f"/api/wizard/{wid}/review").json()
        self.assertEqual(rev["slides"], 3)
        self.assertEqual(rev["google"]["message"], "Connect a Google account first: Settings \u203a Google.")
        self.assertIn("no_source", [c["kind"] for c in rev["checks"]])
        job = self.c.post(f"/api/wizard/{wid}/create").json()["job"]
        for _ in range(100):
            j = self.c.get(f"/api/wizard/jobs/{job}").json()
            if j["state"] != "working":
                break
            time.sleep(0.1)
        self.assertEqual(j["state"], "done", j)
        path = tugas.resolve(j["result"]["path"])
        self.assertEqual(path.parent.name, "Fisika")
        xml = zipfile.ZipFile(path).read("ppt/slides/slide2.xml").decode()
        self.assertIn("Brevity is the soul of wit", texts(xml))
        self.assertEqual(self.c.get(f"/api/wizard/{wid}").json()["step"], "create")  # resuming lands on the result
        self.assertEqual(self.c.get(f"/api/wizard/{wid}").json()["result"]["slides"], 3)

    def test_a_document_you_wrote_needs_no_approval(self):
        st = self.c.post("/api/wizard", json={"mode": "doc", "project_id": self.pid, "doc_id": self.doc["id"]}).json()
        self.assertEqual(self.c.post(f"/api/wizard/{st['id']}/mockup").status_code, 200)

    def test_create_needs_somewhere_to_go(self):
        st = self.c.post("/api/wizard", json={"mode": "blank", "project_id": self.pid, "title": "Quick"}).json()
        self.c.post(f"/api/wizard/{st['id']}/mockup")
        r = self.c.post(f"/api/wizard/{st['id']}/create")
        self.assertIn("Pick a Tugas folder", r.json()["detail"])
        self.c.put(f"/api/wizard/{st['id']}", json={"output": {"pptx": False, "google": True}})
        self.assertIn("Connect a Google account first", self.c.post(f"/api/wizard/{st['id']}/create").json()["detail"])
        self.c.put(f"/api/wizard/{st['id']}", json={"output": {"pptx": False, "google": False}})
        self.assertIn("Choose where the deck goes", self.c.post(f"/api/wizard/{st['id']}/create").json()["detail"])

    def test_the_quick_blank_path_needs_no_ai(self):
        st = self.c.post("/api/wizard", json={"mode": "blank", "project_id": self.pid, "title": "Quick deck", "folder": "Fisika"}).json()
        m = self.c.post(f"/api/wizard/{st['id']}/mockup").json()["mockup"]
        self.assertEqual([s["layout"] for s in m["slides"]], ["title", "content", "closing"])
        self.assertEqual(self.ai.prompts, [])

    def test_an_edit_and_an_op_reach_the_stored_mockup(self):
        st = self.c.post("/api/wizard", json={"mode": "blank", "project_id": self.pid, "title": "Quick deck"}).json()
        m = self.c.post(f"/api/wizard/{st['id']}/mockup").json()["mockup"]
        m["slides"][1]["title"] = "My own title"
        self.assertEqual(self.c.put(f"/api/wizard/{st['id']}/mockup", json=m).status_code, 200)
        r = self.c.post(f"/api/wizard/{st['id']}/mockup/op", json={"op": "move", "from": 1, "to": 0, "mockup": m}).json()
        self.assertEqual(r["mockup"]["slides"][0]["title"], "My own title")
        self.assertEqual(self.c.put(f"/api/wizard/{st['id']}/mockup", json={"slides": [{"layout": "nope"}]}).status_code, 422)


class RewriteAndNoAITest(Base):
    def test_rewrite_one_slide_keeps_its_id_and_source(self):
        ai = FakeAI()
        with mock.patch.object(gslides, "complete_json", ai):
            st = self.draft("blank", title="Quick")
            asyncio.run(wizard.make_mockup(st["id"]))
            wizard.edit_mockup(st["id"], {"op": "add", "layout": "content"})
            cur = wizard.load(st["id"])["mockup"]
            cur["slides"][1]["source"] = "Report 2020"
            wizard.save_mockup(st["id"], cur)
            out = asyncio.run(wizard.rewrite(st["id"], 1, "make it punchy", "content"))
        s = out["mockup"]["slides"][1]
        self.assertEqual((s["title"], s["bullets"], s["id"], s["source"]), ("Rewritten", ["new point"], cur["slides"][1]["id"], "Report 2020"))
        prompt = ai.prompts[-1][1]
        self.assertIn("make it punchy", prompt)

    def test_without_an_ai_the_message_is_plain(self):
        d = db.create_document(self.pid, "Notes", "doc", "Some text " * 20)
        st = self.draft("doc", doc_id=d["id"])
        with mock.patch.object(wizard, "get_settings", return_value={"ai_mode": "api"}), self.assertRaises(Exception) as cm:
            asyncio.run(wizard.make_mockup(st["id"]))
        self.assertIn("No AI is connected", str(cm.exception))

    def test_claude_mode_without_the_page_hand_off_fails_fast_with_a_plain_message(self):
        d = db.create_document(self.pid, "Notes", "doc", "Some text " * 20)
        st = self.draft("doc", doc_id=d["id"])
        with self.assertRaises(Exception) as cm:  # gslides.complete_json -> llm -> handoff.ask, no x-afra-handoff id
            asyncio.run(wizard.make_mockup(st["id"]))
        self.assertIn("Claude app", str(cm.exception))


class UploadsAnalysisTest(Base):
    def test_uploads_are_read_analysed_and_feed_the_mockup(self):
        ai = FakeAI({"title": "Crude oil basics", "summary": "Refineries split crude by boiling point.", "key_points": ["Distillation", "Cracking"],
                     "figures": ["Figure 2: the CDU flow sheet"], "tables": ["Yield by unit"], "quotes": [{"text": "Everything flows", "by": "Heraclitus"}, {"text": "", "by": "x"}]})
        st = self.draft("upload")
        wid = st["id"]
        with self.assertRaises(ValueError):
            wizard.source_ready(wizard.load(wid))  # nothing uploaded yet
        with self.assertRaises(ValueError):
            wizard.add_material(wid, "virus.exe", b"x")
        with self.assertRaises(ValueError):
            wizard.add_material(wid, "empty.txt", b"   ")
        wizard.add_material(wid, "crude.md", b"# Crude\n\nCrude oil is split by boiling point in the CDU.")
        from docx import Document
        buf = io.BytesIO()
        doc = Document()
        doc.add_paragraph("Yields: CDU 41 percent, FCC 52 percent.")
        doc.save(buf)
        wizard.add_material(wid, "yields.docx", buf.getvalue())
        pdf = simple_pdf.build("Handbook", "A paragraph about hydrotreating naphtha.")
        wizard.add_material(wid, "book.pdf", pdf)
        names = [m["name"] for m in wizard.load(wid)["materials"]]
        self.assertEqual(names, ["crude.md", "yields.docx", "book.pdf"])
        with mock.patch.object(gslides, "complete_json", ai):
            a = asyncio.run(wizard.analyze(wid))
            self.assertEqual(a["title"], "Crude oil basics")
            self.assertEqual(a["quotes"], [{"text": "Everything flows", "by": "Heraclitus"}])  # the empty quote is dropped
            prompt = ai.prompts[0][1]
            for w in ("boiling point", "CDU 41 percent", "hydrotreating naphtha"):
                self.assertIn(w, prompt)
            self.assertEqual(wizard.load(wid)["title"], "Crude oil basics")
            out = asyncio.run(wizard.make_mockup(wid))
        outline_prompt = next(p for t, p in ai.prompts if t == "slides-outline")
        self.assertIn("Distillation", outline_prompt)
        self.assertIn("Heraclitus", outline_prompt)
        self.assertIn("Crude oil is split by boiling point", outline_prompt)  # the uploaded text itself is in the material
        self.assertEqual(len(out["mockup"]["slides"]), 3)
        # removing a file forgets the analysis, which belonged to the old set
        wizard.remove_material(wid, wizard.load(wid)["materials"][0]["id"])
        self.assertIsNone(wizard.load(wid)["analysis"])

    def test_a_google_doc_link_is_read_through_the_google_connection(self):
        fake = FakeGoogle(self.tmp)
        st = self.draft("upload")
        fid = "1" + "a" * 24
        r = wizard.add_google_doc(st["id"], f"https://docs.google.com/document/d/{fid}/edit")
        self.assertEqual(r["name"], "Notes doc")
        self.assertIn("text%2Fplain", " ".join(fake.urls))
        self.assertIn("Google Doc text", wizard.load(st["id"])["materials"][0]["text"])

    def test_google_links_without_an_account_say_what_to_do(self):
        st = self.draft("upload")
        with self.assertRaises(ValueError) as cm:
            wizard.add_google_doc(st["id"], "https://docs.google.com/document/d/" + "a" * 25 + "/edit")
        self.assertEqual(str(cm.exception), "Connect a Google account first: Settings \u203a Google.")


class FakeGoogle:
    """One connected account and the few Drive endpoints the wizard touches."""

    def __init__(self, tmp):
        self.requests, self.urls = [], []
        g.DIR.mkdir(parents=True, exist_ok=True)
        (g.DIR / "accounts").mkdir(parents=True, exist_ok=True)
        g._write(g._acct_file("ana@example.com"), {"email": "ana@example.com", "access_token": "tok", "expires": time.time() + 3000,
                                                   "folders": {"decks": "DECKS", "templates": "TPL"}})
        g.TRANSPORT = httpx.MockTransport(self)

    def __call__(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        self.requests.append(req)
        self.urls.append(url)
        if "/export" in url:
            return httpx.Response(200, content=b"Google Doc text", headers={"content-type": "text/plain"})
        if url.startswith(g.UPLOAD):
            return httpx.Response(200, json={"id": "NEWDOC", "name": "Uploaded", "webViewLink": "https://docs.google.com/document/d/NEWDOC/edit"})
        if url.startswith(g.DRIVE):
            if req.method == "GET":
                fid = url.split("/files/")[1].split("?")[0]
                return httpx.Response(200, json={"id": fid, "name": "Notes doc", "trashed": False})
        return httpx.Response(404)


class GoogleDocExportTest(Base):
    def test_the_essay_goes_to_drive_as_a_word_file_converted_to_a_google_doc(self):
        fake = FakeGoogle(self.tmp)
        d = db.create_document(self.pid, "Essay on heat", "doc", "# Essay on heat\n\n## Intro\n\nHeat matters a lot here.\n")
        st = self.draft("topic", topic="Heat integration in refineries")
        wizard.attach_essay(st["id"], d["id"])
        out = wizard.export_google_doc(st["id"])
        self.assertEqual((out["id"], out["url"], out["account"]), ("NEWDOC", "https://docs.google.com/document/d/NEWDOC/edit", "ana@example.com"))
        up = next(r for r in fake.requests if str(r.url).startswith(g.UPLOAD))
        self.assertEqual((up.method, up.url.params["uploadType"]), ("POST", "multipart"))
        self.assertEqual(up.headers["authorization"], "Bearer tok")
        body = up.content
        meta = json.loads(re.search(rb"application/json; charset=UTF-8\r\n\r\n(\{.*?\})\r\n--", body, re.S).group(1))
        self.assertEqual(meta, {"name": "Essay on heat", "parents": ["DECKS"], "mimeType": "application/vnd.google-apps.document"})  # converted on upload
        self.assertIn(b"Content-Type: " + g.MIME_DOCX.encode(), body)  # the media part is the Word file
        docx = body.split(b"\r\n\r\n", 2)[2].rsplit(b"\r\n--", 1)[0]
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(docx)))
        self.assertIn(b"Heat matters a lot here.", zipfile.ZipFile(io.BytesIO(docx)).read("word/document.xml"))
        scopes = " ".join(g.SCOPES)
        self.assertNotIn("documents", scopes)  # no new OAuth scope was needed

    def test_without_google_the_message_is_plain_and_other_exports_still_work(self):
        d = db.create_document(self.pid, "Essay on heat", "doc", "# Essay on heat\n\nHeat matters [@lee2020].\n")
        st = self.draft("topic", topic="Heat integration in refineries")
        wizard.attach_essay(st["id"], d["id"])
        with self.assertRaises(ValueError) as cm:
            wizard.export_google_doc(st["id"])
        self.assertEqual(str(cm.exception), "Connect a Google account first: Settings \u203a Google.")
        pdf, name = wizard.export_pdf(st["id"])
        self.assertEqual(name, "Essay on heat.pdf")
        self.assertTrue(pdf.startswith(b"%PDF"))
        docx, name = wizard.export_docx(st["id"])
        self.assertEqual(name, "Essay on heat.docx")
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(docx)))

    def test_the_pdf_has_the_essay_text_and_a_reference_list(self):
        from pypdf import PdfReader
        paper = {"title": "Heat recovery", "authors": [{"family": "Lee", "given": "A."}], "year": 2020, "venue": "Journal of Heat", "type": "article"}
        stored = db.add_paper(self.pid, paper)
        d = db.create_document(self.pid, "Essay on heat", "doc", f"# Essay on heat\n\n## Intro\n\nHeat matters [@{stored['key']}].\n\n- a point\n")
        st = self.draft("topic", topic="Heat integration in refineries")
        wizard.attach_essay(st["id"], d["id"])
        pdf, _ = wizard.export_pdf(st["id"])
        text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
        self.assertIn("Heat matters (Lee, 2020)", text)
        self.assertIn("References", text)
        self.assertIn("Heat recovery", text)
        self.assertNotIn("[@", text)


class EssayRunTest(Base):
    """The API-mode essay: Deep Research with mocked search and AI, then the essay becomes an A.F.R.A document."""

    def test_the_research_becomes_a_document_with_real_sources_in_the_library(self):
        from app import research
        found = [{"title": "Heat recovery in refineries", "authors": [{"family": "Lee", "given": "A."}], "year": 2021, "venue": "Energy", "doi": "10.1/abc",
                  "abstract": "We study heat recovery.", "type": "article", "url": "https://doi.org/10.1/abc"}]

        async def fake_json(prompt, system="", **kw):
            if kw.get("task") == "research-plan":
                return {"queries": [{"query": "heat recovery refinery", "goal": "overview"}]}
            return {"learnings": [{"text": "Heat recovery cuts fuel use.", "sources": [1]}], "followUpQuestions": []}

        async def fake_stream(messages, system="", **kw):
            key = re.search(r"@([\w:\-]+):", messages[0]["content"]).group(1)
            for piece in (f"# Heat recovery essay\n\n## Abstract\n\nHeat recovery matters [@{key}].", " It cuts fuel use.\n"):
                yield piece

        async def fake_search(q, sources, per, yf, yt):
            return [dict(f) for f in found], []

        st = self.draft("topic", topic="Heat recovery in refineries: what does the evidence say?", title="")

        async def go():
            with mock.patch.object(research, "complete_json", fake_json), mock.patch.object(research, "stream_chat", fake_stream), \
                    mock.patch.object(research, "search_all", fake_search), mock.patch.object(wizard, "get_settings", return_value={"ai_mode": "api", "default_sources": ["openalex"]}), \
                    mock.patch.object(wizard, "ensure_ai"):
                r = await wizard.start_essay(st["id"])
                self.assertEqual(r["mode"], "api")
                for _ in range(100):
                    s = wizard.essay_status(st["id"])
                    if s["status"] != "running":
                        return s
                    await asyncio.sleep(0.05)
        s = asyncio.run(go())
        self.assertEqual(s["status"], "done", s)
        self.assertIn("Heat recovery matters [@", s["content"])
        self.assertFalse(s["approved"])
        doc = db.get_document(s["doc_id"])
        self.assertEqual((doc["title"], doc["meta"]["wizard"]), ("Heat recovery essay", st["id"]))
        self.assertEqual([p["title"] for p in db.list_papers(self.pid)], ["Heat recovery in refineries"])  # added to the library
        self.assertEqual(wizard.load(st["id"])["title"], "Heat recovery essay")
        # the person asks for a change
        async def fake_complete(prompt, system="", **kw):
            return "# Heat recovery essay\n\nShorter [@" + re.search(r"@([\w:\-]+)\]", doc["content"]).group(1) + "] and a made-up [@ghost1999]."
        wizard.approve_essay(st["id"])
        self.assertTrue(wizard.essay_info(wizard.load(st["id"]))["approved"])
        with mock.patch("app.llm.complete", fake_complete), mock.patch.object(wizard, "ensure_ai"):
            r = asyncio.run(wizard.revise_essay(st["id"], "shorter please"))
        self.assertEqual(r["unknown_keys"], ["ghost1999"])  # a source that isn't in the library is reported
        self.assertFalse(wizard.essay_info(wizard.load(st["id"]))["approved"])  # the change has to be approved again
        self.assertEqual(r["previous"], doc["content"])

    def test_claude_mode_hands_the_whole_job_to_the_claude_app(self):
        st = self.draft("topic", topic="Heat recovery in refineries: what does the evidence say?", title="Heat")
        r = asyncio.run(wizard.start_essay(st["id"]))
        self.assertEqual(r["mode"], "claude")
        for w in ("literature_review", "save_document", "Heat recovery in refineries", "Do not make slides"):
            self.assertIn(w, r["prompt"])
        self.assertEqual(wizard.essay_status(st["id"])["status"], "claude")
        d = db.create_document(self.pid, "Essay: Heat", "doc", "# Essay\n\n" + "text " * 30)
        self.assertIn(d["id"], [c["id"] for c in wizard.essay_candidates(st["id"])])

    def test_a_topic_that_is_too_short_is_refused(self):
        st = self.draft("topic", topic="heat")
        with self.assertRaises(ValueError):
            asyncio.run(wizard.start_essay(st["id"]))


def make_pdf(pages: list[bytes], fonts: dict) -> bytes:
    """A tiny PDF with the given content streams and fonts {resource name: BaseFont}."""
    objs = {1: b"<< /Type /Catalog /Pages 2 0 R >>"}
    nfont = 3
    fonts_ref = {}
    for name, base in fonts.items():
        objs[nfont] = f"<< /Type /Font /Subtype /Type1 /BaseFont /{base} >>".encode()
        fonts_ref[name] = nfont
        nfont += 1
    kids, n = [], nfont
    res = "<< /Font << " + " ".join(f"/{k} {v} 0 R" for k, v in fonts_ref.items()) + " >> >>"
    for stream in pages:
        objs[n] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
        objs[n + 1] = f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 960 540] /Contents {n} 0 R /Resources {res} >>".encode()
        kids.append(n + 1)
        n += 2
    objs[2] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
    out, offs = bytearray(b"%PDF-1.4\n"), {}
    for i in sorted(objs):
        offs[i] = len(out)
        out += f"{i} 0 obj\n".encode() + objs[i] + b"\nendobj\n"
    x = len(out)
    out += f"xref\n0 {max(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{offs[i]:010d} 00000 n \n".encode() for i in range(1, max(objs) + 1))
    out += f"trailer\n<< /Size {max(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode()
    return bytes(out)


SLIDE_PAGE = (b"0.0784 0.1294 0.2392 rg 0 0 960 540 re f 1 0.78 0.34 rg 50 400 300 20 re f "
              b"BT /F1 40 Tf 1 1 1 rg 60 300 Td (Hello) Tj ET BT /F2 18 Tf (body one) Tj (body two) Tj (body three) Tj ET")


class PdfDesignTest(Base):
    def test_colours_fonts_and_size_are_read_from_a_vector_pdf(self):
        pdf = make_pdf([SLIDE_PAGE, SLIDE_PAGE], {"F1": "ABCDEF+Calibri-Bold", "F2": "ArialMT"})
        info = pdf_design.read(pdf)
        self.assertEqual((info["pages"], info["size"]), (2, (960.0, 540.0)))
        self.assertEqual(info["colors"][0], "#14213D")  # the page-filling colour comes first
        self.assertEqual(info["fonts"], ["Calibri", "Arial"])  # biggest text = heading font; most shown = body font
        self.assertEqual(info["images"], 0)

    def test_import_pdf_saves_a_design(self):
        pdf = make_pdf([SLIDE_PAGE], {"F1": "ABCDEF+Calibri-Bold", "F2": "ArialMT"})
        r = pdf_design.import_pdf(pdf, "Campus slides.pdf")
        self.assertEqual(r["method"], "colours")
        d = r["design"]
        self.assertEqual((d["name"], d["theme"]["dark"], d["theme"]["heading_font"], d["theme"]["body_font"]), ("Campus slides (PDF)", "#14213D", "Calibri", "Arial"))
        self.assertIn(d["id"], [x["id"] for x in designs.all_designs()])
        self.assertIn("Layouts are A.F.R.A's own", r["message"])

    def test_not_a_pdf_and_picture_only_pdfs_say_so_plainly(self):
        with self.assertRaises(ValueError) as cm:
            pdf_design.import_pdf(b"not a pdf", "x.pdf")
        self.assertIn("isn't a PDF", str(cm.exception))
        blank = make_pdf([b"q Q"], {"F1": "Arial"})
        with self.assertRaises(ValueError) as cm:
            pdf_design.import_pdf(blank, "scan.pdf")
        self.assertIn("pages are pictures", str(cm.exception))

    def test_page_pictures_go_to_the_vision_model_when_one_is_set_up(self):
        from app import template_import
        from tests.test_pptx_build import png
        spec = template_import.validate_spec({"background": "#FFFFFF", "palette": ["#112233"], "fonts": {"heading": "Georgia", "body": "Arial"},
                                              "texts": [{"role": "title", "text": "Hello", "x": 5, "y": 5, "w": 80, "h": 20, "size": 30, "color": "#112233"}]})
        with mock.patch.object(pdf_design, "page_images", return_value=[(png(8, 5), "image/png")]), \
                mock.patch.object(pdf_design, "read", return_value={"pages": 1, "size": (960, 540), "colors": [], "fonts": [], "images": 1}):
            with mock.patch.object(template_import, "vision_conn", side_effect=template_import.Stop("missing", "Rebuilding needs an OpenRouter key.")):
                with self.assertRaises(ValueError) as cm:  # no key, nothing in the vectors: plain message naming the key
                    pdf_design.import_pdf(b"%PDF", "scan.pdf")
                self.assertIn("OpenRouter key", str(cm.exception))
            with mock.patch.object(template_import, "vision_conn", return_value=("https://x/v1", "k", "z-ai/glm-4.5v")), \
                    mock.patch.object(template_import, "read_slide", return_value=spec) as rs:
                r = pdf_design.import_pdf(b"%PDF", "scan.pdf")
        self.assertEqual(r["method"], "rebuilt")
        rs.assert_called_once()
        self.assertTrue(r["design"]["has_pptx"])
        self.assertIn("Georgia", r["design"]["theme"]["heading_font"])


if __name__ == "__main__":
    unittest.main()
