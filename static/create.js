// ================================================================== CREATE INTO PPT (the wizard)
// One page for every deck: Content → Design → Mockup → Review → Create. The draft lives on the server (data/wizard/),
// so closing the page and coming back resumes it. Loaded after app.js and uses its helpers (api, modal, pickDesign…).
const CW = { id: "", st: null, mock: null, sel: 0, preset: null, save: { t: 0, p: null }, poll: 0, job: "", essay: null, prev: null, designs: [] };
const CW_STEPS = [["content", "Content"], ["design", "Design"], ["mockup", "Mockup"], ["review", "Review"], ["create", "Create"]];
const CW_LAYOUT = { title: "Title", section: "Section", content: "Bullets", two_column: "Two columns", image_left: "Image left", image_right: "Image right", figure: "Picture", chart: "Chart", table: "Table", quote: "Quote", references: "References", closing: "Closing" };
const CW_NO_GOOGLE = "Connect a Google account first: Settings › Google.";
const cwLayoutOpts = (cur) => Object.entries(CW_LAYOUT).map(([k, v]) => `<option value="${k}" ${k === cur ? "selected" : ""}>${v}</option>`).join("");

// ---- entry points: other pages call these
function startWizard(body) {  // body: {mode: "doc"|"paper"|"blank"|"topic"|"upload", doc_id?, paper_id?, title?, folder?}
  return api("/api/wizard", { body: { project_id: S.project.id, ...body } }).then((st) => { location.hash = "#create/" + st.id; }).catch((e) => toast(e.message, "bad"));
}
function openWizardIn(folder) { CW.preset = { folder }; location.hash = "#create"; }

VIEWS.create = async () => {
  CW.poll++;  // stops the pollers of an earlier visit
  const id = location.hash.split("/")[1];
  return id ? cwOpen(id) : cwStart();
};

// ---- the start page: pick a way in, or continue a draft
async function cwStart() {
  const [drafts, docs, papers] = await Promise.all([api("/api/wizard"), api(`/api/projects/${S.project.id}/documents?kind=doc`).catch(() => []), api(`/api/projects/${S.project.id}/papers`).catch(() => [])]);
  const pre = CW.preset;
  main().innerHTML = `<div class="page-head"><div><h1>Create into PPT</h1><p>One path for every deck: the content, a design, a plain mockup you check, then the real slides. Nothing is built until you have reviewed the mockup.</p></div></div>
    ${needLLM()}
    ${pre ? `<div class="notice info small">The deck will be saved in <b>${esc(pre.folder || "All Tugas")}</b>. <a href="#create" id="cwnof">Choose later</a></div>` : ""}
    ${drafts.length ? `<div class="card"><h3>Continue where you left off</h3>${drafts.slice(0, 8).map((d) => `<div class="list-item" data-d="${d.id}" style="cursor:default"><div class="row" style="justify-content:space-between">
      <div><div class="t">${esc(d.title)}</div><div class="small muted">${d.done ? "Built" : "Step: " + esc(d.step)} · ${d.slides ? d.slides + " slides · " : ""}${ago(d.updated)}</div></div>
      <div class="row" style="gap:4px"><a class="btn small primary" href="#create/${d.id}">${d.done ? "Open" : "Resume"}</a><button class="btn small ghost" data-dd="${d.id}">Delete</button></div></div></div>`).join("")}</div>` : ""}
    <div class="grid g2">
      <div class="card" style="margin:0"><h3>Topic or description</h3><p class="small muted" style="margin-top:0">A.F.R.A researches it with real paper search and writes an essay first. You read it, change it and approve it, then it becomes slides.</p>
        <textarea id="cwt" rows="4" placeholder="e.g. Heat integration in refineries: what does the evidence say about energy savings?"></textarea>
        <div class="row" style="margin-top:8px"><button class="btn primary" id="cwgo1">Research and write the essay</button></div></div>
      <div class="card" style="margin:0"><h3>Upload documents</h3><p class="small muted" style="margin-top:0">Essays, reports, papers: .txt, .md, .docx, .pdf or a Google Doc. The AI reads them and proposes what goes on the slides.</p>
        <div class="row"><button class="btn primary" id="cwgo2">Start with my documents</button></div></div>
      <div class="card" style="margin:0"><h3>From a document or reading</h3><p class="small muted" style="margin-top:0">One of your A.F.R.A documents or a paper in the library.</p>
        <select id="cwsrc"><option value="">Pick one…</option>${docs.length ? `<optgroup label="Documents">${docs.map((d) => `<option value="doc:${esc(d.id)}">${esc(d.title)}</option>`).join("")}</optgroup>` : ""}
          ${papers.length ? `<optgroup label="Library papers">${papers.map((p) => `<option value="paper:${esc(p.id)}">${esc(p.title || "Untitled")}</option>`).join("")}</optgroup>` : ""}</select>
        <div class="row" style="margin-top:8px"><button class="btn primary" id="cwgo3">Make slides from it</button></div></div>
      <div class="card" style="margin:0"><h3>Blank deck (quick)</h3><p class="small muted" style="margin-top:0">A title, one content slide and a closing slide in the design you pick; you fill in the text.</p>
        <input type="text" id="cwbt" placeholder="Deck title"><div class="row" style="margin-top:8px"><button class="btn primary" id="cwgo4">Start a blank deck</button></div></div>
    </div>`;
  const begin = (body) => api("/api/wizard", { body: { project_id: S.project.id, ...(pre ? { folder: pre.folder } : {}), ...body } }).then((st) => { CW.preset = null; location.hash = "#create/" + st.id; });
  const nof = $("#cwnof"); if (nof) nof.onclick = () => { CW.preset = null; };
  $("#cwgo1").onclick = (e) => busy(e.currentTarget, () => { if ($("#cwt").value.trim().length < 8) return toast("Write the topic, or a few lines about what the deck should cover"); return begin({ mode: "topic", topic: $("#cwt").value }); });
  $("#cwgo2").onclick = (e) => busy(e.currentTarget, () => begin({ mode: "upload" }));
  $("#cwgo3").onclick = (e) => busy(e.currentTarget, () => {
    const [kind, id] = ($("#cwsrc").value || ":").split(":"); if (!id) return toast("Pick a document or a paper first");
    return begin(kind === "doc" ? { mode: "doc", doc_id: id } : { mode: "paper", paper_id: id });
  });
  $("#cwgo4").onclick = (e) => busy(e.currentTarget, () => begin({ mode: "blank", title: $("#cwbt").value.trim() || "New deck" }));
  $$("[data-dd]").forEach((b) => b.onclick = async () => { if (!confirm("Delete this draft? Decks already built stay where they are.")) return; await api("/api/wizard/" + b.dataset.dd, { method: "DELETE" }); cwStart(); });
}

