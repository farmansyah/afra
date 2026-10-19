"""The Google Slides engine without Google: fitting, the batchUpdate requests, the review, citations and a .pptx build job."""
import shutil
import time
import unittest

from app import db, designs, google_api as g, gslides

THEME = designs.THEME


def fake_template(theme=THEME) -> dict:
    """The presentation Google would return after template_requests() and the "afra:layout=" notes markers."""
    slides, shapes = [], {}
    for r in gslides.template_requests(theme):
        if "createSlide" in r:
            sid = r["createSlide"]["objectId"]
            nid = sid + "_notes"
            slides.append({"objectId": sid, "pageElements": [], "slideProperties": {"notesPage": {
                "notesProperties": {"speakerNotesObjectId": nid},
                "pageElements": [{"objectId": nid, "shape": {"text": {"textElements": [{"textRun": {"content": f"afra:layout={sid.split('_', 1)[1]}\n"}}]}}}]}}})
        elif "createShape" in r:
            c = r["createShape"]
            ep = c["elementProperties"]
            el = {"objectId": c["objectId"], "size": ep["size"], "transform": ep["transform"], "shape": {"shapeType": c["shapeType"], "text": {"textElements": []}}}
            shapes[c["objectId"]] = el
            next(s for s in slides if s["objectId"] == ep["pageObjectId"])["pageElements"].append(el)
        elif "insertText" in r:
            shapes[r["insertText"]["objectId"]]["shape"]["text"]["textElements"].append({"textRun": {"content": r["insertText"]["text"]}})
    return {"presentationId": "tpl", "slides": slides, "pageSize": {"width": {"magnitude": 9144000}, "height": {"magnitude": 5143500}}}


def fake_layout_deck() -> dict:
    ph = lambda oid, kind, idx=0: {"objectId": oid, "size": {"width": {"magnitude": 8000000, "unit": "EMU"}, "height": {"magnitude": 1000000, "unit": "EMU"}},
                                   "transform": {"scaleX": 1, "scaleY": 1, "translateX": 500000, "translateY": 300000, "unit": "EMU"},
                                   "shape": {"placeholder": {"type": kind, "index": idx}}}
    lay = lambda oid, name, *phs: {"objectId": oid, "layoutProperties": {"name": name, "displayName": name}, "pageElements": list(phs)}
    return {"presentationId": "real", "slides": [{"objectId": "p1", "pageElements": []}],
            "layouts": [lay("L1", "TITLE", ph("t1", "CENTERED_TITLE"), ph("s1", "SUBTITLE")),
                        lay("L2", "SECTION_HEADER", ph("t2", "TITLE")),
                        lay("L3", "TITLE_AND_BODY", ph("t3", "TITLE"), ph("b3", "BODY")),
                        lay("L4", "TITLE_AND_TWO_COLUMNS", ph("t4", "TITLE"), ph("b4", "BODY", 1), ph("c4", "BODY", 2))],
            "pageSize": {"width": {"magnitude": 9144000}, "height": {"magnitude": 5143500}}}


def kinds(reqs):
    return [next(iter(r)) for r in reqs]


