# A.F.R.A plan

## Pending

### Google Slides studio (connect Google Drive, Sheets and Slides)
Turn anything into a finished Google Slides deck: a title, a PDF, papers from the library, notes or a spreadsheet. The AI writes and lays it out; the output is a real, editable deck in your Google Drive. Decks can follow a style you show it (an uploaded deck, a Google Slides link, or a link to a template site such as Slidesgo).

How it differs from what A.F.R.A has today:
- Today, quick slides go through the Claude chat, and designer slides use PPT Master in the Code tab, which outputs .pptx files.
- The library's Google Drive sharing (app/sync.py) only uses the Drive *folder* on the computer. It doesn't talk to Google.
- This feature talks to Google directly, so decks open in Google Slides and are shareable straight away.

#### 1. Connect Google (one time)
- **Settings → Google:** a "Connect Google" button opens Google's sign-in in the browser, then a "Connected as …@gmail.com" line and a Disconnect button.
- **Permissions asked:**
  - Only files A.F.R.A creates or that you pick (Drive "drive.file").
  - Slides.
  - Sheets.
  - It never asks for your whole Drive.
- **Setup work:**
  - A Google Cloud project with the Drive, Slides and Sheets APIs, and an OAuth "desktop app" client.
  - While the app is in Google's "testing" mode, only the people on the test-user list (you and Raina) can sign in, which is fine for us. Publishing for anyone needs Google's verification.
- **Tokens:** sign-in tokens are kept in data/ (never committed), refresh automatically, and are removed on Disconnect.
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
- **PPT Master stays** for when you want a .pptx file. The new studio is the Google-native path, and decks can be downloaded as .pptx or PDF from Google.

#### 6. Build order
1. Connect Google, plus read a Sheet and list templates.
2. Title-or-PDF → outline → deck from one built-in A.F.R.A template.
3. Style from an uploaded deck or Google Slides link, saved as a template.
4. Sheets tables and linked charts.
5. Style from web links (Slidesgo and others).
6. Claude app mode MCP tools, re-do and the review pass.

#### Open questions
- One Google account shared between you and Raina, or each connects their own (templates shared through a shared Drive folder)?
- Should the decks land in a fixed Drive folder ("A.F.R.A Decks") or next to the source files?
