# A.F.R.A: Article Finder & Research Assistant

**Website: https://farmansyah.github.io/afra/** · Download: [latest version (zip)](https://github.com/farmansyah/afra/archive/refs/heads/main.zip)

A.F.R.A (Article Finder & Research Assistant) helps you go from a research title to a finished, correctly formatted Word manuscript. It runs on your
own computer, in your web browser, and keeps all your data on your computer.

---

## Install (no programming needed)

### Windows
1. Unzip `AFRA.zip` into a folder you'll keep, for example `Documents\AFRA`.
2. Double-click **`start.bat`**.
   - If Python isn't installed yet, the download page opens for you. Install it and **tick "Add python.exe to
     PATH"** on the first screen, then double-click `start.bat` again.
3. The first start takes about a minute. After that, AFRA opens in your browser, and a
   **"AFRA" shortcut** appears on your Desktop for next time.

### Mac
1. Open **Terminal** (⌘ Space, type *Terminal*, Return).
2. Paste this line and press Return:
   ```bash
   curl -fsSL https://farmansyah.github.io/afra/install.sh | bash
   ```
3. That's all. A.F.R.A opens in your browser and appears in your Dock and Applications. It runs quietly in the
   background, starts with your Mac, and nothing needs approving under *Privacy & Security*. If you used A.F.R.A from
   an unzipped folder before, your library is copied over. Running the line again repairs or reinstalls; add
   `-s -- --uninstall` after `bash` to remove it (your data in `~/AFRA/data` stays).

> On Windows, keep the small black window open while you work (closing it stops the app; your work is already
> saved).
>
> **Updates** install themselves while you're not working (Settings → Updates can switch this off). On Windows a yellow
> *Update available* button appears in the sidebar as well.