class FitTest(unittest.TestCase):
    def test_crowded_slides_continue(self):
        long = "word " * 60
        out, flags = gslides.fit_slides([{"layout": "content", "title": "Many", "bullets": [f"Point {i}" for i in range(8)] + [long], "notes": "N"}])
        self.assertEqual([s["title"] for s in out], ["Many", "Many (cont.)"])
        self.assertEqual(sum(len(s["bullets"]) for s in out), 9)
        self.assertTrue(all(len(b) <= gslides.MAX_CHARS + 1 for s in out for b in s["bullets"]))
        self.assertEqual((out[0]["notes"], out[1]["notes"]), ("N", ""))  # notes stay on the first slide
        self.assertIn("a long point was shortened", [f["issue"] for f in flags])

    def test_tables_are_paged_and_cut(self):
        rows = [[f"r{i}c{j}" for j in range(8)] for i in range(25)]
        out, flags = gslides.fit_slides([{"layout": "table", "title": "T", "table": {"header": [f"h{j}" for j in range(8)], "rows": rows}}])
        self.assertEqual([len(s["table"]["rows"]) for s in out], [10, 10, 5])
        self.assertTrue(all(len(s["table"]["header"]) == 6 and len(s["table"]["rows"][0]) == 6 for s in out))
        self.assertEqual(out[2]["title"], "T (cont.)")
        out, _ = gslides.fit_slides([{"layout": "table", "title": "Empty", "table": {}, "caption": "c"}])
        self.assertEqual((out[0]["layout"], out[0]["bullets"]), ("content", ["c"]))

    def test_two_columns_continue(self):
        out, _ = gslides.fit_slides([{"layout": "two_column", "title": "C", "left": {"heading": "L", "bullets": list("abcdefg")}, "right": {"bullets": ["x"]}}])
        self.assertEqual(len(out), 2)
        self.assertEqual(out[1]["left"]["bullets"], ["f", "g"])


class RequestsTest(unittest.TestCase):
    def slides(self):
        return [gslides._clean(s, s) for s in [
            {"layout": "title", "title": "Deck", "subtitle": "Sub"},
            {"layout": "content", "title": "Points", "bullets": ["a", "b"], "notes": "Say a and b."},
            {"layout": "table", "title": "Tab", "table": {"header": ["A", "B"], "rows": [["1", "2"]]}},
            {"layout": "chart", "title": "Chart", "chart": {"sheet_id": "S1", "chart_id": 7}},
            {"layout": "figure", "title": "Fig"},
            {"layout": "two_column", "title": "Cols", "left": {"bullets": ["l"]}, "right": {"bullets": ["r"]}},
        ]]

    def test_token_template(self):
        pres = fake_template()
        self.assertEqual(set(gslides.token_slides(pres)), set(gslides.LAYOUTS))
        reqs, recs, flags = gslides.build_requests(pres, self.slides(), {"kind": "token"}, "n1")
        k = kinds(reqs)
        self.assertEqual(k.count("duplicateObject"), 6)
        self.assertEqual([r["id"] for r in recs], [f"an1_{i:03d}" for i in range(6)])
        self.assertEqual(k.count("createTable"), 1)
        self.assertEqual(k.count("createSheetsChart"), 1)
        chart = next(r["createSheetsChart"] for r in reqs if "createSheetsChart" in r)
        self.assertEqual((chart["spreadsheetId"], chart["chartId"], chart["linkingMode"]), ("S1", 7, "LINKED"))
        deleted = [r["deleteObject"]["objectId"] for r in reqs if "deleteObject" in r]
        self.assertTrue(set(s["objectId"] for s in pres["slides"]) <= set(deleted))  # the template's own slides go
        self.assertEqual(k[-6:], ["updateSlidesPosition"] * 6)
        self.assertIn({"slide": 5, "issue": "no figure to insert: add it by hand"}, flags)
        texts = [r["replaceAllText"]["replaceText"] for r in reqs if "replaceAllText" in r]
        self.assertIn("a\nb", texts)
        dup = reqs[0]["duplicateObject"]
        self.assertEqual(dup["objectIds"][dup["objectId"]], "an1_000")

    def test_real_layouts(self):
        pres = fake_layout_deck()
        self.assertEqual({k: v["id"] for k, v in gslides.map_layouts(pres).items() if k in ("title", "section", "content", "two_column")},
                         {"title": "L1", "section": "L2", "content": "L3", "two_column": "L4"})
        reqs, recs, flags = gslides.build_requests(pres, self.slides(), {"kind": "layout"}, "n2")
        created = [r["createSlide"] for r in reqs if "createSlide" in r]
        self.assertEqual([c["slideLayoutReference"]["layoutId"] for c in created], ["L1", "L3", "L3", "L3", "L3", "L4"])
        self.assertIn("createTable", kinds(reqs))
        self.assertIn({"deleteObject": {"objectId": "p1"}}, reqs)
        self.assertNotIn("updateSlidesPosition", kinds(reqs))
        self.assertEqual(recs[1]["fields"], {"title": "an2_001_title", "body": "an2_001_body"})
        with self.assertRaises(ValueError):
            gslides.build_requests({"slides": [], "layouts": []}, self.slides(), {"kind": "layout"}, "x")

    def test_notes_and_review(self):
        pres = fake_template()
        sid = pres["slides"][2]["objectId"]
        reqs = gslides.notes_requests(pres, {sid: "New notes"})
        self.assertEqual(kinds(reqs), ["deleteText", "insertText"])  # the afra:layout marker is cleared
        built = {"slides": [{"objectId": "x_000", "pageElements": [{"objectId": "e", "shape": {"text": {"textElements": [{"textRun": {"content": "{{body}}"}}]}}}]}]}
        flags = gslides.review(built, [{"id": "x_000"}], [{"layout": "content", "title": "T", "bullets": ["Yield rose 12% in 2021"]}])
        issues = [f["issue"] for f in flags]
        self.assertTrue(any("placeholder" in i for i in issues))
        self.assertIn("a factual claim without a source", issues)