// ---- the wizard frame
async function cwOpen(id) {
  try { CW.st = await api("/api/wizard/" + id); } catch (e) { toast(e.message, "bad"); location.hash = "#create"; return; }
  Object.assign(CW, { id, mock: CW.st.mockup, essay: null, prev: null, job: CW.id === id ? CW.job : "" });
  if (CW.mock && CW.sel >= CW.mock.slides.length) CW.sel = 0;
  cwFrame();
}
const cwSourceReady = (st) => st.source.type === "essay" ? st.essay_info.approved : st.source.type === "uploads" ? st.materials.length > 0 : true;
const cwCan = (k) => k === "content" || (k === "design" && cwSourceReady(CW.st)) || (k === "mockup" && cwSourceReady(CW.st)) || (k === "review" && !!CW.mock) || (k === "create" && (!!CW.st.result || !!CW.job));
async function cwPut(body) { CW.st = await api("/api/wizard/" + CW.id, { method: "PUT", body }); return CW.st; }
async function cwGo(step) { await cwFlush(); if (CW.st.step !== step) await cwPut({ step }); cwFrame(); }
function cwFrame() {
  const st = CW.st, at = CW_STEPS.findIndex(([k]) => k === st.step);
  main().innerHTML = `<div class="page-head"><div><h1>Create into PPT</h1><p>${esc(st.title || "Untitled deck")}</p></div>
    <div class="row"><a class="btn" href="#create">All drafts</a><button class="btn ghost" id="cwdel">Delete draft</button></div></div>
    <div class="cw-steps">${CW_STEPS.map(([k, l], n) => `<button class="cw-step ${n === at ? "on" : n < at ? "done" : ""}" data-s="${k}" ${cwCan(k) || n === at ? "" : "disabled"}><b>${n < at ? "✓" : n + 1}</b> ${l}</button>`).join("")}</div>
    <div id="cwbody"></div>
    <div class="cw-nav"><button class="btn" id="cwback">Back</button><span class="grow small muted" id="cwhint"></span><button class="btn primary" id="cwnext">Next</button></div>`;
  $$(".cw-step").forEach((b) => b.onclick = () => cwGo(b.dataset.s));
  $("#cwdel").onclick = async () => { if (!confirm("Delete this draft? Decks already built stay where they are.")) return; await api("/api/wizard/" + CW.id, { method: "DELETE" }); location.hash = "#create"; };
  ({ content: cwContent, design: cwDesign, mockup: cwMockup, review: cwReview, create: cwCreate })[st.step]();
}
// back / next for the current step: {back: fn|null, next: fn|null, label, hint}
function cwNav({ back, next, label = "Next", hint = "", hide = false }) {
  const b = $("#cwback"), n = $("#cwnext");
  b.style.visibility = back ? "visible" : "hidden"; b.onclick = () => back && back();
  n.style.display = hide ? "none" : ""; n.textContent = label; n.disabled = !next; n.onclick = (e) => next && busy(e.currentTarget, next);
  $("#cwhint").textContent = hint;
}
const cwPrevStep = () => CW_STEPS[Math.max(0, CW_STEPS.findIndex(([k]) => k === CW.st.step) - 1)][0];

// ---- saving: text edits are queued, structural changes go to the server one at a time
function cwQueueSave() {
  clearTimeout(CW.save.t); $("#cwhint") && ($("#cwhint").textContent = "Saving…");
  CW.save.t = setTimeout(() => { CW.save.p = cwSaveNow(); }, 700);
}
async function cwSaveNow() {
  clearTimeout(CW.save.t); CW.save.t = 0;
  try { CW.st = await api(`/api/wizard/${CW.id}/mockup`, { method: "PUT", body: CW.mock }); const h = $("#cwhint"); if (h) h.textContent = "Saved"; }
  catch (e) { toast(e.message, "bad"); }
}
async function cwFlush() { if (CW.save.t) await cwSaveNow(); else if (CW.save.p) await CW.save.p; CW.save.p = null; }

// ================================================================== 1 · Content
const cwOptionsHTML = (o) => `<div class="card"><h3>Options</h3><div class="grid g3" style="gap:10px">
  <div><label class="f">Number of slides</label><input type="number" id="cwo_slides" min="3" max="40" value="${o.slides}"></div>
  <div><label class="f">Style</label><select id="cwo_tone"><option value="academic" ${o.tone === "academic" ? "selected" : ""}>Academic</option><option value="punchy" ${o.tone === "punchy" ? "selected" : ""}>Punchy</option></select></div>
  <div><label class="f">Language</label><select id="cwo_language"><option value="">Same as the content</option>${["English", "Indonesian"].map((l) => `<option ${o.language === l ? "selected" : ""}>${l}</option>`).join("")}</select></div></div>
  <label class="f">Audience</label><input type="text" id="cwo_audience" value="${esc(o.audience)}" placeholder="e.g. thesis committee, class, conference session">
  <label class="row small" style="margin-top:8px;gap:6px"><input type="checkbox" id="cwo_notes" ${o.notes ? "checked" : ""}> Speaker notes</label></div>`;
function cwWireOptions(root) {
  const save = debounce(() => cwPut({ options: { slides: +$("#cwo_slides", root).value || 10, tone: $("#cwo_tone", root).value, language: $("#cwo_language", root).value, audience: $("#cwo_audience", root).value, notes: $("#cwo_notes", root).checked } }).catch((e) => toast(e.message, "bad")), 500);
  $$("#cwo_slides,#cwo_tone,#cwo_language,#cwo_audience,#cwo_notes", root).forEach((x) => { x.oninput = save; x.onchange = save; });
}
function cwContent() {
  const st = CW.st, t = st.source.type, el = $("#cwbody");
  const titleBox = `<label class="f">Deck title <small>(optional: the AI suggests one)</small></label><input type="text" id="cwtitle" value="${esc(st.title)}">`;
  const wireTitle = () => { const x = $("#cwtitle"); if (x) x.oninput = debounce(() => cwPut({ title: x.value }).catch(() => { }), 500); };
  if (t === "essay") { cwEssayStep(el); return; }
  if (t === "uploads") { cwUploadStep(el); return; }
  let info = "";
  if (t === "doc") info = `<p>The slides are written from your A.F.R.A document <b>${esc(st.topic)}</b> (and the library papers it cites). <a href="#docs/${esc(st.source.id)}">Open the document</a></p>`;
  else if (t === "paper") info = `<p>The slides are written from the reading <b>${esc(st.topic)}</b>: its abstract, your notes and its full text if you fetched it. It is cited as a library source. <a href="#library">Open the library</a></p>`;
  else info = `<p>A blank deck: a title slide, one content slide and a closing slide. You write the text in the mockup step, so no AI is needed.</p>`;
  el.innerHTML = `<div class="card"><h3>Source</h3>${info}${titleBox}</div>${t === "blank" ? "" : cwOptionsHTML(st.options)}`;
  wireTitle(); if (t !== "blank") cwWireOptions(el);
  cwNav({ next: () => cwGo("design"), label: "Next: design" });
}