### Use it with your Claude Pro plan (no API key)
With the [Claude desktop app](https://claude.ai/download), Claude does the thinking and writing on your Claude
Pro/Max plan, and A.F.R.A gives it real papers, your library, your documents and the Word export.
1. In A.F.R.A open **Settings → Connect to Claude** (the Mac installer does this already), then quit and reopen Claude.
2. In Claude, click **+ → A.F.R.A** and pick a workflow: *Coursework / Tugas*, *Literature review*, *Find evidence*,
   *Write a paper*, *Review my draft* or *Make slides*. Or just ask, e.g. "find 10 Q1 papers on monetite cement and add them to my
   library", "write my assignment from document X, APA, 1500 words, then export it to Word".
3. References only come from real databases, land in your A.F.R.A library, and the finished file is saved to
   *Documents/AFRA exports*.

This works in the Claude **desktop** app (Mac and Windows). claude.ai in the browser and the phone app cannot reach
programs on your computer.

A.F.R.A uses the Claude app by default (**Settings → How A.F.R.A uses AI**). A button that needs AI (Paper Writer,
Writing Tools, reading notes…) opens Claude with the request already typed: press **Enter** in Claude, and the answer
appears back in A.F.R.A, in the same place an API answer would. A.F.R.A sends Claude exactly the instructions it would
send an API, so nothing is done twice; with an API key instead (*My own API key*), the same buttons answer directly.
Bigger jobs (Deep Research, Gap Finder, Evidence) run in Claude as one request, and pages refresh by themselves when
Claude adds papers or edits a document.

### Share one library between two people
**Settings → Shared library**: choose **Google Drive** or **iCloud Drive** and click *Start sharing*. Share that folder
with the other person (Google Drive: *Share* → their account as Editor, and they *Add shortcut to My Drive*; iCloud:
*Share → Collaborate*), then they click *Join* in their A.F.R.A. Everything is shared (projects, papers, notes, quotes,
folders, PDFs, documents), on Mac and Windows, within a minute. Settings and API keys stay private. If you both edit
the same document at the same time, both versions are kept. The database itself never sits in the cloud folder: each
change travels as a small file, so cloud syncing can't corrupt it.

### Use it on your phone
**Settings → Use on your phone** opens A.F.R.A to phones and tablets on the same Wi-Fi, protected by a 6-digit PIN.
Read and edit everything except settings, keys and updates. Tip: *Add to Home Screen*.

### Or connect an AI model with an API key
Open **Settings**, choose a provider under *Quick preset*, paste your API key, and click **Test connection**.

| If you have… | Choose preset | Main model (writing) | Fast model (cheap steps) |
|---|---|---|---|
| **OpenRouter** key (recommended) | openrouter | `anthropic/claude-sonnet-5.5` | `google/gemini-3.8-flash` |
| Anthropic key | anthropic | `claude-sonnet-5-5` | `claude-haiku-4-5-20251001` |
| OpenAI key | openai | `gpt-5.6-terra` | `gpt-5.6-luna` |
| Google AI Studio key | gemini | `gemini-3.8-flash` | `gemini-3.5-flash-lite` |
| No key, free and local | ollama | `qwen2.5:14b` | (blank) |

The **fast model** handles query planning, source screening, citation suggestions and PDF details. The **main model**
writes your reports and paper sections. Splitting the work this way usually cuts AI cost by more than half.

---

## What you can do

**Find sources**
- **Discover**, under Literature Search. Paste your research title and get up to 200 relevant papers.
  - The tool splits the title into concepts and their synonyms (e.g. monetite = DCPA = dicalcium phosphate anhydrous)
    and searches several databases with many queries.
  - It follows the citation trails of the best papers, then ranks results by how well they cover your topic.
  - Cost: 1 small AI call.
- **Evidence for my argument.** Write your argument or opinion and get papers whose abstracts *support*,
  *partially support* or *contradict* it. Each comes with the exact sentence that backs it. Click **Add & cite** to
  save the paper and cite it. In the document editor, select a sentence and click **Find evidence**, and the citation
  is inserted right there.
- **Journal quality filters:** Q1–Q4, open access, preprints, minimum citations. Results also show journal
  impact (2-year citedness), h-index, DOAJ and open-access badges. Retracted papers are flagged and hidden.
- **Snowballing:** References, Cited by and Related, for any paper.
- **Journal finder:** which journals publish on your topic, with quartile, open-access status and APC (publication
  fee).
- **Deep Research:** an AI research agent that writes a cited literature report from 20–60 sources. You can limit it
  to Q1–Q2 journals.
- **Databases:** OpenAlex, Europe PMC (includes PubMed), Crossref, arXiv, Semantic Scholar, and web search through
  Tavily.

**Organise**
- **Library:** one per project. Import by DOI, arXiv ID, **book ISBN**, BibTeX or PDF. (In Claude, a photo of a book's
  back cover is enough.)
- **Share a project as a file** (Settings → Project): one `.afra` file with papers, notes, PDFs and documents, for
  group work. Importing never overwrites anything. Get AI reading notes. Export BibTeX or RIS
  (Mendeley, EndNote, Zotero).

**Write**
- **Paper Writer**, a guided workflow:
  1. Journal style guide.
  2. Literature synthesis and research gaps, each gap backed by 3–5 citations.
  3. Outline.
  4. Simulated reviewer check (35 points; 28 needed to pass).
  5. Section-by-section drafting.
  6. Title, abstract and keywords.
  7. Final manuscript review.
- **Coursework (tugas kuliah) outlines:** *New document* → Makalah, Laporan Praktikum, Proposal Penelitian or Resume
  Jurnal, exported in Indonesian campus format: cover with university logo, course, lecturer and students (Nama + NIM),
  Kata Pengantar, Daftar Isi, BAB I…, Daftar Pustaka.
- **Slides:** in any document click **Slides**. *Quick slides*: Claude (chat) builds an editable .pptx with a References
  slide. *Designer slides*: [PPT Master](https://github.com/hugohe3/ppt-master) by Hugo He (MIT) in the **Code** tab of the
  Claude app; A.F.R.A sets it up in one click (folder `AFRA-Slides` in your home folder). Both run on your Claude plan.
- **Documents:** Markdown editor with live preview, citation picker, AI tools on selected text, similarity check
  against your library, readability score and quality check.
- **Writing Tools:** polish, paraphrase, translate EN ↔ ID, find unsupported claims, reviewer critique, response
  letter, and *Ask your library*.

**Finish**
- **Export to Word (.docx)**:
  - Templates: APA 7, IEEE (two-column), journal article, **Skripsi/Tesis** (BAB I, Daftar Isi, Daftar Pustaka),
    **Tugas kuliah** (cover with logo, Kata Pengantar) and research report.
  - Citation styles: APA, IEEE, Harvard, Chicago, Vancouver, MLA.
  - The reference list is generated automatically.

**Track**
- **Token Usage:** every AI call, with tokens and cost, by task, model and day.

### Citing in your text
Write `[@smith2020deep]`, `[@smith2020deep, p. 12; @lee2019]`, or `@smith2020deep found that…`. When you export,
these become *(Smith et al., 2020)* or *[1]* in the style you choose.

---

## Your data is safe
- Everything is saved automatically on your computer, in the `data` folder. Nothing is stored online, apart from the
  questions you send to your AI provider and to the search databases.
- The database uses crash-safe mode. Text you type is also kept in your browser until the save is confirmed, so
  closing the window or a power cut doesn't lose it.
- An automatic backup is made every 6 hours and before anything is deleted. The newest 20 are kept in
  `data/backups`. **Settings → Back up now** makes one at any time.
- To move to a new computer, copy the whole `data` folder.

## Official journal quartiles (optional)
Quartiles marked **"est."** are estimated from OpenAlex citation statistics. For the official **SJR** quartiles:
1. Go to scimagojr.com/journalrank.php and click **Download data**.
2. In **Settings → Journal quartiles**, import that file. Do this once a year.

## Semantic Scholar
Semantic Scholar limits anonymous use heavily. You can do any of these:
- Request a free API key at semanticscholar.org/product/api and paste it in Settings.
- Simply rely on OpenAlex and Europe PMC, which cover most of the same papers and need no key.

AFRA already spaces out its Semantic Scholar requests and caches the answers.

---

## For developers
```bash
pip install -r requirements.txt
python run.py                 # --port 8765 --no-browser --host 127.0.0.1
python tools/package.py       # builds dist/AFRA.zip (keeps the Mac launcher executable)
```
- **Stack:** FastAPI and SQLite on the backend; a single-page frontend in plain JavaScript (`static/`), with no build
  step.
- **Modules** in `app/`: `search` (connectors), `quality` (SJR/OpenAlex journal metrics), `discovery`
  (Discover/Evidence), `research` (deep research), `writer`, `citations`, `docx_export`, `llm` (OpenAI-compatible
  and Anthropic APIs, fast model, prompt caching, usage logging).
- **Claude connector:** `app/mcp_server.py` is a dependency-free MCP server (stdio) that Claude Desktop starts; it
  forwards tool calls to the running server and starts it if needed. `app/claude_connect.py` registers it in
  `claude_desktop_config.json`.
- **Mac install:** `docs/install.sh` (served by GitHub Pages) installs to `~/AFRA`, runs the server as a LaunchAgent
  (`AFRA_SUPERVISED=1`, so updates restart through launchd) and builds the Dock app with `osacompile`.
- **Shared library:** `app/sync.py`. SQLite triggers log every change; each record is written as
  `records/<table>/<id>.json` in the shared folder (newest wins; documents edited on both sides keep a conflict copy).
- **Phone access:** `app/phone.py`, a second uvicorn server on 0.0.0.0:8770 behind a PIN cookie (`AFRA_PHONE_HOST`
  overrides the address for tests).
- **Claude mode:** `app/handoff.py`. With `ai_mode=claude`, `llm.stream_chat` parks the exact prompt as a task when
  the request carries the page's `X-AFRA-Handoff` id (else fails fast so no-AI fallbacks run); Claude fetches it
  with `get_task` and answers with `finish_task`. Connector writes carry `X-AFRA-Source: claude` and appear in
  `/api/events` so pages refresh.
- **Notes behind the heart:** `static/notes.json` (fetched from GitHub every 10 minutes, cached, bundled fallback).
- **Data location:** `RT_DATA_DIR` overrides the data folder.

---

## Copyright

© 2026 **danafarmansyah**. All rights reserved. Crafted with ♥.

A.F.R.A, its name, logo, design and source code are the work of danafarmansyah. See [LICENSE](LICENSE).
