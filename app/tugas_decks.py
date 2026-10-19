"""Decks inside Tugas folders: a .pptx made from a design and written straight into an assignment's folder.

The steps are separate functions so a wizard can call them one at a time:
  blank_slides(title)                      a starting outline with no AI
  source_body(title, source)               the material body gslides.outline / gslides.content read
  make_slides(title, source, step)         outline -> slides with the Slides studio's own logic (async, needs an API model)
  render_deck(slides, design_id, title)    slides + design -> .pptx bytes (pptx_build)
  save_deck(folder, title, data)           bytes -> the Tugas folder ("name (2)" when taken)
  create_deck(folder, design_id, title, source, slides)   all of the above; returns the saved path
  start(...) / job(jid)                    create_deck in the background with a step message for the page
In Claude mode the page writes the slides itself through /api/decks/outline and /api/decks/content (which hand over
to the Claude app) and then passes them in as `slides`.
"""
import asyncio
import secrets
import threading
import time

from . import db, designs, gslides, pptx_build, tugas

JOBS: dict[str, dict] = {}


def blank_slides(title: str) -> list[dict]:
    """Title, one empty content slide and a closing slide: the person fills the text in PowerPoint."""
    t = (title or "New deck").strip()[:150]
    return [gslides._clean({"title": t, "subtitle": ""}, {"layout": "title"}),
            gslides._clean({"title": "Slide title", "bullets": ["First point", "Second point"]}, {"layout": "content"}),
            gslides._clean({"title": "Thank you", "subtitle": "Questions?"}, {"layout": "closing"})]


def source_body(title: str, source: dict | None) -> dict:
    """source: None/{"type": "blank"} or {"type": "doc", "id": <A.F.R.A document id>}."""
    source = source or {}
    if source.get("type") != "doc":
        return {"title": title}
    d = db.get_document(source.get("id") or "")
    if not d:
        raise ValueError("That A.F.R.A document no longer exists.")
    return {"title": title, "topic": d["title"], "document_id": d["id"], "project_id": d["project_id"], "use_library": True}


async def make_slides(title: str, source: dict | None, step=lambda s: None) -> tuple[list[dict], list[dict]]:
    """(slides, flags). A blank source needs no AI."""
    body = source_body(title, source)
    if not body.get("document_id"):
        return blank_slides(title), []
    step("Writing the outline…")
    outline = await gslides.outline(body)
    step(f"Writing {len(outline['slides'])} slides…")
    done = await gslides.content({**body, "outline": outline})
    return done["slides"], done["flags"]


def render_deck(slides: list[dict], design_id: str, title: str) -> tuple[bytes, list[dict]]:
    design = designs.pick(design_id)
    return pptx_build.build_pptx(slides, designs.theme(design), title, pptx_build.fetch_images(slides))


def save_deck(folder: str, title: str, data: bytes) -> str:
    return tugas.save_bytes(folder, pptx_build.safe_name(title) + ".pptx", data)


async def create_deck(folder: str, design_id: str, title: str, source: dict | None = None, slides: list[dict] | None = None,
                      step=lambda s: None) -> dict:
    """Make the deck and save it into `folder` (a Tugas path). Returns {"path", "name", "slides", "flags"}."""
    title = (title or "").strip()[:150] or "New deck"
    if not tugas.resolve(folder).is_dir():
        raise tugas.TugasError("That folder doesn't exist.")
    designs.pick(design_id)  # fails now if the design is gone
    flags = []
    if slides:
        slides = [gslides._clean(s, s) for s in slides if isinstance(s, dict)]
        fitted = gslides.finish_content(slides, "", True)  # the same fit/split as the Slides page
        slides, flags = fitted["slides"], fitted["flags"]
    else:
        slides, flags = await make_slides(title, source, step)
    step(f"Writing the PowerPoint ({len(slides)} slides)…")
    data, more = await asyncio.to_thread(render_deck, slides, design_id, title)
    path = save_deck(folder, title, data)
    return {"path": path, "name": path.rsplit("/", 1)[-1], "slides": len(slides), "flags": gslides._dedupe(flags + more)}


def start(folder: str, design_id: str, title: str, source: dict | None = None, slides: list[dict] | None = None) -> dict:
    tugas.resolve(folder)
    designs.pick(design_id)
    jid = secrets.token_hex(5)
    JOBS[jid] = {"id": jid, "state": "working", "step": "Starting…", "error": "", "started": time.time(), "result": None}
    for k in [k for k, v in JOBS.items() if time.time() - v["started"] > 86400]:
        JOBS.pop(k, None)

    def run():
        J = JOBS[jid]
        try:
            J["result"] = asyncio.run(create_deck(folder, design_id, title, source, slides, lambda s: J.update(step=s)))
            J.update(state="done", step="Done")
        except Exception as e:  # shown in the page
            J.update(state="error", error=str(e) or e.__class__.__name__)
    threading.Thread(target=run, daemon=True).start()
    return {"job": jid}


def job(jid: str) -> dict:
    j = JOBS.get(jid)
    if not j:
        raise ValueError("Unknown job (A.F.R.A was restarted?). Look in the folder.")
    return j
