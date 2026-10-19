# A.F.R.A plan

## Pending

### Google Slides studio (connect Google Drive, Sheets and Slides)
Turn anything into a finished Google Slides deck: a title, a PDF, papers from the library, notes or a spreadsheet. The AI writes and lays it out; the output is a real, editable deck in your Google Drive. Decks can follow a style you show it (an uploaded deck, a Google Slides link, or a link to a template site such as Slidesgo).

How it differs from what A.F.R.A has today:
- Today, quick slides go through the Claude chat, and designer slides use PPT Master in the Code tab, which outputs .pptx files.
- The library's Google Drive sharing (app/sync.py) only uses the Drive *folder* on the computer. It doesn't talk to Google.
- This feature talks to Google directly, so decks open in Google Slides and are shareable straight away.

#### 1. Connect Google (one time)
- **Settings → Google:** each person connects their own Google account. More than one account can be connected (e.g. personal and campus), each listed as "Connected as …@gmail.com" with its own Disconnect button, and one marked as the default for new decks. When making a deck you can pick which account it goes to.
- **Permissions asked:**
  - Only files A.F.R.A creates or that you pick (Drive "drive.file").
  - Slides.
  - Sheets.
  - It never asks for your whole Drive.
- **Setup work:**
  - A Google Cloud project with the Drive, Slides and Sheets APIs, and an OAuth "desktop app" client.
  - While the app is in Google's "testing" mode, only the people on the test-user list (you and Raina) can sign in, which is fine for us. Publishing for anyone needs Google's verification.
- **Tokens:** sign-in tokens are kept per account in data/ (never committed), refresh automatically, and are removed on Disconnect.
- **Where decks go:** an "A.F.R.A Decks" folder in the chosen account's Drive, created on first use. Templates go in "A.F.R.A Decks/Templates".
- **Library:** plain HTTP calls (httpx), no heavy Google SDK. This is the same reason we skipped the `mcp` SDK.

#### 2. What you can give it
- A title or topic only ("Thesis defence: chapter 3 results").
- **PDFs and materials:** uploaded PDFs, Word files, notes, or papers and notes already in the A.F.R.A library (with proper citations on the slides and a references slide).
- **A Google Sheet** (picked from Drive or pasted as a link):
  - Its tables become slide tables.
  - Its charts are embedded as linked charts, so updating the sheet updates the deck.
- **Options:** audience, length (5 / 10 / 20 slides), language, speaker notes on/off, and "keep it academic" or "make it punchy".

#### 3. Style references
- **Upload a deck** (.pptx, or a PDF export of slides). A.F.R.A reads the colours, fonts, title and body sizes, layouts (title, section, two-column, image-left…), backgrounds and logo, and saves them as a style.
- **A Google Slides link:** the same thing, read straight from Google.
- **A web link** (Slidesgo or any other site):
  - A.F.R.A opens the page and takes the preview images, colours and fonts from it.
  - If the site offers "Use as Google Slides theme", it walks you through copying the template into your Drive, then uses that real template. This gives the best result, since it has the real layouts.
  - A.F.R.A doesn't bypass any download wall or sign-in on those sites.
- **Saved styles become templates:**
  - Each style is saved as a template deck in a "A.F.R.A Templates" Drive folder, with named placeholders ({{title}}, {{body}}, {{figure}}…).
  - The style picker shows thumbnails. You can rename, duplicate or delete templates, and set one as the default (e.g. your campus style).

#### 4. Making the deck (AI baked in)
1. **Outline:** the AI reads the materials and proposes an outline (slide titles plus one line each). You can drag, rename, add or remove slides before anything is built.
2. **Content:** for each slide the AI writes the text, chooses a layout from the template, and picks a figure, table or chart from the materials or the sheet.
3. **Build:**
   - A.F.R.A copies the chosen template in Drive.
   - It fills it through the Slides API: duplicate layouts, replace placeholders, insert images, tables and linked Sheets charts, add speaker notes.
   - Text that would overflow is shortened, or split onto a second slide.
4. **Check:** a quick review pass flags slides with too much text, missing sources or empty placeholders. Then "Open in Google Slides".
- **Re-do:** "rewrite this slide", "make it shorter" or "change style" on an existing deck. A.F.R.A edits the same deck and keeps your own changes.