// -- the essay (topic or description first)
function cwEssayStep(el) {
  const st = CW.st, o = st.options;
  el.innerHTML = `<div class="card"><h3>What should the deck cover?</h3>
    <textarea id="cwtopic" rows="4" placeholder="A topic, or a few paragraphs about what the deck should say">${esc(st.topic)}</textarea>
    <label class="f">Deck title <small>(optional)</small></label><input type="text" id="cwtitle" value="${esc(st.title)}"></div>
    ${cwOptionsHTML(o)}
    <div class="card"><h3>The essay</h3><div id="cwessay"><div class="small muted">Checking…</div></div></div>`;
  const savetop = debounce(() => cwPut({ topic: $("#cwtopic").value, title: $("#cwtitle").value }).catch(() => { }), 500);
  $("#cwtopic").oninput = savetop; $("#cwtitle").oninput = savetop;
  cwWireOptions(el);
  cwEssayRefresh();
}
async function cwEssayRefresh() {
  const my = ++CW.poll;
  const box = $("#cwessay"); if (!box) return;
  let e; try { e = await api(`/api/wizard/${CW.id}/essay`); } catch (err) { box.innerHTML = `<div class="notice bad">${esc(err.message)}</div>`; return; }
  if (my !== CW.poll || !box.isConnected) return;
  CW.essay = e;
  cwEssayDraw(e);
  if (e.status === "running" || e.status === "claude") setTimeout(() => { if (my === CW.poll) cwEssayRefresh(); }, e.status === "running" ? 2500 : 4000);
}
function cwEssayNav(e) {
  const ok = e.approved;
  cwNav({ next: ok ? () => cwGo("design") : null, label: "Create into PPT →", hide: !ok, hint: ok ? "" : e.doc_id ? "Approve the essay to continue." : "" });
}
async function cwDocOptions(selectedHtml = "") {
  const docs = await api(`/api/projects/${S.project.id}/documents?kind=doc`).catch(() => []);
  return `<select id="cwpick"><option value="">Pick a document…</option>${docs.map((d) => `<option value="${esc(d.id)}">${esc(d.title)}</option>`).join("")}</select>`;
}
async function cwEssayDraw(e) {
  const box = $("#cwessay"); if (!box) return;
  cwEssayNav(e);
  const attach = async () => `<details style="margin-top:12px"><summary class="small">Use a document I already have as the essay</summary><div class="row" style="margin-top:6px">${await cwDocOptions()}<button class="btn small" id="cwattach">Use it</button></div></details>`;
  const wireAttach = () => { const b = $("#cwattach"); if (b) b.onclick = () => busy(b, async () => { if (!$("#cwpick").value) return toast("Pick a document first"); CW.st = await api(`/api/wizard/${CW.id}/essay/attach`, { body: { doc_id: $("#cwpick").value } }); cwEssayRefresh(); }); };
  if (e.status === "running") {
    const p = e.progress || {};
    box.innerHTML = `<div class="statusbar"><span class="spin"></span> ${p.writing ? "Writing the essay…" : "Researching…"} ${p.queries ? `${p.queries} searches · ${p.sources} sources · ${p.learnings} findings` : ""}</div>
      <div class="small muted">It searches real papers and cites only those, and adds them to your library. This takes a few minutes; you can leave this page and come back.</div>`;
    return;
  }
  if (e.status === "claude" && !e.doc_id) {
    let cands = []; try { cands = await api(`/api/wizard/${CW.id}/essay/candidates`); } catch { }
    if (!$("#cwessay")) return;
    box.innerHTML = `<div class="notice info"><span class="spin"></span> <b>Claude is writing the essay.</b> It searches real papers, cites them, and saves the essay as a document here. When it has, pick it below.</div>
      ${cands.length ? cands.map((c) => `<div class="list-item" style="cursor:default"><div class="row" style="justify-content:space-between"><div><div class="t">${esc(c.title)}</div><div class="small muted">${ago(c.updated)}</div></div><button class="btn small primary" data-use="${c.id}">Use as the essay</button></div></div>`).join("") : `<div class="small muted">No new document yet.</div>`}
      <div class="row" style="margin-top:8px"><button class="btn small" id="cwagain">Show the request again</button></div>${await attach()}`;
    $$("[data-use]", box).forEach((b) => b.onclick = () => busy(b, async () => { CW.st = await api(`/api/wizard/${CW.id}/essay/attach`, { body: { doc_id: b.dataset.use } }); cwEssayRefresh(); }));
    $("#cwagain").onclick = (ev) => busy(ev.currentTarget, async () => { const r = await api(`/api/wizard/${CW.id}/essay/start`, { body: {} }); if (r.prompt) showHandoff(r.prompt); });
    wireAttach(); return;
  }
  if (!e.doc_id) {
    box.innerHTML = `${e.status === "error" ? `<div class="notice bad">${esc(e.error)}</div>` : ""}<p class="small muted" style="margin-top:0">A.F.R.A researches the topic with its real paper search, writes an essay with real citations, and saves it as a document. You read it, change it and approve it before any slide exists.</p>
      <div class="row"><button class="btn primary" id="cwwrite">Write the essay</button></div>${await attach()}`;
    $("#cwwrite").onclick = (ev) => busy(ev.currentTarget, async () => {
      if ($("#cwtopic").value.trim().length < 8) return toast("Write the topic, or a few lines about what the deck should cover");
      await cwPut({ topic: $("#cwtopic").value, title: $("#cwtitle").value });
      const r = await api(`/api/wizard/${CW.id}/essay/start`, { body: {} });
      if (r.mode === "claude") showHandoff(r.prompt);
      cwEssayRefresh();
    });
    wireAttach(); return;
  }
  // the essay exists: read it, edit it, ask for changes, export it, approve it
  const d = await api("/api/documents/" + e.doc_id);
  if (!$("#cwessay")) return;
  await refreshLibKeys().catch(() => { });
  let view = "edit", text = d.content;
  box.innerHTML = `<div class="row" style="justify-content:space-between;margin-bottom:8px"><div class="row"><b>${esc(d.title)}</b> <span class="pill gray" id="cwwc">${wordCount(text).toLocaleString()} words</span>
      <span id="cwap">${e.approved ? '<span class="pill ok">Approved</span>' : e.changed_after_approval ? '<span class="pill warn">Changed since you approved it</span>' : '<span class="pill warn">Not approved yet</span>'}</span></div>
      <div class="row" style="gap:4px"><button class="btn small" id="cwview">Preview</button><button class="btn small" data-ex="pdf">PDF</button><button class="btn small" data-ex="docx">Word</button><button class="btn small" id="cwgdoc">Google Doc</button><a class="btn small ghost" href="#docs/${esc(d.id)}">Open in Documents</a></div></div>
    <textarea id="cwtext" rows="18" spellcheck="true">${esc(text)}</textarea><div class="prose hidden" id="cwprev" style="max-height:60vh;overflow:auto"></div>
    <div class="row" style="margin-top:10px"><input type="text" id="cwask" class="grow" placeholder="Ask for changes: shorter, more on costs, add a counter-argument, simpler words…"><button class="btn" id="cwaskgo">Revise</button><button class="btn ghost hidden" id="cwundo">Undo</button></div>
    <div class="row" style="margin-top:12px"><button class="btn primary" id="cwapprove">${e.approved ? "Approved ✓" : "Approve essay"}</button>
      <span class="small muted">${e.approved ? "You can go on: Create into PPT." : "Create into PPT appears once you approve the essay. Changing the text afterwards asks for a new approval."}</span></div>`;
  const ta = $("#cwtext"), prev = $("#cwprev");
  const showApproval = (a) => {
    $("#cwap").innerHTML = a.approved ? '<span class="pill ok">Approved</span>' : a.changed_after_approval ? '<span class="pill warn">Changed since you approved it</span>' : '<span class="pill warn">Not approved yet</span>';
    $("#cwapprove").textContent = a.approved ? "Approved ✓" : "Approve essay"; cwEssayNav({ ...CW.essay, ...a });
  };
  const saveText = debounce(async () => {
    try { await api("/api/documents/" + d.id, { method: "PUT", body: { content: ta.value } }); showApproval((await api("/api/wizard/" + CW.id)).essay_info); } catch (err) { toast(err.message, "bad"); }
  }, 800);
  ta.oninput = () => { text = ta.value; $("#cwwc").textContent = wordCount(text).toLocaleString() + " words"; showApproval({ approved: false, changed_after_approval: CW.essay.approved || CW.essay.changed_after_approval, doc_id: d.id }); saveText(); };
  $("#cwview").onclick = () => { view = view === "edit" ? "preview" : "edit"; ta.classList.toggle("hidden", view === "preview"); prev.classList.toggle("hidden", view === "edit"); if (view === "preview") prev.innerHTML = md(ta.value); $("#cwview").textContent = view === "edit" ? "Preview" : "Edit"; };
  $$("[data-ex]", box).forEach((b) => b.onclick = () => busy(b, async () => { await saveText.flush?.(); await api("/api/documents/" + d.id, { method: "PUT", body: { content: ta.value } }); await cwDownload(`/api/wizard/${CW.id}/essay/export?fmt=${b.dataset.ex}`); }));
  $("#cwgdoc").onclick = (ev) => busy(ev.currentTarget, async () => {
    try { await api("/api/documents/" + d.id, { method: "PUT", body: { content: ta.value } }); const r = await api(`/api/wizard/${CW.id}/essay/google-doc`, { body: {} }); toast("Saved as a Google Doc in A.F.R.A Decks", "ok"); window.open(r.url, "_blank"); }
    catch (err) { cwNeedGoogle(err.message); }
  });
  $("#cwaskgo").onclick = (ev) => busy(ev.currentTarget, async () => {
    const instr = $("#cwask").value.trim(); if (!instr) return toast("Say what to change");
    await api("/api/documents/" + d.id, { method: "PUT", body: { content: ta.value } });
    const r = await api(`/api/wizard/${CW.id}/essay/revise`, { body: { instruction: instr } });
    CW.prev = r.previous; ta.value = r.content; text = r.content; $("#cwwc").textContent = wordCount(text).toLocaleString() + " words"; $("#cwask").value = ""; $("#cwundo").classList.remove("hidden");
    if (r.unknown_keys.length) toast("These citations are not in your library: " + r.unknown_keys.join(", "), "bad");
    showApproval((await api("/api/wizard/" + CW.id)).essay_info);
  });
  $("#cwundo").onclick = () => busy($("#cwundo"), async () => { if (CW.prev == null) return; ta.value = CW.prev; await api("/api/documents/" + d.id, { method: "PUT", body: { content: ta.value } }); CW.prev = null; $("#cwundo").classList.add("hidden"); showApproval((await api("/api/wizard/" + CW.id)).essay_info); });
  $("#cwapprove").onclick = (ev) => busy(ev.currentTarget, async () => {
    await api("/api/documents/" + d.id, { method: "PUT", body: { content: ta.value } });
    CW.st = await api(`/api/wizard/${CW.id}/essay/approve`, { body: { approved: true } });
    CW.essay = { ...CW.essay, ...CW.st.essay_info }; showApproval(CW.st.essay_info); toast("Essay approved", "ok");
  });
}
async function cwDownload(url) {
  const r = await api(url);
  const cd = r.headers.get("content-disposition") || "", m = cd.match(/filename\*=UTF-8''([^;]+)/);
  download(await r.blob(), m ? decodeURIComponent(m[1]) : "essay");
}
function cwNeedGoogle(msg) {
  if (!/Google account/.test(msg)) return toast(msg, "bad");
  const box = modal(`<h2>Connect Google first</h2><p>${esc(msg)}</p><div class="modal-foot"><button class="btn" id="cwx">Close</button><button class="btn primary" id="cwok">Open Settings</button></div>`);
  $("#cwx", box).onclick = closeModal; $("#cwok", box).onclick = () => { closeModal(); location.hash = "#settings"; };
}