class ContentAndBuildTest(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(g.DIR, ignore_errors=True)
        designs.FILE.unlink(missing_ok=True)

    def test_citations_become_references(self):
        db.init()
        pid = db.create_project("Slides test")
        key = db.add_paper(pid, {"title": "Heat transfer in crude units", "year": 2020, "authors": [{"family": "Lee", "given": "Ana"}], "journal": "J. Heat", "key": "lee2020"})["key"]
        slides = [gslides._clean(s, s) for s in [{"layout": "title", "title": "Deck"},
                                                 {"layout": "content", "title": "Why", "bullets": [f"Heat matters [@{key}]", "Unknown [@nobody1999]"]},
                                                 {"layout": "closing", "title": "Thanks"}]]
        r = gslides.finish_content(slides, pid, notes=False)
        L = [s["layout"] for s in r["slides"]]
        self.assertEqual(L, ["title", "content", "references", "closing"])
        self.assertIn("Lee", r["slides"][1]["bullets"][0])
        self.assertNotIn("[@", r["slides"][1]["bullets"][0])
        self.assertTrue(r["slides"][2]["bullets"])
        self.assertEqual(r["missing"], ["nobody1999"])

    def test_pptx_build_job(self):
        body = {"title": "Job deck", "targets": ["google", "pptx"], "design_id": "afra-forest",
                "slides": [{"layout": "title", "title": "Job deck"}, {"layout": "content", "title": "A", "bullets": ["b"]}]}
        started = gslides.build(body)
        self.assertEqual(started["targets"], ["pptx"])  # no Google account: the PowerPoint only
        for _ in range(100):
            j = gslides.job(started["job"])
            if j["state"] != "working":
                break
            time.sleep(.05)
        self.assertEqual(j["state"], "done", j.get("error"))
        p = j["pptx"]
        self.assertFalse(p["in_drive"])
        listed = gslides.decks()
        self.assertEqual((listed[0]["id"], listed[0]["slides"], listed[0]["rebuild"]), (p["id"], 2, True))
        data, name = gslides.export_deck(p["id"], "pptx")
        self.assertTrue(data.startswith(b"PK"))
        self.assertTrue(name.endswith(".pptx"))
        with self.assertRaises(ValueError):
            gslides.update_slide(p["id"], 0, {"title": "x"})  # a PowerPoint file is edited in PowerPoint
        with self.assertRaises(ValueError):
            gslides.build({**body, "targets": ["google"]})  # Google only, and nothing connected
        gslides.forget_deck(p["id"])
        self.assertEqual(gslides.decks(), [])


if __name__ == "__main__":
    unittest.main()
