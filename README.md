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
1. Unzip `AFRA.zip`, for example into your Documents folder.
2. **Right-click** **`Start AFRA.command`**, choose **Open**, then **Open** again. You only need to do this
   the first time; macOS asks because the file was downloaded.
   - If Python isn't installed yet, a message explains what to do and the download page opens. Install Python, then
     double-click `Start AFRA.command` again.
3. AFRA opens in your browser. After the first time, a normal double-click is enough.

> Keep the small black window (Windows) or Terminal window (Mac) open while you work. Closing it stops the app,
> and your work is already saved.

### Connect an AI model (one time)
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
- **Library:** one per project. Import by DOI, arXiv ID, BibTeX or PDF. Get AI reading notes. Export BibTeX or RIS
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
- **Documents:** Markdown editor with live preview, citation picker, AI tools on selected text, similarity check
  against your library, readability score and quality check.
- **Writing Tools:** polish, paraphrase, translate EN ↔ ID, find unsupported claims, reviewer critique, response
  letter, and *Ask your library*.

**Finish**
- **Export to Word (.docx)**:
  - Templates: APA 7, IEEE (two-column), journal article, **Skripsi/Tesis** (BAB I, Daftar Isi, Daftar Pustaka) and
    research report.
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
- **Data location:** `RT_DATA_DIR` overrides the data folder.

---

## Copyright

© 2026 **danafarmansyah**. All rights reserved. Crafted with ♥.

A.F.R.A, its name, logo, design and source code are the work of danafarmansyah. See [LICENSE](LICENSE).