// -- uploaded documents
function cwUploadStep(el) {
  const st = CW.st, a = st.analysis;
  el.innerHTML = `<div class="card"><h3>Your documents</h3>
    <div class="row"><label class="btn" style="cursor:pointer">Add files<input type="file" id="cwfiles" multiple accept=".txt,.md,.markdown,.docx,.pdf" hidden></label>
      <input type="text" id="cwglink" class="grow" placeholder="…or a Google Doc link"><button class="btn" id="cwgadd">Add Google Doc</button></div>
    <div class="small muted" style="margin-top:4px">.txt, .md, .docx and .pdf (a scanned PDF has no text to read). A Google Doc is read through your Google connection.</div>
    <div id="cwfl" style="margin-top:8px"></div><label class="f">Deck title <small>(optional)</small></label><input type="text" id="cwtitle" value="${esc(st.title)}"></div>
    <div class="card"><div class="row" style="justify-content:space-between"><h3 style="margin:0">What the AI found</h3><button class="btn primary" id="cwanalyze">${a ? "Analyse again" : "Analyse the documents"}</button></div>
      <div id="cwan" style="margin-top:10px"></div></div>${cwOptionsHTML(st.options)}`;
  const files = () => { $("#cwfl").innerHTML = CW.st.materials.length ? CW.st.materials.map((m) => `<span class="pill gray" style="margin-right:6px">${esc(m.name)} <button data-rm="${m.id}" title="Remove" style="border:0;background:none;cursor:pointer;color:inherit">×</button></span>`).join("") : `<div class="small muted">No documents yet.</div>`;
    $$("[data-rm]").forEach((b) => b.onclick = async () => { await api(`/api/wizard/${CW.id}/materials/${b.dataset.rm}`, { method: "DELETE" }); CW.st = await api("/api/wizard/" + CW.id); files(); analysis(); nav(); }); };
  const analysis = () => {
    const a = CW.st.analysis;
    $("#cwan").innerHTML = a ? `<p style="margin-top:0">${esc(a.summary)}</p>${[["Key points", a.key_points], ["Figures worth showing", a.figures], ["Tables worth showing", a.tables]].filter(([, l]) => l.length).map(([h, l]) => `<h3 class="small muted" style="margin:8px 0 2px">${h}</h3><ul style="margin:0;padding-left:20px">${l.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`).join("")}
      ${a.quotes.length ? `<h3 class="small muted" style="margin:8px 0 2px">Quotes worth a slide</h3>${a.quotes.map((q) => `<div class="small"><i>“${esc(q.text)}”</i> ${q.by ? "— " + esc(q.by) : ""}</div>`).join("")}` : ""}
      <div class="small muted" style="margin-top:8px">You can change all of this in the mockup step.</div>` : `<div class="small muted">Add your documents, then analyse them: the AI says what could go on slides (key points, figures, tables, quotes). The mockup is built from that.</div>`;
  };
  const nav = () => cwNav({ next: CW.st.materials.length ? async () => {
    if (!CW.st.analysis) { try { await api(`/api/wizard/${CW.id}/analyze`, { body: {} }); CW.st = await api("/api/wizard/" + CW.id); } catch (e) { if (!e.handoff) toast("The documents weren't analysed: " + e.message, "bad"); } }
    await cwGo("design");
  } : null, label: "Next: design", hint: CW.st.materials.length ? "" : "Add at least one document." });
  files(); analysis(); nav(); cwWireOptions(el);
  $("#cwtitle").oninput = debounce(() => cwPut({ title: $("#cwtitle").value }).catch(() => { }), 500);
  $("#cwfiles").onchange = async (e) => {
    for (const f of e.target.files) { const fd = new FormData(); fd.append("file", f); try { await api(`/api/wizard/${CW.id}/materials`, { body: fd }); } catch (err) { toast(`${f.name}: ${err.message}`, "bad"); } }
    e.target.value = ""; CW.st = await api("/api/wizard/" + CW.id); files(); analysis(); nav();
  };
  $("#cwgadd").onclick = (e) => busy(e.currentTarget, async () => {
    try { await api(`/api/wizard/${CW.id}/materials/google-doc`, { body: { link: $("#cwglink").value } }); } catch (err) { return cwNeedGoogle(err.message); }
    $("#cwglink").value = ""; CW.st = await api("/api/wizard/" + CW.id); files(); analysis(); nav();
  });
  $("#cwanalyze").onclick = (e) => busy(e.currentTarget, async () => {
    if (!CW.st.materials.length) return toast("Add a document first");
    await api(`/api/wizard/${CW.id}/analyze`, { body: {} }); CW.st = await api("/api/wizard/" + CW.id); analysis(); $("#cwanalyze").textContent = "Analyse again";
  });
}

// ================================================================== 2 · Design
async function cwDesign() {
  const el = $("#cwbody");
  el.innerHTML = `<div class="card"><div class="small muted">Loading designs…</div></div>`;
  const list = (await api("/api/designs")).designs; CW.designs = list;
  const chosen = list.find((d) => d.id === CW.st.design) || list.find((d) => d.default) || list[0];
  const drawn = () => cwDesign();
  const pick = async (d) => { await cwPut({ design: d.id }); drawn(); };
  el.innerHTML = `<div class="card"><h3>Design</h3><div class="row" style="gap:14px;align-items:flex-start"><div style="width:220px">${chosen ? pdCard(chosen, true) : ""}</div>
      <div class="grow"><div class="small muted" style="margin-bottom:8px">${CW.st.design ? "The deck is built in this design." : "No design chosen: the default one is used."} A design changes colours and fonts only; the mockup decides the content.</div>
      <button class="btn primary" id="cwpick">Pick from my designs</button></div></div></div>
    <div class="card"><h3>Provide one now</h3><div class="grid g3" style="gap:12px">
      <div><b class="small">A PowerPoint (.pptx)</b><div class="small muted" style="margin:2px 0 6px">Any deck or template. It is saved in the Designs folder.</div><input type="file" id="cwdp" accept=".pptx"></div>
      <div><b class="small">Slides as a PDF</b><div class="small muted" style="margin:2px 0 6px">Colours and fonts are read from the file. Pictures of pages need an OpenRouter key (GLM vision).</div><input type="file" id="cwdf" accept=".pdf"></div>
      <div><b class="small">A link</b><div class="small muted" style="margin:2px 0 6px">Free templates: Slidesgo, SlidesCarnival, Microsoft, a Google Slides deck.</div>
        <input type="text" id="cwdl" placeholder="https://…"><button class="btn small" id="cwdla" style="margin-top:6px">Add</button></div></div>
      <div id="cwdres" class="small" style="margin-top:10px"></div></div>`;
  $("#cwpick").onclick = async () => { const d = await pickDesign({ selected: chosen && chosen.id, title: "Pick a design", button: "Use this design" }); if (d) pick(d); };
  const fresh = (before) => (CW.designs.find((d) => !before.has(d.id)) || null);
  $("#cwdp").onchange = (e) => busy(e.target, async () => { const fd = new FormData(); fd.append("file", e.target.files[0]); const d = await api("/api/designs/pptx", { body: fd }); toast(`Added "${d.name}"`, "ok"); await pick(d); });
  $("#cwdf").onchange = (e) => busy(e.target, async () => {
    const fd = new FormData(); fd.append("file", e.target.files[0]);
    $("#cwdres").innerHTML = `<span class="spin"></span> Reading the PDF…`;
    try { const r = await api("/api/designs/pdf", { body: fd }); await cwPut({ design: r.design.id }); await drawn(); $("#cwdres").innerHTML = `<div class="notice ok small">${esc(r.message)}</div>`; }
    catch (err) { $("#cwdres").innerHTML = `<div class="notice bad small">${esc(err.message)}</div>`; }
  });
  $("#cwdla").onclick = (e) => busy(e.currentTarget, async () => {
    if (!$("#cwdl").value.trim()) return toast("Paste a link first");
    const before = new Set(list.map((d) => d.id));
    await designImport($("#cwdl").value, $("#cwdres"));
    CW.designs = (await api("/api/designs")).designs;
    const d = fresh(before); if (d) await pick(d); else toast("Nothing was added: see the message above", "bad");
  });
  cwNav({ back: () => cwGo("content"), next: () => cwGo("mockup"), label: "Next: mockup" });
}

// ================================================================== 3 · Mockup (plain wireframes, no design)
const cwSet = (obj, path, v) => { const k = path.split("."); let o = obj; k.slice(0, -1).forEach((p) => { if (o[p] == null) o[p] = isNaN(+k[k.indexOf(p) + 1]) ? {} : []; o = o[p]; }); o[k[k.length - 1]] = v; };
const cwLines = (s) => String(s || "").split("\n").map((x) => x.trim()).filter(Boolean);
const cwWF = (s, edit) => `<div class="wf-in">${cwWFin(s, edit)}</div>`;
function cwWFin(s, edit) {
  const ed = (f, one = true) => edit ? ` contenteditable="true" spellcheck="true" data-f="${f}" ${one ? 'data-one="1"' : ""}` : "";
  const T = (text, f, cls, ph) => `<div class="wf-t ${cls}"${ed(f)} data-ph="${esc(ph)}">${esc(text || "")}</div>`;
  const UL = (items, f) => `<ul class="wf-ul"${ed(f, false)} data-ph="Type a point">${(items && items.length ? items : [""]).map((b) => `<li>${esc(b)}</li>`).join("")}</ul>`;
  const IMG = (s) => `<div class="wf-img">${s.figure && s.figure.url ? "Picture linked" : "▭ Picture slot"}${s.figure && s.figure.description ? `<br><span>${esc(s.figure.description)}</span>` : ""}</div>`;
  const head = T(s.title, "title", "title", "Slide title");
  const cap = (s.caption || edit) ? T(s.caption, "caption", "cap", "Caption") : "";
  const q = s.quote || {}, ch = s.chart || {}, tb = s.table || {};
  switch (s.layout) {
    case "title": case "closing": return `<div class="wf-hero">${T(s.title, "title", "wfh", "Title")}${T(s.subtitle, "subtitle", "sub", "Subtitle")}</div>`;
    case "section": return `<div class="wf-hero">${T(s.title, "title", "wfh", "Section title")}${T(s.subtitle, "subtitle", "sub", "One line")}</div>`;
    case "two_column": return `${head}<div class="wf-cols">${["left", "right"].map((c) => `<div>${T((s[c] || {}).heading, c + ".heading", "h", "Heading")}${UL((s[c] || {}).bullets, c + ".bullets")}</div>`).join("")}</div>`;
    case "image_left": return `${head}<div class="wf-row">${IMG(s)}<div>${UL(s.bullets, "bullets")}</div></div>${cap}`;
    case "image_right": return `${head}<div class="wf-row"><div>${UL(s.bullets, "bullets")}</div>${IMG(s)}</div>${cap}`;
    case "figure": return `${head}${IMG(s)}${cap}`;
    case "chart": {
      const mx = Math.max(1, ...(ch.values || []).map(Math.abs));
      return `${head}${(ch.values || []).length ? `<div class="wf-bars">${ch.values.map((v, i) => `<div class="wf-bar" style="height:${Math.max(4, Math.abs(v) / mx * 100)}%"><span>${esc(v)}</span><em>${esc((ch.labels || [])[i] || "")}</em></div>`).join("")}</div>` : `<div class="wf-img">▭ Chart slot<br><span>add numbers below</span></div>`}${cap}`;
    }
    case "table": return `${head}<table class="wf-tb">${(tb.header && tb.header.length ? `<tr>${tb.header.map((h, c) => `<th${ed(`table.header.${c}`)}>${esc(h)}</th>`).join("")}</tr>` : "")}${(tb.rows || []).map((r, ri) => `<tr>${r.map((c, ci) => `<td${ed(`table.rows.${ri}.${ci}`)}>${esc(c)}</td>`).join("")}</tr>`).join("") || `<tr><td>…</td></tr>`}</table>${cap}`;
    case "quote": return `${s.title || edit ? head : ""}<div class="wf-qq">${T(q.text, "quote.text", "q", "The quotation")}${T(q.by, "quote.by", "by", "Who said it")}</div>`;
    default: return `${head}${UL(s.bullets, "bullets")}`;  // content, references
  }
}
const cwThumb = (s, n) => `<div class="wf" data-l="${s.layout}">${cwWF(s, false)}</div>`;
function cwReadWF(root, s) {
  $$("[data-f]", root).forEach((el) => {
    const f = el.dataset.f;
    if (el.tagName === "UL") cwSet(s, f, $$("li", el).map((li) => li.innerText.trim()).filter(Boolean));
    else cwSet(s, f, el.innerText.replace(/\s*\n+\s*/g, " ").trim());
  });
}
async function cwMockup() {
  const el = $("#cwbody");
  if (!CW.mock) { cwGenerate(); return; }
  const m = CW.mock, n = m.slides.length;
  CW.sel = Math.max(0, Math.min(CW.sel, n - 1));
  el.innerHTML = `<div class="cw-mock"><div class="card cw-list"><div class="row" style="margin-bottom:8px"><select id="cwaddl" style="width:auto;flex:1">${cwLayoutOpts("content")}</select><button class="btn small" id="cwadd">+ Add</button></div>
      <div id="cwsl"></div><div class="small muted" style="margin-top:6px">Drag slides to reorder.</div>
      <div class="row" style="margin-top:8px"><button class="btn small ghost" id="cwregen">Write all again</button></div></div>
    <div id="cwedit"></div></div>`;
  cwDrawList(); cwDrawEdit();
  $("#cwadd").onclick = () => cwOp({ op: "add", at: CW.sel + 1, layout: $("#cwaddl").value }, () => CW.sel + 1);
  $("#cwregen").onclick = () => { if (confirm("Write the whole mockup again? Your edits to it are replaced.")) { CW.mock = null; cwGenerate(true); } };
  cwNav({ back: () => cwGo("design"), next: () => cwGo("review"), label: "Next: review", hint: "Saved" });
}
async function cwGenerate(again) {
  const el = $("#cwbody");
  el.innerHTML = `<div class="card"><div class="statusbar"><span class="spin"></span> Writing the wireframes from your ${CW.st.source.type === "blank" ? "title" : "content"}…</div>
    <div class="small muted">${claudeMode() && CW.st.source.type !== "blank" ? "This runs in the Claude app: press Enter there when it opens." : "Two AI steps: the outline, then each slide. Nothing is designed yet."}</div></div>`;
  cwNav({ back: () => cwGo("design"), next: null });
  try {
    // a blank source needs no AI, so it must not wait for the Claude app (api() would hand every /mockup POST over)
    CW.st = CW.st.source.type === "blank" ? await (await fetch(`/api/wizard/${CW.id}/mockup`, { method: "POST" })).json() : await api(`/api/wizard/${CW.id}/mockup`, { body: {} });
    if (CW.st.detail) throw new Error(CW.st.detail);
    CW.mock = CW.st.mockup; CW.sel = 0;
    const fl = (CW.st.mockup_notes || {}).missing || [];
    if (fl.length) toast("Cited but not in your library: " + fl.join(", "), "bad");
    if ($("#cwbody")) cwMockup();
  } catch (e) {
    if (!$("#cwbody")) return;
    el.innerHTML = `<div class="card"><div class="notice bad">${e.handoff ? "Continued in the Claude app. When Claude has answered, click Try again." : esc(e.message)}</div>
      <div class="row"><button class="btn primary" id="cwretry">Try again</button><button class="btn" id="cwskip">Start from a blank mockup</button></div></div>`;
    $("#cwretry").onclick = () => cwGenerate();
    $("#cwskip").onclick = async () => { CW.st = await api("/api/wizard/" + CW.id); CW.mock = { version: 1, title: CW.st.title, slides: [] }; await cwOpBlank(); };
  }
}
async function cwOpBlank() {  // a mockup of one empty slide to start from by hand
  CW.mock = { version: 1, title: CW.st.title || "New deck", slides: [{ layout: "title", title: CW.st.title || "New deck", subtitle: "" }, { layout: "content", title: "Slide title", bullets: ["First point"] }, { layout: "closing", title: "Thank you", subtitle: "" }] };
  CW.st = await api(`/api/wizard/${CW.id}/mockup`, { method: "PUT", body: CW.mock }); CW.mock = CW.st.mockup; CW.sel = 0; cwMockup();
}
function cwDrawList() {
  const box = $("#cwsl"); if (!box) return;
  box.innerHTML = CW.mock.slides.map((s, i) => `<div class="cw-item ${i === CW.sel ? "sel" : ""}" draggable="true" data-i="${i}"><span class="cw-n">${i + 1}</span>${cwThumb(s, i)}
    <div class="cw-acts"><button data-a="dup" title="Duplicate">⧉</button><button data-a="merge" title="Merge with the next slide" ${i === CW.mock.slides.length - 1 ? "disabled" : ""}>⤓</button><button data-a="del" title="Delete">×</button></div></div>`).join("");
  $$(".cw-item", box).forEach((it) => {
    const i = +it.dataset.i;
    it.onclick = (e) => {
      const a = e.target.closest("[data-a]");
      if (a) { e.stopPropagation(); if (a.disabled) return; const act = a.dataset.a;
        if (act === "del") { if (CW.mock.slides.length === 1) return toast("A deck needs at least one slide"); cwOp({ op: "delete", index: i }, () => Math.min(i, CW.mock.slides.length - 2)); }
        else if (act === "dup") cwOp({ op: "duplicate", index: i }, () => i + 1);
        else cwOp({ op: "merge", index: i }, () => i);
        return; }
      CW.sel = i; cwDrawList(); cwDrawEdit();
    };
    it.ondragstart = (e) => { e.dataTransfer.setData("text/plain", String(i)); it.style.opacity = ".5"; };
    it.ondragend = () => { it.style.opacity = ""; };
    it.ondragover = (e) => { e.preventDefault(); it.classList.add("over"); };
    it.ondragleave = () => it.classList.remove("over");
    it.ondrop = (e) => { e.preventDefault(); it.classList.remove("over"); const from = +e.dataTransfer.getData("text/plain"); if (!isNaN(from) && from !== i) cwOp({ op: "move", from, to: i }, () => i); };
  });
}
async function cwOp(op, selAfter) {
  try {
    await cwFlush();
    CW.st = await api(`/api/wizard/${CW.id}/mockup/op`, { body: { ...op, mockup: CW.mock } });
    CW.mock = CW.st.mockup; CW.sel = selAfter ? selAfter() : CW.sel; cwDrawList(); cwDrawEdit();
  } catch (e) { toast(e.message, "bad"); }
}
function cwDrawEdit() {
  const box = $("#cwedit"); if (!box) return;
  const s = CW.mock.slides[CW.sel], n = CW.mock.slides.length, L = s.layout;
  const fig = ["figure", "image_left", "image_right"].includes(L), ch = s.chart || {};
  box.innerHTML = `<div class="card"><div class="row" style="margin-bottom:10px"><label class="small muted">Layout</label><select id="cwlay" style="width:auto">${cwLayoutOpts(L)}</select><span class="grow"></span><span class="small muted">Slide ${CW.sel + 1} of ${n}</span></div>
    <div class="wf big" id="cwwf" data-l="${L}">${cwWF(s, true)}</div>
    <div class="small muted" style="margin:6px 0 10px">Click any text to edit it. This is a plain wireframe: the design is chosen for the final deck.</div>
    ${fig ? `<div class="grid g2" style="gap:10px"><div><label class="f">What the picture should show</label><input type="text" id="cwfd" value="${esc((s.figure || {}).description || "")}"></div>
      <div><label class="f">Picture link <small>(optional, https)</small></label><input type="text" id="cwfu" value="${esc((s.figure || {}).url || "")}" placeholder="https://…/picture.png"></div></div>` : ""}
    ${L === "chart" ? `<div class="grid g2" style="gap:10px"><div><label class="f">Numbers <small>(one per line: label: value)</small></label><textarea id="cwcd" rows="5">${esc((ch.labels || []).map((l, i) => `${l}: ${(ch.values || [])[i]}`).join("\n"))}</textarea></div>
      <div><label class="f">What is measured</label><input type="text" id="cwcl" value="${esc(ch.label || "")}"><div class="small muted" style="margin-top:6px">Drawn as editable bars in the .pptx.</div></div></div>` : ""}
    ${L === "table" ? `<div class="row" style="margin-bottom:6px"><button class="btn small" data-t="row">+ Row</button><button class="btn small" data-t="col">+ Column</button><button class="btn small ghost" data-t="delrow">− Row</button><button class="btn small ghost" data-t="delcol">− Column</button></div>` : ""}
    <div class="grid g2" style="gap:10px"><div><label class="f">Source <small>(small line at the bottom of the slide)</small></label><input type="text" id="cwsrc" value="${esc(s.source || "")}" placeholder="e.g. Lee (2020) or [@lee2020]"></div>
      <div><label class="f">Speaker notes</label><textarea id="cwnotes" rows="2">${esc(s.notes || "")}</textarea></div></div>
    <div class="row" style="margin-top:12px"><input type="text" id="cwrw" class="grow" placeholder="Ask the AI to change this slide: shorter, add an example, make it a comparison…"><button class="btn" id="cwrwgo">Rewrite this slide</button></div></div>`;
  const wf = $("#cwwf"), sync = () => { cwReadWF(wf, s); const it = $(`.cw-item[data-i="${CW.sel}"] .wf`); if (it) it.innerHTML = cwWF(s, false); cwQueueSave(); };
  wf.addEventListener("input", sync);
  wf.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.dataset && e.target.dataset.one) { e.preventDefault(); e.target.blur(); } });
  wf.addEventListener("paste", (e) => { e.preventDefault(); document.execCommand("insertText", false, (e.clipboardData || window.clipboardData).getData("text/plain")); });
  $("#cwlay").onchange = (e) => cwOp({ op: "layout", index: CW.sel, layout: e.target.value });
  $("#cwsrc").oninput = (e) => { s.source = e.target.value; cwQueueSave(); };
  $("#cwnotes").oninput = (e) => { s.notes = e.target.value; cwQueueSave(); };
  if (fig) { const f = () => { s.figure = { ...(s.figure || {}), description: $("#cwfd").value, url: $("#cwfu").value.trim() }; $(".wf-img", wf).innerHTML = (s.figure.url ? "Picture linked" : "▭ Picture slot") + (s.figure.description ? `<br><span>${esc(s.figure.description)}</span>` : ""); const it = $(`.cw-item[data-i="${CW.sel}"] .wf`); if (it) it.innerHTML = cwWF(s, false); cwQueueSave(); }; $("#cwfd").oninput = f; $("#cwfu").oninput = f; }
  if (L === "chart") {
    const f = () => {
      const rows = cwLines($("#cwcd").value).map((l) => { const i = l.lastIndexOf(":"); return i < 0 ? [l, NaN] : [l.slice(0, i).trim(), parseFloat(l.slice(i + 1))]; }).filter(([, v]) => !isNaN(v));
      s.chart = { kind: "bar", label: $("#cwcl").value, labels: rows.map((r) => r[0]), values: rows.map((r) => r[1]) };
      const keep = $("#cwcd"); const pos = document.activeElement === keep; wf.innerHTML = cwWF(s, true); const it = $(`.cw-item[data-i="${CW.sel}"] .wf`); if (it) it.innerHTML = cwWF(s, false); cwQueueSave(); if (pos) keep.focus();
    };
    $("#cwcd").oninput = f; $("#cwcl").oninput = f;
  }
  $$("[data-t]", box).forEach((b) => b.onclick = () => {
    const t = s.table = s.table || { header: [], rows: [] }, cols = Math.max((t.header || []).length, ...(t.rows || []).map((r) => r.length), 1);
    ({ row: () => t.rows.push(Array(cols).fill("")), col: () => { t.header = (t.header || []).concat(Array(cols + 1 - (t.header || []).length).fill("")); t.rows.forEach((r) => r.push("")); }, delrow: () => t.rows.pop(), delcol: () => { t.header.pop(); t.rows.forEach((r) => r.pop()); } })[b.dataset.t]();
    cwSaveNow().then(() => { cwDrawList(); cwDrawEdit(); });
  });
  $("#cwrwgo").onclick = (e) => busy(e.currentTarget, async () => {
    await cwFlush();
    CW.st = await api(`/api/wizard/${CW.id}/rewrite`, { body: { index: CW.sel, instruction: $("#cwrw").value } });
    CW.mock = CW.st.mockup; cwDrawList(); cwDrawEdit(); toast("Slide rewritten", "ok");
  });
}