#### 5. Which AI does the work
- **API key mode:** A.F.R.A's own AI (llm.py) does the outline and content.
- **Claude app mode (the default):**
  - New MCP tools in app/mcp_server.py (google_slides_create, google_slides_fill, google_sheets_read, style_list) let Claude in the desktop app build the deck through A.F.R.A.
  - A.F.R.A does the Google calls; Claude does the writing.
  - As decided on 2026-10-17, A.F.R.A never uses your Claude login for its own calls.
- **Both engines, both outputs:** PPT Master and the new Google studio can each give a .pptx file *and* a Google Slides deck.
  - A PPT Master deck gets an "Open in Google Slides" button: A.F.R.A uploads the .pptx to "A.F.R.A Decks" and converts it to Google Slides (Drive's built-in conversion), keeping the editable text boxes.
  - A Google studio deck gets a "Download .pptx" button (Drive export), saved next to the PPT Master outputs.
  - The PPT Master templates and the Google templates share one style library, so a style saved in one can be used by the other.

#### 6. Build order
1. Connect Google, plus read a Sheet and list templates.
2. Title-or-PDF → outline → deck from one built-in A.F.R.A template.
3. Style from an uploaded deck or Google Slides link, saved as a template.
4. Sheets tables and linked charts.
5. Style from web links (Slidesgo and others).
6. Claude app mode MCP tools, re-do and the review pass.

#### Built on branch claude/sweet-hamilton-04454d (2026-10-18, not on main, untested against Google)
- **Files:** app/google_api.py, app/designs.py, app/pptx_build.py, app/gslides.py, routes in app/main.py, 6 MCP tools, and a Slides page with 5 tabs.
- **What it does:**
  - Sign-in through a one-off local page, with several accounts.
  - Without any setup, decks are saved into "My Drive/A.F.R.A Decks" when Drive for desktop is installed.
  - PPT-ify any reading or document.
  - A design gallery: built-in designs, several Slidesgo links at once, uploaded .pptx files and Slides links.
  - Saved designs can be renamed, deleted or set as the default.
- **Tests:** 27 offline tests pass. Run `python -m unittest discover -s tests -t .`
- **Before merging:** make the Google Cloud client (README "Google" section), connect both accounts, and try each Google path with a real account.

#### Decided (2026-10-18)
- PPT-ify readings and documents, with a design gallery (built-in, several Slidesgo links, uploads). Designs can be saved, and the Drive connection should be as close to one click as possible.
- Each person connects their own Google account, and several accounts can be connected at once.
- Decks go into "A.F.R.A Decks" in Drive.
- PPT Master gives both .pptx and Google Slides.

### Tugas tab (asked 2026-10-19, built on this branch)
A dedicated **Tugas** page in the sidebar: a Finder-style view of real folders on disk, holding every assignment.

#### Decided (2026-10-19)
- **Real folders on disk:** default `Documents/A.F.R.A Tugas`, changeable in Settings. File Explorer, Finder and Google Drive for desktop see the same folders.
- **Free folders:** nothing predefined; people make their own (by mata kuliah, semester, anything) and nest them.
- **Any files plus A.F.R.A docs:** slides, Word, PDFs, images and the assignment brief, plus links to A.F.R.A documents and papers. A folder can have a due date and a note.
- **Opening:** a .pptx opens in PowerPoint (the computer's default app) or in Google Slides (upload to "A.F.R.A Decks" and convert, using the Slides studio's Google connection).
- **Templates from any link:** Slidesgo, SlidesCarnival, Microsoft templates, Canva and others.
  - Where the site offers the real .pptx for free, A.F.R.A downloads that file (an exact copy), keeping any attribution the free licence asks for.
  - Where there is only a preview (Canva and similar), REA (`rea-agents`, installed with npm) captures the preview pages and a GLM vision model (through OpenRouter, `z-ai/glm-…`) rebuilds them as an editable .pptx: same layouts, colours and fonts, not pixel-identical.
  - Free templates only: rebuilding paid templates would get around the site's licence.

#### Build order
1. Tugas page: folder tree and grid, new folder, rename, move (drag and drop), delete to the Recycle Bin, add files, open in the default app or show in File Explorer, link A.F.R.A documents, due dates.
2. New deck in a folder from a saved design (the Slides studio's designs), and "Open in Google Slides" for any .pptx.
3. Template import from links: direct .pptx download first, REA + GLM rebuild second.

#### Added 2026-10-19
- **Designs folder:** a folder on disk (default `A.F.R.A Tugas/_Designs`, changeable in Settings) where people can drop any number of .pptx files. Every .pptx in it, subfolders included, shows up in the design selection automatically: a thumbnail and name, refreshed when files are added, changed or removed. Templates imported from links are saved into this folder too, so the folder is the one place for all designs.
- **Step 3 details:** links from Slidesgo, SlidesCarnival, Microsoft templates and others. Free templates only.
  - First try the site's own free .pptx download.
  - Otherwise A.F.R.A starts Edge or Chrome headless with a loopback debugging port. REA (`rea inspect-web-page`, `rea capture-web-screenshot`) captures the preview slides. A GLM vision model through OpenRouter turns each preview into a layout spec, and `pptx_build` renders it as an editable .pptx.
  - When REA or a GLM key is missing, say so plainly and keep the direct-download path working.

### Create into PPT: the full workflow (asked 2026-10-19, step 4 after the Tugas steps)
Every deck goes through one wizard. Google Drive, Docs, Slides and Sheets are connected throughout (through the Slides studio's Google connection).

#### Two ways in
1. **Topic or description first:** the user gives a topic, or paragraphs describing what the deck should cover.
   - The AI researches it with A.F.R.A's real paper search (Deep Research / Paper Writer, real citations only) and writes an essay first.
   - The essay is shown as text and can be exported as a PDF, a Word file or a Google Doc.
   - The user can edit it or ask for changes. Only once the user approves it does **Create into PPT** appear.
2. **Upload an essay or documents:** text, Word, PDF or a Google Doc. The AI analyses them and decides what goes on the slides (key points, figures, tables, quotes); the user can change this in the review steps.

#### The wizard
1. **Content:** the approved essay or the uploaded documents, plus options (number of slides, audience, language, speaker notes, academic or punchy).
2. **Design:** pick one from the design selection (built-in, the Designs folder, imported links), or provide one now as a .pptx upload, a PDF upload (slides exported as PDF: A.F.R.A reads colours, fonts and layouts from the page images, using the same GLM vision step as link imports) or a link to a deck or template.
3. **Mockup review:** every slide shown as a plain wireframe with no design: the layout (title, bullets, two columns, image left, chart, table, quote), the text and the figure placeholders. The user can reorder, add, delete and merge slides, edit the text, swap a slide's layout, and ask the AI to rewrite one slide. Nothing is built yet.
4. **Final review:** the wireframes next to the chosen design's thumbnails, with checks flagged (too much text, missing source, empty placeholder), the output choice (.pptx in the Tugas folder, Google Slides in "A.F.R.A Decks", or both) and where it is saved.
5. **Create:** the AI builds the deck in the chosen design. When it's done: open in PowerPoint or Google Slides, or show in the Tugas folder.

#### Notes
- The wizard can be started from a Tugas folder, from a document or reading, and from the Slides page. The "New deck" button from step 2 becomes the start of this wizard.
- The mockup is the contract: the build follows it exactly, and only the design changes how it looks.

#### Built 2026-10-19 (steps 2 and 3 plus the Designs folder, on this branch)
- **Designs folder:** `designs_dir` setting (default `<Tugas folder>/_Designs`). Every .pptx in it (subfolders too, lock files ignored) is a design; theme and picture are cached in `data/design_thumbs/` by path + mtime + size. Uploads on the Designs page are also saved there. Settings and the Designs page have an "Open Designs folder" button and a file count.
- **New deck (step 2):** in a Tugas folder: pick a design (`pickDesign()` in app.js, reusable), a title and a source (blank, or an A.F.R.A document written into slides with the Slides studio's outline and content steps). `app/tugas_decks.py` keeps the steps separate (`blank_slides`, `source_body`, `make_slides`, `render_deck`, `save_deck`, `create_deck`) and writes the .pptx into the folder ("name (2)" on a clash). .pptx files in the grid get Open in PowerPoint, Open in Google Slides and Use as design.
- **Templates from links (step 3):** `app/template_import.py`. Direct download works for SlidesCarnival, Microsoft template CDN links, public Google Slides decks and plain .pptx links, with a `.source.json` sidecar holding the link and the credit. Slidesgo's free download needs a sign-in, so A.F.R.A stops there with a plain message (and still adds the page's colours and fonts as before); premium pages stop with "paid members only". Preview pages with no file go to the REA + browser + GLM rebuild (`template_vision_model`, default `z-ai/glm-4.5v`), written by `pptx_build.build_from_spec` as real text boxes; a plain message names whatever is missing.
- **Not verified:** the GLM call with a real key, Google calls, and the rebuild on a real preview-only site.

#### Built 2026-10-19 (step 4: the Create into PPT wizard, on this branch)
- **Files:** `app/wizard.py` (drafts, essay, uploads, mockup, checks, create), `app/simple_pdf.py`, `app/pdf_design.py`, `static/create.js` (the `#create` view), routes `/api/wizard/*` and `/api/designs/pdf` in `app/main.py`, small additions to `gslides.py` and `pptx_build.py`.
- **Flow:** Content → Design → Mockup → Review → Create. The draft is one JSON file in `data/wizard/`, so closing and reopening resumes it (list on `#create`). Entry points: Tugas › New deck (folder preset; blank or a document), the Documents editor, the library paper dialog, the Slides page, the sidebar.
- **Essay first:** API mode runs Deep Research (real papers, added to the library) and saves the essay as a document; Claude mode hands the request to Claude and the page offers the new documents to pick. Edit it, ask for changes (AI, with undo), export as PDF (own small writer, Latin letters only), Word, or Google Doc (the .docx uploaded to "A.F.R.A Decks" and converted; no new scope). Every step that reads the content refuses an unapproved essay, and editing after approval asks again.
- **Uploads:** .txt, .md, .docx, .pdf and a Google Doc link; the AI analyses them (summary, key points, figures, tables, quotes) and the analysis feeds the outline.
- **Mockup = the contract:** validated JSON, layouts title, section, content, two_column, figure, image_left, image_right, table, chart (native bars), quote, references, closing. Reorder by drag, add, delete, duplicate, merge, swap layout (text kept), inline edit, AI rewrite of one slide. The build follows it exactly (no re-splitting); long text gets a smaller font. Checks: too much text, no source, empty placeholder.
- **Design from a PDF:** colours, fonts and page size read from the PDF drawing commands (works for exports from PowerPoint, Slides, Keynote). Pages that are pictures go to the GLM vision step only if an OpenRouter key is set; otherwise a plain message. No page renderer is installed, so pictures of vector pages are never made.
- **Output:** .pptx into a Tugas folder (folder picker), Google Slides (the .pptx converted), or both. Without a Google account the Google parts say "Connect a Google account first: Settings › Google." and the .pptx still works.
- **Tests:** 121 pass (`tests/test_wizard.py` adds 38). Smoke-tested in the browser on port 8777: blank path, inline edits, ops, resume, review, create, essay approval gate, and the Claude-app hand-off for analyse, outline and slides.
- **Not verified:** real AI output, Deep Research end to end with a key, any Google call, the vision step with a real key, opening the files in real PowerPoint.

### Several sources at once, and cheatsheets (asked 2026-10-19)
#### Several documents as the source
- Everywhere a deck or essay starts from "a document or reading" (the Slides page card, the Create into PPT wizard's Content step, Tugas New deck), people can pick **several**: A.F.R.A documents, library papers, uploaded files and files in a Tugas folder, mixed.
- The AI reads them together. Each slide keeps track of which source it came from, so citations and the references slide list every source used, and the mockup shows a slide's source.
- Long sets are summarised per source first, then merged, so nothing is cut off by the model's context.

#### Cheatsheets
- **From many lectures, documents and materials at once:** lecture slides (.pptx and PDF), Word, PDF, notes, A.F.R.A documents and papers, and whole Tugas folders.
- **Output:** a dense, printable cheatsheet: definitions, formulas, key concepts, steps, comparisons and short examples, grouped by topic, with a small source tag on each item.
- **Options:** length (1 page, 2 pages, or both sides of A4), columns (2 or 3), font size, what to include (definitions, formulas, examples, dates and names), and language.
- **Review first:** the AI proposes the sections and items; the person can tick items off, move them or edit them before the file is made.
- **Formats:** Word (.docx, real columns), PDF, and Google Doc. Saved into a Tugas folder.
- **Where it starts:** select several files in a Tugas folder and choose **Make cheatsheet**; a **Cheatsheet** page in the sidebar; and from the Create into PPT sources.

### Free Slidesgo templates, rebuilt from their previews (2026-10-19)
- Slidesgo's real .pptx needs a sign-in, so A.F.R.A no longer tries it. A theme page marked **Free** is rebuilt from its public slide previews (media.slidesgo.com, the largest variant of each slide); **Premium** stops with "This template is for paid members only". Never a login, never a third-party downloader.
- Previews: REA (headless browser) adds the picture addresses it sees; the page's own HTML lists every slide as well, so a missing REA or browser only gets a note. They are downloaded (size and time caps) into `data/slidesgo_previews`, and removed afterwards.
- Engine by AI mode: **API** = GLM vision per preview, then `pptx_build`; **Claude app** = a task (`handoff.create`, kind "design") telling Claude to rebuild it with PPT Master; Claude hands the file back with the MCP tool `save_design(task_id, pptx_path)` (checked as a real .pptx, saved in the Designs folder with its sidecar, task finished). No OpenRouter key is needed in Claude mode.
- The sidecar credit reads "Rebuilt from Slidesgo (free template) — not the original file. Template by Slidesgo / Freepik: keep the credits slide."; the design list shows a **Rebuilt** badge. "Add from links" shows a status per link, including "Waiting for Claude" with an Open Claude button.
- Code: `app/slidesgo.py`, hooks in `template_import.py`, `handoff.create`, `/api/designs/rebuilt/{task}`, `save_design` in `mcp_server.py`. Tests: `tests/test_slidesgo.py`.
- **Not verified:** a real GLM key run, a real Claude + PPT Master rebuild, the browser UI by eye.

### GreatPPT as a template source + Downloads watcher (2026-10-19)
- **Watcher** (`app/watcher.py`, loop `_downloads_loop` in `main.py`): on by default, watches the user's Downloads folder (Windows known folder, else `~/Downloads`; setting `downloads_dir`) for **new** `.pptx`/`.potx` only (the files there at start are a baseline and never touched). Waits until the size is stable and no `.crdownload`/`.part` sits next to it, checks zip + `ppt/presentation.xml`, then **moves** (default) or copies (`downloads_action`) into the Designs folder with a sidecar. The site and credit come from the file's `Zone.Identifier` stream (ReferrerUrl, then HostUrl). A `.potx` is saved as a normal `.pptx` (content type rewritten). A toast "Added 'X' from GreatPPT to your designs" arrives through `/api/events` (`design_added`) and the Designs list refreshes. Settings › Templates.
- **GreatPPT link, no account** (`app/greatppt.py`): `greatppt.com/downloads/<slug>/` is classified `greatppt`; Free = the Download form's `data-price` is 0 (and the title says Free); previews are read from `data-src`/`data-lazy-src`/`srcset` (`/wp-content/uploads/edd/…-<n>.jpg`, cover has no number, related templates dropped) and rebuilt with the same two engines as Slidesgo (`slidesgo.rebuild_site`, a per-site description in `slidesgo.SITES`). Sidecar: "Rebuilt from GreatPPT (free template) — not the original file…" + source URL; Rebuilt badge; hint "Connect GreatPPT for the original file, or download it in your browser — the Downloads watcher picks it up."
- **GreatPPT account, exact file**: "Connect GreatPPT" (Settings › Templates and the Add-from-links box) opens a **visible** Edge/Chrome with profile `data/browser/greatppt` and a loopback debugging port (`app/cdp.py`, a tiny stdlib WebSocket client). The user signs in and passes any check; A.F.R.A only reads `body.logged-in`. Connected → for a link: open the page in that window, press the page's own Download form, follow the $0 checkout (stops if the total is not 0, unreadable, or card fields show; terms boxes are left to the user), take the file link from the confirmation / account page, download it with that session's cookies for greatppt.com only (never stored), keep an exact copy ("Template by GreatPPT (free licence)."). A Cloudflare / captcha / sign-in page brings the window forward, the link shows "Waiting for you in the GreatPPT window" and the import goes on after the user passes it (5 min limit). Disconnect deletes the profile. No script ever types or fills a field (only `Page.navigate`, `Runtime.evaluate` reads and `.click()`), the state file holds only port/pid/connected.
- Never used: GreatPPT's "Slidesgo Free Downloader" or any third-party fetcher. Tests: `tests/test_greatppt.py`, `test_greatppt_account.py`, `test_watcher.py`.
- **Not verified:** the real GreatPPT sign-in, checkout and confirmation page selectors (written from EDD's standard markup, tested only on a scripted fake site), a real Cloudflare check, a real Claude + PPT Master rebuild, the browser UI by eye.
