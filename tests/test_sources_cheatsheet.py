"""Several sources at once (reading, merging, per-slide source tags, references) and cheatsheets (validation, review operations,
fit estimate, Word columns, PDF, Google Doc, Tugas folder, Claude-app hand-off). Offline: the AI and Google are mocked."""
import asyncio
import io
import json
import re
import unittest
import zipfile
import zlib
from unittest import mock

from fastapi.testclient import TestClient

from app import cheatsheet, db, google_api as g, gslides, handoff, simple_pdf, sources, tugas, wizard
from app.main import app
from tests.test_wizard import Base, FakeGoogle

A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'


def make_pptx(*slides: list[str]) -> bytes:
    """A tiny .pptx with only the slide XML (all the reader needs): each slide is a list of paragraphs."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, paras in enumerate(slides, 1):
            body = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in paras)
            z.writestr(f"ppt/slides/slide{n}.xml", f'<p:sld xmlns:p="p" {A}><p:cSld><p:spTree><p:sp><p:txBody>{body}</p:txBody></p:sp></p:spTree></p:cSld></p:sld>')
    return buf.getvalue()


def docx_cols(data: bytes) -> list[str]:
    return re.findall(r'<w:cols [^>]*w:num="(\d)"', zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode())


def pdf_text(data: bytes) -> str:
    out = []
    for m in re.finditer(rb"stream\n(.*?)\nendstream", data, re.S):
        try:
            out.append(zlib.decompress(m.group(1)).decode("cp1252", "replace"))
        except zlib.error:
            pass
    return "\n".join(out)


class Src(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(cheatsheet, "DIR", self.tmp / "cheatsheet")
        p.start()
        self.addCleanup(p.stop)

    def put(self, rel: str, data: bytes | str):
        p = tugas.resolve("") / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode())
        return rel


class ReadingTest(Src):
    def test_lecture_slides_are_read_in_slide_order(self):
        t = sources.extract_text("week1.pptx", make_pptx(["Heat exchangers", "LMTD method"], ["Q = U A dT"]))
        self.assertEqual(t, "## Slide 1\nHeat exchangers\nLMTD method\n\n## Slide 2\nQ = U A dT")

    def test_every_supported_type_and_a_plain_refusal_for_the_rest(self):
        self.assertIn("boiling", sources.extract_text("a.md", b"# Crude\nboiling point"))
        self.assertIn("hydrotreating", sources.extract_text("b.pdf", simple_pdf.build("Book", "About hydrotreating naphtha.")))
        with self.assertRaises(ValueError):
            sources.extract_text("virus.exe", b"x")
        with self.assertRaises(ValueError):
            sources.extract_text("broken.pptx", b"not a zip")

    def test_a_folder_becomes_its_files_with_a_cap_and_a_message(self):
        for n in range(45):
            self.put(f"Fisika/L/lec{n:02d}.txt", f"lecture {n}")
        self.put("Fisika/L/.hidden.txt", "x")
        self.put("Fisika/pic.png", "x")
        files, cut = sources.tugas_files("Fisika")
        self.assertEqual((len(files), cut), (40, True))
        self.assertNotIn("Fisika/L/.hidden.txt", files)
        refs, notes = sources.expand([{"type": "folder", "path": "Fisika"}, {"type": "file", "path": files[0]}])
        self.assertEqual(len([r for r in refs if r["type"] == "file"]), 40)  # the file named twice is read once
        self.assertTrue(any("only the first 40" in n for n in notes))
        self.assertEqual(sources.candidates()["folders"][0]["path"], "Fisika/L")

    def test_refs_are_validated(self):
        self.assertEqual(sources.clean_refs([{"type": "doc", "id": "a"}, {"type": "doc", "id": "a"}]), [{"type": "doc", "id": "a"}])
        for bad in ("x", [{"type": "nope", "id": "a"}], [{"type": "file"}], [{"type": "doc", "id": 3}]):
            with self.assertRaises(ValueError):
                sources.clean_refs(bad)


class CollectTest(Src):
    def setUp(self):
        super().setUp()
        self.doc = db.create_document(self.pid, "My notes", "doc", "Notes about pumps.")
        self.paper = db.add_paper(self.pid, {"title": "Heat transfer in crude units", "year": 2020, "authors": [{"family": "Lee", "given": "Ana"}],
                                             "journal": "J. Heat", "key": "lee2020", "abstract": "Fouling reduces duty."})
        self.put("Fisika/lec1.pptx", make_pptx(["Entropy", "Second law"]))
        self.put("Fisika/readme.md", "# Syllabus\nWeek plan")
        self.put("Fisika/scan.pdf", b"%PDF-1.4 nothing")

    def test_documents_papers_uploads_and_tugas_files_are_read_together(self):
        mats = [{"id": "m1", "name": "extra.txt", "text": "uploaded words"}]
        items, notes = sources.collect([{"type": "doc", "id": self.doc["id"]}, {"type": "paper", "id": self.paper["id"]}, {"type": "folder", "path": "Fisika"}], mats)
        labels = [i["label"] for i in items]
        self.assertEqual([i["tag"] for i in items], [f"S{n}" for n in range(1, len(items) + 1)])
        for want in ("My notes", "Heat transfer in crude units", "lec1.pptx", "readme.md", "extra.txt"):
            self.assertIn(want, labels)
        self.assertTrue(any("scan.pdf" in n for n in notes))  # unreadable, skipped with a note, not fatal
        paper = next(i for i in items if i["kind"] == "paper")
        self.assertEqual(sources.source_text(paper), "[@lee2020]")
        self.assertIn("Fouling reduces duty", paper["text"])
        self.assertEqual(sources.source_text(next(i for i in items if i["kind"] == "file")), "lec1.pptx")
        with self.assertRaises(ValueError):
            sources.collect([{"type": "doc", "id": "gone"}])

    def test_a_short_set_is_not_summarised_and_a_long_one_is_summarised_per_source_then_merged(self):
        items = [{"tag": "S1", "kind": "file", "label": "a.txt", "key": "", "text": "short A"}, {"tag": "S2", "kind": "file", "label": "b.txt", "key": "", "text": "short B"}]
        ai = mock.AsyncMock(return_value="SUMMARY")
        with mock.patch.object(sources, "complete", ai):
            text = asyncio.run(sources.digest(items))
            self.assertEqual(ai.await_count, 0)
            self.assertIn("[S1] a.txt", text)
            self.assertIn("short B", text)
            big = [{**i, "text": "word " * 20000} for i in items] + [{"tag": "S3", "kind": "doc", "label": "tiny", "key": "", "text": "tiny text"}]
            text = asyncio.run(sources.digest(big, budget=10000))
        self.assertEqual(ai.await_count, 2)  # one call per long source; the tiny one is kept whole
        self.assertIn("SUMMARY", text)
        self.assertIn("tiny text", text)
        self.assertLess(len(text), 20000)


class MultiDeckTest(Src):
    def setUp(self):
        super().setUp()
        self.doc = db.create_document(self.pid, "My notes", "doc", "Notes about pumps.")
        self.paper = db.add_paper(self.pid, {"title": "Heat transfer in crude units", "year": 2020, "authors": [{"family": "Lee", "given": "Ana"}],
                                             "journal": "J. Heat", "key": "lee2020", "abstract": "Fouling reduces duty."})
        self.put("Fisika/lec1.pptx", make_pptx(["Entropy", "Second law"]))
        self.refs = [{"type": "doc", "id": self.doc["id"]}, {"type": "paper", "id": self.paper["id"]}, {"type": "file", "path": "Fisika/lec1.pptx"}]
        self.prompts = []

        async def ai(prompt, system="", **kw):
            self.prompts.append((kw.get("task"), prompt))
            if kw.get("task") == "slides-outline":
                return {"title": "Mixed deck", "slides": [{"layout": "title", "title": "Mixed deck", "goal": "open"}, {"layout": "content", "title": "Entropy", "goal": "x"},
                                                          {"layout": "content", "title": "Fouling", "goal": "y"}, {"layout": "content", "title": "Pumps", "goal": "z"}]}
            return {"slides": [{"layout": "title", "title": "Mixed deck"},
                               {"layout": "content", "title": "Entropy", "bullets": ["Entropy rises"], "sources": ["S3"]},
                               {"layout": "content", "title": "Fouling", "bullets": ["Fouling cuts duty"], "sources": ["S2", "S3", "S9"]},
                               {"layout": "content", "title": "Pumps", "bullets": ["Pumps need NPSH"], "sources": ["S1"]}]}
        p = mock.patch.object(gslides, "complete_json", ai)
        p.start()
        self.addCleanup(p.stop)

    def test_several_sources_start_a_draft_and_the_ai_reads_them_together(self):
        st = self.draft("multi", sources=self.refs)
        self.assertEqual(wizard.public(st)["source_labels"][2]["name"], "lec1.pptx")
        with self.assertRaises(ValueError):
            self.draft("multi", sources=[])
        out = asyncio.run(wizard.make_mockup(st["id"]))
        outline = next(p for t, p in self.prompts if t == "slides-outline")
        for w in ("[S1] My notes", "[S2]", "[S3] lec1.pptx", "Second law", "Notes about pumps", "Fouling reduces duty"):
            self.assertIn(w, outline)
        content = next(p for t, p in self.prompts if t == "slides-content")
        self.assertIn('"sources"', content)
        self.assertEqual(out["step"], "mockup")

    def test_each_slide_records_its_sources_and_shows_them(self):
        st = self.draft("multi", sources=self.refs)
        slides = asyncio.run(wizard.make_mockup(st["id"]))["mockup"]["slides"]
        by = {s["title"]: s for s in slides}
        self.assertEqual((by["Entropy"]["sources"], by["Entropy"]["source"]), (["S3"], "lec1.pptx"))
        self.assertEqual((by["Fouling"]["sources"], by["Fouling"]["source"]), (["S2", "S3"], "[@lee2020]; lec1.pptx"))  # S9 doesn't exist: dropped
        self.assertEqual(by["Pumps"]["source"], "My notes")
        self.assertTrue(all(s["layout"] != "content" or s["source"] for s in slides))

    def test_the_references_slide_lists_every_source_used(self):
        st = self.draft("multi", sources=self.refs)
        asyncio.run(wizard.make_mockup(st["id"]))
        slides, missing = wizard.prepare(wizard.load(st["id"]))
        refs = next(s for s in slides if s["layout"] == "references")["bullets"]
        text = "\n".join(refs)
        self.assertIn("lec1.pptx", text)
        self.assertIn("My notes", text)
        self.assertIn("Lee", text)  # the paper becomes a real citation
        self.assertEqual(missing, [])
        fouling = next(s for s in slides if s["title"] == "Fouling")
        self.assertNotIn("[@", fouling["source"])
        self.assertIn("Lee", fouling["source"])

    def test_a_single_source_is_given_to_every_content_slide(self):
        st = self.draft("multi", sources=[self.refs[0]])
        for s in asyncio.run(wizard.make_mockup(st["id"]))["mockup"]["slides"]:
            if s["layout"] == "content":
                self.assertEqual(s["source"], "My notes")

    def test_uploaded_files_join_a_multi_draft_and_pptx_uploads_are_read(self):
        st = self.draft("multi", sources=self.refs[:1])
        wizard.add_material(st["id"], "slides.pptx", make_pptx(["Uploaded slide text"]))
        out = asyncio.run(wizard.make_mockup(st["id"]))
        self.assertIn("Uploaded slide text", next(p for t, p in self.prompts if t == "slides-outline"))
        self.assertIn("slides.pptx", [i["label"] for i in sources.collect(self.refs[:1], wizard.load(st["id"])["materials"])[0]])
        wizard.patch(st["id"], {"sources": []})
        self.assertIsNone(wizard.load(st["id"])["mockup"])  # the mockup belonged to the old sources
        self.assertEqual(out["source"]["type"], "multi")

    def test_merging_slides_keeps_both_slides_sources(self):
        a = {**wizard.blank_slide("content"), "sources": ["S1"]}
        b = {**wizard.blank_slide("content"), "sources": ["S2", "S1"]}
        self.assertEqual(wizard.merge_slides(a, b)["sources"], ["S1", "S2"])


class SheetTest(Src):
    def setUp(self):
        super().setUp()
        self.secs = cheatsheet.validate_sections([
            {"title": "Heat", "items": [{"text": "Entropy: disorder of a system", "kind": "definition", "source": "week1"},
                                        {"text": "Q = U A ΔT_lm", "kind": "formula", "source": "week1"}]},
            {"title": "Pumps", "items": [{"text": "NPSH must exceed the vendor value", "kind": "concept", "source": "notes"}]}])

    def test_validation_gives_plain_messages(self):
        for bad in ("x", [{"title": "A"}], [{"title": 3, "items": []}], [{"title": "A", "items": [{"text": 3}]}], [{"title": "t", "items": []}] * 41):
            with self.assertRaises(ValueError):
                cheatsheet.validate_sections(bad)
        s = cheatsheet.validate_sections([{"title": "  ", "items": [{"text": "  ", "kind": "weird"}, {"text": "kept", "kind": "weird", "on": False}]}])
        self.assertEqual((s[0]["title"], len(s[0]["items"]), s[0]["items"][0]["kind"], s[0]["items"][0]["on"]), ("Untitled", 1, "concept", False))

    def test_the_ai_answer_is_cleaned_filtered_and_its_tags_become_source_names(self):
        items = [{"tag": "S1", "kind": "file", "label": "week1.pptx", "key": "", "text": ""}, {"tag": "S2", "kind": "doc", "label": "My notes", "key": "", "text": ""}]
        j = {"title": "Sheet", "sections": [{"title": "A", "items": [{"text": "Term: meaning", "kind": "definition", "source": ["S1"]},
                                                                      {"text": "x = 1", "kind": "formula", "source": "S1, S2"}, "plain string", {"text": ""}]}, {"title": "empty", "items": []}]}
        title, secs = cheatsheet.from_ai(j, items, {"formulas": False})
        self.assertEqual((title, len(secs)), ("Sheet", 1))
        self.assertEqual([i["text"] for i in secs[0]["items"]], ["Term: meaning", "plain string"])  # formulas switched off
        self.assertEqual(secs[0]["items"][0]["source"], "week1")
        with self.assertRaises(ValueError):
            cheatsheet.from_ai({"sections": []}, items, {})
        with self.assertRaises(ValueError):
            cheatsheet.from_ai("nope", items, {})

    def test_review_operations(self):
        s = self.secs
        a, b, c = s[0]["items"][0]["id"], s[0]["items"][1]["id"], s[1]["items"][0]["id"]
        s2 = cheatsheet.apply_op(s, {"op": "toggle", "item": a})
        self.assertFalse(s2[0]["items"][0]["on"])
        s2 = cheatsheet.apply_op(s2, {"op": "set_section", "section": s[0]["id"], "on": True})
        self.assertTrue(all(i["on"] for i in s2[0]["items"]))
        s2 = cheatsheet.apply_op(s2, {"op": "edit_item", "item": b, "text": "  Q = m c dT "})
        self.assertEqual(s2[0]["items"][1]["text"], "Q = m c dT")
        s2 = cheatsheet.apply_op(s2, {"op": "move_item", "item": c, "to": s[0]["id"], "index": 0})
        self.assertEqual([i["id"] for i in s2[0]["items"]][0], c)
        self.assertEqual(s2[1]["items"], [])
        s2 = cheatsheet.apply_op(s2, {"op": "move_section", "section": s[1]["id"], "to": 0})
        self.assertEqual(s2[0]["id"], s[1]["id"])
        s2 = cheatsheet.apply_op(s2, {"op": "edit_section", "section": s[0]["id"], "title": "Thermo"})
        s2 = cheatsheet.apply_op(s2, {"op": "add_item", "section": s[0]["id"], "text": "Mine: my own item"})
        self.assertEqual(s2[1]["items"][-1]["source"], "mine")
        s2 = cheatsheet.apply_op(s2, {"op": "add_section", "title": "Extra"})
        s2 = cheatsheet.apply_op(s2, {"op": "delete_item", "item": a})
        s2 = cheatsheet.apply_op(s2, {"op": "delete_section", "section": s2[-1]["id"]})
        self.assertEqual([x["title"] for x in s2], ["Pumps", "Thermo"])
        for bad in ({"op": "toggle", "item": "gone"}, {"op": "edit_item", "item": b, "text": " "}, {"op": "fly"}, {"op": "delete_section", "section": "gone"}):
            with self.assertRaises(ValueError):
                cheatsheet.apply_op(s, bad)

    def test_the_fit_estimate_follows_length_columns_and_font(self):
        many = cheatsheet.validate_sections([{"title": "S", "items": [{"text": "a fairly long item that takes a couple of lines on the page " * 2, "source": "week1"}] * 150}])
        o = {"length": "1", "columns": 3, "font": 7.0}
        one = cheatsheet.estimate(many, o)
        self.assertTrue(one["fits"] is False and one["ratio"] > 1 and "too much" in one["tip"])
        two = cheatsheet.estimate(many, {**o, "length": "2"})
        self.assertGreater(two["capacity"], one["capacity"] * 1.9)
        self.assertLess(two["ratio"], one["ratio"])
        self.assertGreater(cheatsheet.estimate(many, {**o, "font": 10})["needed"], one["needed"])
        small = cheatsheet.estimate(self.secs, o)
        self.assertTrue(small["fits"] and "room" in small["tip"])
        many[0]["items"] = [dict(i, on=False) for i in many[0]["items"]]
        self.assertEqual(cheatsheet.estimate(many, o)["items"], 0)

    def test_both_sides_starts_the_back_at_a_section_boundary_near_the_middle(self):
        secs = cheatsheet.validate_sections([{"title": f"S{n}", "items": [{"text": "x" * 80}] * 10} for n in range(4)])
        self.assertEqual(cheatsheet.break_section(secs, {"length": "both", "columns": 3, "font": 7}), 2)
        self.assertIsNone(cheatsheet.break_section(secs, {"length": "1", "columns": 3, "font": 7}))
        self.assertIsNone(cheatsheet.break_section(secs[:1], {"length": "both", "columns": 3, "font": 7}))


class FilesTest(Src):
    def draft_with_items(self, **opts):
        st = cheatsheet.new({"project_id": self.pid, "title": "Fisika UTS", "folder": "Fisika"})
        cheatsheet.save_sections(st["id"], [{"title": "Heat", "items": [{"text": "Entropy: disorder of a system", "kind": "definition", "source": "week1"},
                                                                       {"text": "Q = U A dT", "kind": "formula", "source": "week1"}, {"text": "Hidden item", "on": False}]},
                                           {"title": "Pumps", "items": [{"text": "NPSH must exceed the vendor value", "source": "notes"}]}])
        if opts:
            cheatsheet.patch(st["id"], {"options": opts})
        return st["id"]

    def test_the_word_file_has_real_columns_and_only_the_ticked_items(self):
        for cols in (2, 3):
            cid = self.draft_with_items(columns=cols)
            data = cheatsheet.build_docx(cheatsheet.load(cid))
            self.assertEqual(docx_cols(data), ["1", str(cols)])  # a one-column title, then the columns
            xml = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode()
            for w in ("Fisika UTS", "Heat", "Entropy: ", "Q = U A dT", "[week1]", "NPSH must exceed", 'w:pgMar w:top="567"'):
                self.assertIn(w, xml)
            self.assertNotIn("Hidden item", xml)

    def test_both_sides_puts_a_page_break_before_the_back_side(self):
        cid = self.draft_with_items(length="both")
        st = cheatsheet.load(cid)
        st["sections"] = cheatsheet.validate_sections([{"title": f"S{n}", "items": [{"text": "x" * 80}] * 10} for n in range(4)])
        xml = zipfile.ZipFile(io.BytesIO(cheatsheet.build_docx(st))).read("word/document.xml").decode()
        self.assertEqual(xml.count("<w:pageBreakBefore"), 1)

    def test_the_pdf_has_the_items_and_says_when_it_cannot_print_them(self):
        cid = self.draft_with_items()
        data = cheatsheet.build_pdf(cheatsheet.load(cid))
        self.assertTrue(data.startswith(b"%PDF"))
        t = pdf_text(data)
        for w in ("Fisika UTS", "Entropy: disorder", "NPSH must exceed", "[week1]"):
            self.assertIn(w, t)
        self.assertNotIn("Hidden item", t)
        self.assertFalse(cheatsheet.public(cheatsheet.load(cid))["non_latin"])
        cheatsheet.edit(cid, {"op": "add_item", "section": cheatsheet.load(cid)["sections"][0]["id"], "text": "Σx / n"})
        self.assertTrue(cheatsheet.public(cheatsheet.load(cid))["non_latin"])
        with self.assertRaises(ValueError):
            cheatsheet.render(cheatsheet.load(cid), "rtf")

    def test_make_saves_into_the_tugas_folder_and_a_clash_becomes_2(self):
        cid = self.draft_with_items()
        cheatsheet.patch(cid, {"output": {"docx": True, "pdf": True, "folder": "Fisika"}})
        r = cheatsheet.make(cid)
        self.assertEqual(r["files"], ["Fisika/Fisika UTS.docx", "Fisika/Fisika UTS.pdf"])
        r2 = cheatsheet.make(cid)
        self.assertEqual(r2["files"], ["Fisika/Fisika UTS (2).docx", "Fisika/Fisika UTS (2).pdf"])
        self.assertTrue((tugas.resolve("Fisika") / "Fisika UTS.docx").is_file())
        self.assertEqual(cheatsheet.load(cid)["result"]["files"], r2["files"])

    def test_make_needs_something_ticked_a_format_and_a_folder(self):
        st = cheatsheet.new({"project_id": self.pid})
        with self.assertRaises(ValueError):
            cheatsheet.make(st["id"])  # nothing ticked
        cid = self.draft_with_items()
        cheatsheet.patch(cid, {"output": {"docx": False}})
        with self.assertRaises(ValueError):
            cheatsheet.make(cid)  # no format
        cheatsheet.patch(cid, {"output": {"docx": True, "folder": None}})
        with self.assertRaises(ValueError):
            cheatsheet.make(cid)  # no folder
        with self.assertRaises(ValueError):
            cheatsheet.patch(cid, {"options": {"length": "3"}})

    def test_the_google_doc_is_the_word_file_uploaded_and_converted(self):
        fake = FakeGoogle(self.tmp)
        cid = self.draft_with_items()
        cheatsheet.patch(cid, {"output": {"docx": False, "google": True}})
        r = cheatsheet.make(cid)
        self.assertEqual(r["google"]["url"], "https://docs.google.com/document/d/NEWDOC/edit")
        up = next(q for q in fake.requests if str(q.url).startswith(g.UPLOAD))
        meta = json.loads(re.search(rb"application/json; charset=UTF-8\r\n\r\n(\{.*?\})\r\n--", up.content, re.S).group(1))
        self.assertEqual((meta["name"], meta["mimeType"]), ("Fisika UTS", "application/vnd.google-apps.document"))
        self.assertIn(b"Content-Type: " + g.MIME_DOCX.encode(), up.content)

    def test_without_google_the_message_is_plain_and_the_other_files_still_work(self):
        cid = self.draft_with_items()
        cheatsheet.patch(cid, {"output": {"docx": True, "google": True}})
        r = cheatsheet.make(cid)
        self.assertEqual(r["google_error"], "Connect a Google account first: Settings › Google.")
        self.assertEqual(len(r["files"]), 1)
        cheatsheet.patch(cid, {"output": {"docx": False}})
        with self.assertRaises(ValueError) as cm:
            cheatsheet.make(cid)
        self.assertIn("Connect a Google account first", str(cm.exception))


class DraftAndAITest(Src):
    def setUp(self):
        super().setUp()
        self.put("Fisika/week1.pptx", make_pptx(["Entropy", "Second law of thermodynamics"]))
        self.prompts = []
        self.answer = {"title": "Thermo sheet", "sections": [{"title": "Laws", "items": [{"text": "Entropy: always rises", "kind": "definition", "source": ["S1"]},
                                                                                       {"text": "dS >= 0", "kind": "formula", "source": ["S1"]}]}]}

        async def ai(prompt, system="", **kw):
            self.prompts.append((kw.get("task"), prompt))
            if kw.get("task") == "cheatsheet-questions":
                return {"questions": [{"q": "Which exam is it for?", "options": ["Midterm", "Final"]}, {"q": "", "options": []}]}
            if kw.get("task") == "cheatsheet-suggest":
                return {"items": [{"text": "Entropy: always rises", "kind": "definition", "source": ["S1"]}, {"text": "Carnot limit", "kind": "concept", "source": ["S1"]}]}
            return self.answer
        p = mock.patch.object(cheatsheet, "complete_json", ai)
        p.start()
        self.addCleanup(p.stop)

    def new(self):
        return cheatsheet.new({"project_id": self.pid, "sources": [{"type": "folder", "path": "Fisika"}]})

    def test_drafts_are_saved_and_resume(self):
        st = self.new()
        cheatsheet.patch(st["id"], {"title": "Mine", "step": "options", "options": {"columns": 2, "font": 8.4, "include": {"formulas": False}}})
        again = cheatsheet.load(st["id"])
        self.assertEqual((again["title"], again["step"], again["options"]["columns"], again["options"]["font"]), ("Mine", "options", 2, 8.5))
        self.assertFalse(again["options"]["include"]["formulas"])
        self.assertTrue(again["options"]["include"]["definitions"])
        self.assertEqual([d["id"] for d in cheatsheet.listing()], [st["id"]])
        cheatsheet.delete(st["id"])
        self.assertEqual(cheatsheet.listing(), [])
        for bad in ("../x", "zz"):
            with self.assertRaises(ValueError):
                cheatsheet.load(bad)

    def test_the_ai_proposes_sections_from_every_source_and_the_person_can_rerun_nothing_lost(self):
        st = self.new()
        cheatsheet.patch(st["id"], {"options": {"length": "2", "language": "Indonesian"}})
        out = asyncio.run(cheatsheet.propose(st["id"]))
        p = next(p for t, p in self.prompts if t == "cheatsheet-propose")
        for w in ("[S1] week1.pptx", "Second law of thermodynamics", "two A4 pages", "Indonesian"):
            self.assertIn(w, p)
        self.assertEqual((out["step"], out["title"]), ("review", "Thermo sheet"))
        self.assertEqual([i["source"] for i in out["sections"][0]["items"]], ["week1", "week1"])
        self.assertTrue(out["estimate"]["items"] == 2)

    def test_ask_me_first_questions_are_kept_with_the_answers_and_reach_the_prompt(self):
        st = self.new()
        out = asyncio.run(cheatsheet.questions(st["id"]))
        self.assertEqual([q["q"] for q in out["questions"]], ["Which exam is it for?"])
        cheatsheet.answer(st["id"], {"q1": "Final", "zz": "ignored"})
        asyncio.run(cheatsheet.propose(st["id"]))
        self.assertIn("Which exam is it for? Final", next(p for t, p in self.prompts if t == "cheatsheet-propose"))

    def test_more_items_for_a_section_are_offered_not_added(self):
        st = self.new()
        out = asyncio.run(cheatsheet.propose(st["id"]))
        sid = out["sections"][0]["id"]
        r = asyncio.run(cheatsheet.suggest(st["id"], sid, "more on limits"))
        self.assertEqual([i["text"] for i in r["items"]], ["Carnot limit"])  # the one already there is not offered again
        self.assertEqual(len(cheatsheet.load(st["id"])["sections"][0]["items"]), 2)

    def test_nothing_to_read_and_no_ai_say_so_plainly(self):
        empty = cheatsheet.new({"project_id": self.pid})
        with self.assertRaises(ValueError):
            asyncio.run(cheatsheet.propose(empty["id"]))
        st = self.new()
        with mock.patch.object(wizard, "get_settings", return_value={"ai_mode": "api"}), mock.patch("app.llm.resolve", return_value=("openai", "https://x", "", "")):
            with self.assertRaises(Exception) as cm:
                asyncio.run(cheatsheet.propose(st["id"]))
        self.assertIn("No AI is connected", str(cm.exception))

    def test_uploads_join_a_draft(self):
        st = cheatsheet.new({"project_id": self.pid})
        m = cheatsheet.add_material(st["id"], "w2.pptx", make_pptx(["Pressure drop"]))
        self.assertEqual(m["name"], "w2.pptx")
        out = asyncio.run(cheatsheet.propose(st["id"]))
        self.assertIn("Pressure drop", next(p for t, p in self.prompts if t == "cheatsheet-propose"))
        with self.assertRaises(ValueError):
            cheatsheet.add_material(st["id"], "x.exe", b"x")
        cheatsheet.remove_material(st["id"], m["id"])
        self.assertEqual(cheatsheet.load(st["id"])["materials"], [])
        self.assertEqual(out["materials"][0]["name"], "w2.pptx")


class RoutesAndHandoffTest(Src):
    def setUp(self):
        super().setUp()
        self.c = TestClient(app)
        self.put("Fisika/week1.pptx", make_pptx(["Entropy", "Second law"]))

    def test_the_routes_walk_the_whole_flow(self):
        c, pid = self.c, self.pid
        files = c.get("/api/sources/tugas").json()
        self.assertEqual(files["files"][0]["path"], "Fisika/week1.pptx")
        st = c.post("/api/cheatsheet", json={"project_id": pid, "sources": [{"type": "folder", "path": "Fisika"}], "folder": "Fisika"}).json()
        self.assertEqual(st["source_labels"][0]["name"], "Fisika/")
        cid = st["id"]
        self.assertEqual(c.put(f"/api/cheatsheet/{cid}", json={"options": {"columns": 2}}).json()["options"]["columns"], 2)
        r = c.put(f"/api/cheatsheet/{cid}/sections", json={"title": "T", "sections": [{"title": "A", "items": [{"text": "Term: def", "kind": "definition", "source": "w1"}]}]}).json()
        sid, iid = r["sections"][0]["id"], r["sections"][0]["items"][0]["id"]
        r = c.post(f"/api/cheatsheet/{cid}/op", json={"op": "toggle", "item": iid}).json()
        self.assertFalse(r["sections"][0]["items"][0]["on"])
        self.assertEqual(c.post(f"/api/cheatsheet/{cid}/op", json={"op": "fly"}).status_code, 422)
        c.post(f"/api/cheatsheet/{cid}/op", json={"op": "toggle", "item": iid, "on": True})
        c.put(f"/api/cheatsheet/{cid}", json={"output": {"docx": True, "pdf": True, "folder": "Fisika"}})
        made = c.post(f"/api/cheatsheet/{cid}/make").json()
        self.assertEqual(made["files"], ["Fisika/T.docx", "Fisika/T.pdf"])
        d = c.get(f"/api/cheatsheet/{cid}/download?fmt=pdf")
        self.assertEqual((d.status_code, d.headers["content-type"]), (200, "application/pdf"))
        self.assertEqual(docx_cols(c.get(f"/api/cheatsheet/{cid}/download").content), ["1", "2"])
        self.assertEqual(c.get("/api/cheatsheet").json()[0]["id"], cid)
        self.assertEqual(c.delete(f"/api/cheatsheet/{cid}").status_code, 200)

    def test_a_multi_source_wizard_draft_through_the_api(self):
        c = self.c
        d = c.post("/api/wizard", json={"project_id": self.pid, "mode": "multi", "sources": [{"type": "file", "path": "Fisika/week1.pptx"}]}).json()
        self.assertEqual(d["source"]["type"], "multi")
        r = c.put(f"/api/wizard/{d['id']}", json={"sources": [{"type": "file", "path": "Fisika/week1.pptx"}, {"type": "folder", "path": "Fisika"}]}).json()
        self.assertEqual(len(r["source_labels"]), 2)
        up = c.post(f"/api/wizard/{d['id']}/materials", files={"file": ("l.pptx", make_pptx(["Text"]))})
        self.assertEqual(up.status_code, 200)

    def test_in_claude_mode_the_new_ai_steps_are_handed_to_the_claude_app(self):
        st = cheatsheet.new({"project_id": self.pid, "sources": [{"type": "folder", "path": "Fisika"}]})
        # without the page's hand-off id the step fails fast with a plain message
        r = self.c.post(f"/api/cheatsheet/{st['id']}/propose")
        self.assertEqual(r.status_code, 502)
        self.assertIn("Claude app", r.json()["detail"])
        # with it, the request is parked for Claude (get_task / finish_task) and the answer comes back through the same request
        handoff.TASKS.clear()

        async def run():
            from httpx import ASGITransport, AsyncClient
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
                post = asyncio.create_task(ac.post(f"/api/cheatsheet/{st['id']}/propose", headers={"x-afra-handoff": "abc12345"}))
                for _ in range(100):
                    if "abc12345" in handoff.TASKS:
                        break
                    await asyncio.sleep(0.05)
                t = handoff.TASKS["abc12345"]
                self.assertEqual(t["task"], "cheatsheet-propose")
                self.assertTrue(t["json"])
                self.assertIn("Second law", t["prompt"])
                handoff.finish("abc12345", json.dumps({"title": "From Claude", "sections": [{"title": "A", "items": [{"text": "Term: def", "kind": "definition", "source": ["S1"]}]}]}))
                return await post
        r = asyncio.run(run())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["title"], "From Claude")
        self.assertEqual(r.json()["sections"][0]["items"][0]["source"], "week1")
        js = (__import__("pathlib").Path(__file__).parent.parent / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("cheatsheet\\/[^/]+\\/(questions|propose|suggest)", js)  # the page sends these through the hand-off


if __name__ == "__main__":
    unittest.main()