// ================================================================== 4 · Final review
async function cwPickFolder(current) {
  const tree = await api("/api/tugas/tree");
  return new Promise((resolve) => {
    let sel = current == null ? null : current, done = false;
    const finish = (v) => { if (done) return; done = true; closeModal(); resolve(v); };
    const row = (n, d) => `<div class="tg-row ${n.path === sel ? "on" : ""}" data-p="${esc(n.path)}" style="padding-left:${8 + d * 16}px;cursor:pointer;border-radius:6px"><span class="muted small">▸</span> ${esc(n.name)}</div>${(n.children || []).map((c) => row(c, d + 1)).join("")}`;
    const draw = () => {
      $("#cwtree", box).innerHTML = `<div class="tg-row ${sel === "" ? "on" : ""}" data-p="" style="padding-left:8px;cursor:pointer;border-radius:6px"><b>All Tugas</b> <span class="small muted">(top level)</span></div>${tree.children.map((n) => row(n, 1)).join("")}`;
      $$(".tg-row", box).forEach((r) => r.onclick = () => { sel = r.dataset.p; draw(); });
      $("#cwfok", box).disabled = sel == null;
    };
    const box = modal(`<h2>Choose a Tugas folder</h2><p class="small muted" style="margin-top:0">Root: <code>${esc(tree.root)}</code></p><div class="scroll" id="cwtree" style="max-height:44vh;border:1px solid var(--line);border-radius:8px;padding:6px"></div>
      <div class="modal-foot" style="justify-content:space-between"><button class="btn" id="cwfnew">New folder here…</button><div class="row"><button class="btn" id="cwfx">Cancel</button><button class="btn primary" id="cwfok">Use this folder</button></div></div>`);
    draw();
    $("#cwfx", box).onclick = () => finish(null); $("#cwfok", box).onclick = () => finish(sel);
    $("#cwfnew", box).onclick = async () => {
      if (sel == null) return toast("Click the folder to make it in first");
      const name = prompt("Name of the new folder"); if (!name || !name.trim()) return;
      try { const r = await api("/api/tugas/mkdir", { body: { path: sel, name } }); Object.assign(tree, await api("/api/tugas/tree")); sel = r.path; draw(); } catch (e) { toast(e.message, "bad"); }
    };
  });
}
async function cwReview() {
  const el = $("#cwbody");
  el.innerHTML = `<div class="card"><div class="small muted">Checking the mockup…</div></div>`;
  let rev; try { await cwFlush(); rev = await api(`/api/wizard/${CW.id}/review`); } catch (e) { el.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; cwNav({ back: () => cwGo("mockup") }); return; }
  const st = CW.st, out = st.output, m = CW.mock, d = rev.design;
  const KIND = { too_much_text: ["Too much text", "warn"], no_source: ["No source", "warn"], empty_placeholder: ["Empty placeholder", "bad"] };
  const flagged = new Set(rev.checks.map((c) => c.slide));
  const live = ((await gsInfo().catch(() => null)) || {}).accounts || []; const accts = live.filter((a) => !a.needs_reconnect);
  el.innerHTML = `<div class="split-wide" style="grid-template-columns:minmax(0,420px) minmax(0,1fr)"><div>
    <div class="card"><h3>The deck</h3><label class="f">Title</label><input type="text" id="cwdt" value="${esc(rev.title)}">
      <label class="f">Design</label><div class="row" style="gap:10px;align-items:flex-start">${d ? `<img src="/api/designs/${encodeURIComponent(d.id)}/thumb" alt="" style="width:150px;border-radius:6px;border:1px solid var(--line);aspect-ratio:16/9;object-fit:cover">
        <div><div><b>${esc(d.name)}</b></div><div class="sw row" style="gap:3px;margin:5px 0">${gsSwatch(d.theme)}</div><div class="small muted">${esc(d.theme.heading_font || "")}${d.theme.body_font && d.theme.body_font !== d.theme.heading_font ? " / " + esc(d.theme.body_font) : ""}</div>
        <button class="btn small ghost" id="cwchd" style="margin-top:4px">Change design</button></div>` : '<span class="muted small">Default design</span>'}</div></div>
    <div class="card"><h3>Checks</h3>${rev.checks.length ? `<div class="small muted" style="margin-bottom:6px">${rev.checks.length} thing${rev.checks.length === 1 ? "" : "s"} to look at. None of them stops you.</div>` +
      rev.checks.map((c) => `<div class="row small" style="align-items:flex-start;gap:8px;margin:4px 0"><span class="pill ${KIND[c.kind][1]}">${KIND[c.kind][0]}</span><a href="#" data-gs="${c.slide}">Slide ${c.slide}</a><span class="grow">${esc(c.issue)}</span></div>`).join("") : `<div class="notice ok small" style="margin:0">No problems found: the text is short, every slide has a source and nothing is empty.</div>`}</div>
    <div class="card"><h3>Where should it go?</h3>
      <label class="row" style="gap:8px"><input type="checkbox" id="cwo_pptx" ${out.pptx ? "checked" : ""}> <span>PowerPoint (.pptx) in a Tugas folder</span></label>
      <div class="row" style="margin:4px 0 8px 24px"><code id="cwfolder">${out.folder == null ? "No folder chosen yet" : esc(out.folder || "All Tugas (top level)")}</code><button class="btn small" id="cwchf">Choose folder…</button></div>
      <label class="row" style="gap:8px"><input type="checkbox" id="cwo_g" ${out.google && accts.length ? "checked" : ""} ${accts.length ? "" : "disabled"}> <span>Google Slides in “A.F.R.A Decks”</span></label>
      ${accts.length ? (accts.length > 1 ? `<select id="cwacct" style="margin:4px 0 0 24px;width:auto">${accts.map((a) => `<option ${a.email === out.account || (!out.account && a.default) ? "selected" : ""}>${esc(a.email)}</option>`).join("")}</select>` : `<div class="small muted" style="margin-left:24px">${esc(accts[0].email)}</div>`)
        : `<div class="small" style="margin-left:24px">${esc(CW_NO_GOOGLE)} <a href="#settings">Open Settings</a></div>`}</div></div>
    <div class="card"><h3>Wireframes</h3><div class="cw-grid">${m.slides.map((s, i) => `<div class="cw-card ${flagged.has(i + 1) ? "flag" : ""}" data-gs="${i + 1}"><div class="small muted">${i + 1} · ${CW_LAYOUT[s.layout]}${flagged.has(i + 1) ? ' <span class="pill warn">check</span>' : ""}</div>${cwThumb(s, i)}</div>`).join("")}</div></div></div>`;
  $$("[data-gs]", el).forEach((a) => a.onclick = (e) => { e.preventDefault(); CW.sel = +a.dataset.gs - 1; cwGo("mockup"); });
  $("#cwdt").oninput = (e) => { CW.mock.title = e.target.value; cwQueueSave(); };
  const chd = $("#cwchd"); if (chd) chd.onclick = () => cwGo("design");
  const saveOut = () => cwPut({ output: { pptx: $("#cwo_pptx").checked, google: $("#cwo_g").checked, account: ($("#cwacct") || {}).value || "" } }).catch((e) => toast(e.message, "bad"));
  $("#cwo_pptx").onchange = saveOut; $("#cwo_g").onchange = saveOut; const ac = $("#cwacct"); if (ac) ac.onchange = saveOut;
  $("#cwchf").onclick = async () => { const f = await cwPickFolder(CW.st.output.folder); if (f != null) { await cwPut({ output: { folder: f, pptx: true } }); cwReview(); } };
  cwNav({ back: () => cwGo("mockup"), label: "Create the deck", next: async () => {
    await cwSaveNow(); await saveOut();
    const o = CW.st.output;
    if (!o.pptx && !o.google) return toast("Choose where the deck goes", "bad");
    if (o.pptx && o.folder == null) return toast("Choose a Tugas folder for the .pptx", "bad");
    CW.st.result = null; CW.job = (await api(`/api/wizard/${CW.id}/create`, { body: {} })).job;
    await cwGo("create");
  }, hint: rev.checks.length ? "" : "Ready" });
}

// ================================================================== 5 · Create
async function cwCreate() {
  const el = $("#cwbody"), st = CW.st;
  cwNav({ back: () => cwGo("review"), next: null, hide: true });
  if (CW.job) {
    el.innerHTML = `<div class="card"><div class="statusbar" id="cwjs"><span class="spin"></span> Starting…</div><div class="small muted">Building exactly what the mockup shows, in the design you picked.</div></div>`;
    const jid = CW.job, my = ++CW.poll;
    for (; ;) {
      let j; try { j = await api("/api/wizard/jobs/" + jid); } catch (e) { el.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; CW.job = ""; return; }
      if (my !== CW.poll || !el.isConnected) return;
      if (j.state === "error") { CW.job = ""; el.innerHTML = `<div class="notice bad"><b>It couldn't be built.</b> ${esc(j.error)}</div><div class="row"><button class="btn primary" id="cwbk">Back to review</button></div>`; $("#cwbk").onclick = () => cwGo("review"); return; }
      if (j.state === "done") { CW.job = ""; CW.st = await api("/api/wizard/" + CW.id); return cwCreate(); }
      $("#cwjs").innerHTML = `<span class="spin"></span> ${esc(j.step)}`;
      await sleep(900);
    }
  }
  const r = st.result;
  if (!r) { el.innerHTML = `<div class="card"><div class="empty">Nothing is built yet.<div class="row" style="justify-content:center;margin-top:10px"><button class="btn primary" id="cwbk">Go to the review</button></div></div></div>`; $("#cwbk").onclick = () => cwGo("review"); return; }
  const flags = r.flags || [];
  el.innerHTML = `<div class="card"><div class="notice ok" style="margin-bottom:12px"><b>Your deck is ready.</b> “${esc(r.title)}” · ${r.slides} slides${r.path ? ` · saved as <code>${esc(r.path)}</code>` : ""}</div>
    ${r.google_error ? `<div class="notice small">Google Slides was skipped. ${esc(r.google_error)} ${/Google account/.test(r.google_error) ? '<a href="#settings">Open Settings</a>' : ""}</div>` : ""}
    <div class="row" style="gap:8px">${r.path ? `<button class="btn primary" id="cwopen">Open in PowerPoint</button>` : ""}
      ${r.google ? `<a class="btn ${r.path ? "" : "primary"}" href="${esc(r.google.url)}" target="_blank">Open in Google Slides</a>` : r.path ? `<button class="btn" id="cwgs">Open in Google Slides</button>` : ""}
      ${r.path ? `<button class="btn" id="cwshow">Show in Tugas folder</button><button class="btn ghost" id="cwexp">Show in File Explorer</button>` : ""}</div>
    ${flags.length ? `<div class="small" style="margin-top:12px"><b>Worth a look in the deck</b><ul style="margin:4px 0;padding-left:18px">${flags.slice(0, 20).map((f) => `<li>${f.slide ? `Slide ${f.slide}: ` : ""}${esc(f.issue)}</li>`).join("")}</ul></div>` : ""}
    <div class="row" style="margin-top:14px"><a class="btn" href="#create">Make another deck</a><button class="btn ghost" id="cwbk">Back to the review</button></div></div>`;
  const o = $("#cwopen"); if (o) o.onclick = (e) => busy(e.currentTarget, () => api("/api/tugas/open", { body: { path: r.path } }));
  const gs = $("#cwgs"); if (gs) gs.onclick = (e) => busy(e.currentTarget, () => tgGoogle({ path: r.path }));
  const sh = $("#cwshow"); if (sh) sh.onclick = () => { location.hash = tgUrl(r.folder || ""); };
  const ex = $("#cwexp"); if (ex) ex.onclick = (e) => busy(e.currentTarget, () => api("/api/tugas/reveal", { body: { path: r.path } }));
  $("#cwbk").onclick = () => cwGo("review");
}
