// ================================================================== CHEATSHEET
// Many lectures, documents and materials in, one dense printable sheet out: Sources → Options → Review → Make.
// The draft lives on the server (data/cheatsheet/), so closing the page and coming back resumes it.
// Loaded after app.js, sources.js and create.js; uses their helpers (api, srcPicker, cwPickFolder, cwDownload...).
const CS = { id: "", st: null, poll: 0, preset: null, picker: null, saveT: 0, drag: null };
const CS_STEPS = [["sources", "Sources"], ["options", "Options"], ["review", "Review"], ["make", "Make"]];
const CS_KIND = { definition: "Definition", formula: "Formula", example: "Example", date: "Date / name", step: "Steps", comparison: "Comparison", concept: "Key idea" };
const CS_INC = [["definitions", "Definitions"], ["formulas", "Formulas"], ["examples", "Examples"], ["dates", "Dates & names"], ["steps", "Steps / procedures"], ["comparisons", "Comparisons"]];
const CS_NO_GOOGLE = "Connect a Google account first: Settings › Google.";

// ---- entry points: other pages call this (Tugas › Make cheatsheet, Create into PPT sources)
function startCheatsheet(sources, folder) {
  return api("/api/cheatsheet", { body: { project_id: S.project.id, sources, folder: folder == null ? null : folder } })
    .then((st) => { location.hash = "#cheatsheet/" + st.id; return st; }).catch((e) => toast(e.message, "bad"));
}

async function csBegin(refs, files, folder) {  // a new draft from picked sources (+ files still to upload), then open it
  const st = await api("/api/cheatsheet", { body: { project_id: S.project.id, sources: refs, folder: folder == null ? null : folder } });
  await srcUpload("cheatsheet", st.id, files);
  location.hash = "#cheatsheet/" + st.id;
}

VIEWS.cheatsheet = async () => {
  CS.poll++;
  const id = location.hash.split("/")[1];
  return id ? csOpen(id) : csStart();
};

// ---- the start page: continue a draft, or pick sources
async function csStart() {
  const drafts = await api("/api/cheatsheet").catch(() => []);
  const pre = CS.preset; CS.preset = null;
  main().innerHTML = `<div class="page-head"><div><h1>Cheatsheet</h1><p>Pick lectures, documents and materials; the AI proposes what belongs on a dense, printable sheet. You tick, edit and move items before the file is made.</p></div></div>
    ${needLLM()}
    ${drafts.length ? `<div class="card"><h3>Continue where you left off</h3>${drafts.slice(0, 8).map((d) => `<div class="list-item" style="cursor:default"><div class="row" style="justify-content:space-between">
      <div><div class="t">${esc(d.title)}</div><div class="small muted">${d.done ? "Made" : "Step: " + esc(d.step)} · ${d.items ? d.items + " items · " : ""}${ago(d.updated)}</div></div>
      <div class="row" style="gap:4px"><a class="btn small primary" href="#cheatsheet/${d.id}">${d.done ? "Open" : "Resume"}</a><button class="btn small ghost" data-cdel="${d.id}">Delete</button></div></div></div>`).join("")}</div>` : ""}
    ${pre && pre.folder ? `<div class="notice info small">The sheet will be saved in <b>${esc(pre.folder || "All Tugas")}</b>.</div>` : ""}
    <div class="card"><h3>What should the sheet be made from?</h3><div id="csp"></div>
      <div class="row" style="margin-top:10px"><button class="btn primary" id="csgo">Start the cheatsheet</button></div></div>`;
  const pk = await srcPicker($("#csp"), { refs: pre ? pre.sources : [] });
  $("#csgo").onclick = (e) => busy(e.currentTarget, async () => {
    if (!pk.refs().length && !pk.files().length) return toast("Pick at least one source, or upload a file");
    await csBegin(pk.refs(), pk.files(), pre ? pre.folder : null);
  });
  $$("[data-cdel]").forEach((b) => b.onclick = async () => { if (!confirm("Delete this draft? Files already made stay where they are.")) return; await api("/api/cheatsheet/" + b.dataset.cdel, { method: "DELETE" }); csStart(); });
}

// ---- the frame
async function csOpen(id) {
  try { CS.st = await api("/api/cheatsheet/" + id); } catch (e) { toast(e.message, "bad"); location.hash = "#cheatsheet"; return; }
  CS.id = id; csFrame();
}
const csCan = (k) => k === "sources" || k === "options" && csHasSources() || k === "review" && CS.st.sections.length > 0 || k === "make" && CS.st.sections.length > 0;
const csHasSources = () => CS.st.sources.length + CS.st.materials.length > 0;
async function csPut(body) { CS.st = await api("/api/cheatsheet/" + CS.id, { method: "PUT", body }); return CS.st; }
async function csGo(step) { await csFlush(); if (CS.st.step !== step) await csPut({ step }); csFrame(); }
function csFrame() {
  const st = CS.st, at = CS_STEPS.findIndex(([k]) => k === st.step);
  main().innerHTML = `<div class="page-head"><div><h1>Cheatsheet</h1><p>${esc(st.title || "Untitled cheatsheet")}</p></div><div class="row"><a class="btn" href="#cheatsheet">All drafts</a><button class="btn ghost" id="csdel">Delete draft</button></div></div>
    <div class="cw-steps">${CS_STEPS.map(([k, l], n) => `<button class="cw-step ${n === at ? "on" : n < at ? "done" : ""}" data-s="${k}" ${csCan(k) || n === at ? "" : "disabled"}><b>${n < at ? "✓" : n + 1}</b> ${l}</button>`).join("")}</div>
    <div id="csbody"></div>
    <div class="cw-nav"><button class="btn" id="csback">Back</button><span class="grow small muted" id="cshint"></span><button class="btn primary" id="csnext">Next</button></div>`;
  $$(".cw-step").forEach((b) => b.onclick = () => csGo(b.dataset.s));
  $("#csdel").onclick = async () => { if (!confirm("Delete this draft? Files already made stay where they are.")) return; await api("/api/cheatsheet/" + CS.id, { method: "DELETE" }); location.hash = "#cheatsheet"; };
  ({ sources: csSources, options: csOptions, review: csReview, make: csMake })[st.step]();
}
function csNav({ back, next, label = "Next", hint = "", hide = false }) {
  const b = $("#csback"), n = $("#csnext");
  b.style.visibility = back ? "visible" : "hidden"; b.onclick = () => back && back();
  n.style.display = hide ? "none" : ""; n.textContent = label; n.disabled = !next; n.onclick = (e) => next && busy(e.currentTarget, next);
  $("#cshint").textContent = hint;
}

// ================================================================== 1 · Sources
async function csSources() {
  const el = $("#csbody"), st = CS.st;
  el.innerHTML = `<div class="card"><h3>Sources</h3><div id="csp"></div>
    <label class="f">Sheet title <small>(optional: the AI suggests one)</small></label><input type="text" id="cstitle" value="${esc(st.title)}"></div>`;
  const nav = () => csNav({ next: csHasSources() || CS.picker.refs().length || CS.picker.files().length ? csSourcesNext : null, label: "Next: options", hint: "Pick at least one source." });
  CS.picker = await srcPicker($("#csp"), { refs: st.sources, extra: st.materials.map((m) => ({ id: m.id, name: m.name, remove: async () => { await api(`/api/cheatsheet/${CS.id}/materials/${m.id}`, { method: "DELETE" }); CS.st = await api("/api/cheatsheet/" + CS.id); csSources(); } })), onChange: nav });
  $("#cstitle").oninput = debounce(() => csPut({ title: $("#cstitle").value }).catch(() => { }), 500);
  nav();
}
async function csSourcesNext() {
  const files = CS.picker.files();
  await csPut({ sources: CS.picker.refs(), title: $("#cstitle").value });
  if (files.length) { await srcUpload("cheatsheet", CS.id, files); CS.picker.clearFiles(); CS.st = await api("/api/cheatsheet/" + CS.id); }
  if (!csHasSources()) return toast("Pick at least one source", "bad");
  await csGo("options");
}

// ================================================================== 2 · Options
function csOptions() {
  const el = $("#csbody"), st = CS.st, o = st.options;
  const rad = (name, val, label, hint) => `<label class="row small" style="gap:8px;margin:3px 0"><input type="radio" name="${name}" value="${val}" ${String(o[name === "cslen" ? "length" : "columns"]) === String(val) ? "checked" : ""}> <span><b>${label}</b> ${hint ? `<span class="muted">${hint}</span>` : ""}</span></label>`;
  el.innerHTML = `<div class="card"><h3>The sheet</h3><div class="grid g3" style="gap:14px">
      <div><label class="f">Length</label>${rad("cslen", "1", "1 page", "one side of A4")}${rad("cslen", "2", "2 pages", "")}${rad("cslen", "both", "Both sides of A4", "front and back of one sheet")}</div>
      <div><label class="f">Columns</label>${rad("cscol", 2, "2 columns", "easier to read")}${rad("cscol", 3, "3 columns", "denser")}
        <label class="f">Font size <small>(pt)</small></label><input type="number" id="csfont" min="5" max="12" step="0.5" value="${o.font}" style="width:90px"></div>
      <div><label class="f">Include</label>${CS_INC.map(([k, l]) => `<label class="row small" style="gap:6px;margin:2px 0"><input type="checkbox" data-inc="${k}" ${o.include[k] ? "checked" : ""}> ${l}</label>`).join("")}
        <label class="f">Language</label><select id="cslang"><option value="">Same as the sources</option>${["English", "Indonesian"].map((l) => `<option ${o.language === l ? "selected" : ""}>${l}</option>`).join("")}</select></div></div></div>
    <div class="card"><div class="row" style="justify-content:space-between"><h3 style="margin:0">Ask me first <small class="muted">optional</small></h3><button class="btn" id="csask">${st.questions && st.questions.length ? "Ask again" : "Ask me a few questions"}</button></div>
      <div class="small muted" style="margin-top:4px">The AI looks at the sources and asks what the sheet is for (which exam, which chapters), so the first proposal is closer.</div><div id="csqs" style="margin-top:8px"></div></div>
    ${st.sections.length ? `<div class="notice info small">This draft already has a proposal with your edits. <b>Propose again</b> replaces it; <a href="#" id="cskeep">go back to the review</a> to keep it.</div>` : ""}`;
  const save = debounce(() => csPut({ options: { length: ($('input[name="cslen"]:checked') || {}).value || "1", columns: +(($('input[name="cscol"]:checked') || {}).value || 3), font: +$("#csfont").value || 7,
    language: $("#cslang").value, include: Object.fromEntries($$("[data-inc]").map((x) => [x.dataset.inc, x.checked])) } }).catch((e) => toast(e.message, "bad")), 300);
  $$("input,select", el).forEach((x) => { x.onchange = save; if (x.type === "number") x.oninput = save; });
  const keep = $("#cskeep"); if (keep) keep.onclick = (e) => { e.preventDefault(); csGo("review"); };
  const qs = () => {
    const q = CS.st.questions || [];
    $("#csqs").innerHTML = q.map((x) => `<div style="margin:8px 0"><b class="small">${esc(x.q)}</b><div class="row" style="gap:6px;margin:4px 0;flex-wrap:wrap">${x.options.map((op) => `<button class="btn small ${((CS.st.answers || {})[x.id] === op) ? "primary" : ""}" data-q="${x.id}" data-a="${esc(op)}">${esc(op)}</button>`).join("")}</div>
      <input type="text" data-qt="${x.id}" placeholder="…or type your own" value="${esc(x.options.includes((CS.st.answers || {})[x.id]) ? "" : (CS.st.answers || {})[x.id] || "")}"></div>`).join("");
    const put = debounce(async (id, v) => { CS.st = await api(`/api/cheatsheet/${CS.id}/answers`, { body: { answers: { ...(CS.st.answers || {}), [id]: v } } }); qs(); }, 400);
    $$("[data-q]").forEach((b) => b.onclick = () => put(b.dataset.q, b.dataset.a));
    $$("[data-qt]").forEach((i) => i.oninput = () => put(i.dataset.qt, i.value));
  };
  qs();
  $("#csask").onclick = (e) => busy(e.currentTarget, async () => { await save.flush?.(); CS.st = await api(`/api/cheatsheet/${CS.id}/questions`, { body: {} }); csOptions(); });
  csNav({ back: () => csGo("sources"), label: "Propose the sections", hint: claudeMode() ? "The AI step runs in the Claude app: press Enter there when it opens." : "The AI reads the sources and proposes sections and items.", next: async () => {
    await csPutOptionsNow();
    CS.st = await api(`/api/cheatsheet/${CS.id}/propose`, { body: {} });
    if ((CS.st.notes || []).length) toast(CS.st.notes.join(" "), "ok");
    csFrame();
  } });
}
async function csPutOptionsNow() {
  await csPut({ options: { length: ($('input[name="cslen"]:checked') || {}).value || "1", columns: +(($('input[name="cscol"]:checked') || {}).value || 3), font: +$("#csfont").value || 7,
    language: $("#cslang").value, include: Object.fromEntries($$("[data-inc]").map((x) => [x.dataset.inc, x.checked])) } });
}

// ================================================================== 3 · Review
async function csFlush() { if (CS.saveT) { clearTimeout(CS.saveT); CS.saveT = 0; await csSaveNow(); } }
async function csSaveNow() {
  try { CS.st = await api(`/api/cheatsheet/${CS.id}/sections`, { method: "PUT", body: { sections: CS.st.sections, title: CS.st.title } }); csFit(); const h = $("#cshint"); if (h) h.textContent = "Saved"; }
  catch (e) { toast(e.message, "bad"); }
}
function csQueue() { clearTimeout(CS.saveT); $("#cshint") && ($("#cshint").textContent = "Saving…"); CS.saveT = setTimeout(() => { CS.saveT = 0; csSaveNow(); }, 700); }
async function csOp(op, redraw = true) {
  try { await csFlush(); CS.st = await api(`/api/cheatsheet/${CS.id}/op`, { body: { ...op, sections: CS.st.sections } }); if (redraw) csDraw(); else csFit(); }
  catch (e) { toast(e.message, "bad"); }
}
function csFit() {
  const box = $("#csfit"); if (!box) return;
  const e = CS.st.estimate, pct = Math.min(100, Math.round(e.ratio * 100));
  box.innerHTML = `<div class="row" style="justify-content:space-between"><b class="small">${e.items} item${e.items === 1 ? "" : "s"} ticked · ${Math.round(e.ratio * 100)}% of the ${CS.st.options.length === "1" ? "page" : "2 pages"}</b><span class="small ${e.fits ? "muted" : ""}" style="${e.fits ? "" : "color:var(--bad)"}">${esc(e.tip)}</span></div>
    <div class="cs-fit ${!e.fits ? "over" : e.ratio > .9 ? "warn" : ""}" style="margin-top:6px"><b style="width:${pct}%"></b></div><div class="small muted" style="margin-top:2px">An estimate: Word may break lines a little differently.</div>`;
}
function csReview() {
  const el = $("#csbody");
  el.innerHTML = `<div class="card" style="position:sticky;top:0;z-index:5"><div id="csfit"></div></div><div class="row" style="margin-bottom:8px"><input type="text" id="cstitle" class="grow" placeholder="Sheet title" value="${esc(CS.st.title)}">
    <button class="btn" id="csaddsec">+ Section</button><button class="btn ghost" id="csre">Propose again</button></div><div id="cssecs"></div>`;
  $("#cstitle").oninput = (e) => { CS.st.title = e.target.value; csQueue(); };
  $("#csaddsec").onclick = () => { const t = prompt("Name of the new section"); if (t && t.trim()) csOp({ op: "add_section", title: t }); };
  $("#csre").onclick = () => { if (confirm("Propose the sections again? Your edits to this proposal are replaced.")) csGo("options"); };
  csDraw();
  csNav({ back: () => csGo("options"), label: "Next: make the file", next: async () => { await csFlush(); if (!CS.st.estimate.items) return toast("Tick at least one item", "bad"); await csGo("make"); },
    hint: "Click any text to edit it. Drag items to move them." });
}
function csDraw() {
  const box = $("#cssecs"); if (!box) return;
  const secs = CS.st.sections;
  box.innerHTML = secs.map((s, si) => `<div class="cs-sec" data-s="${s.id}"><div class="row" style="gap:6px">
      <input type="checkbox" data-all="${s.id}" title="Tick or untick the whole section" ${s.items.length && s.items.every((i) => i.on) ? "checked" : ""}>
      <input type="text" class="grow" data-st="${s.id}" value="${esc(s.title)}" style="font-weight:600">
      <button class="mini" data-su="${s.id}" ${si ? "" : "disabled"} title="Move up">↑</button><button class="mini" data-sd="${s.id}" ${si < secs.length - 1 ? "" : "disabled"} title="Move down">↓</button><button class="mini" data-sx="${s.id}" title="Delete the section">×</button></div>
    ${s.items.map((it) => `<div class="cs-it ${it.on ? "" : "off"}" draggable="true" data-i="${it.id}"><input type="checkbox" data-on="${it.id}" ${it.on ? "checked" : ""}>
      <div class="cs-t" contenteditable="true" spellcheck="true" data-t="${it.id}">${esc(it.text)}</div>
      ${it.source ? `<span class="cs-src" title="${esc(it.source)}">${esc(it.source)}</span>` : ""}
      <select class="cs-kind" data-k="${it.id}" title="What kind of item">${Object.entries(CS_KIND).map(([k, v]) => `<option value="${k}" ${k === it.kind ? "selected" : ""}>${v}</option>`).join("")}</select>
      <select class="cs-kind" data-mv="${it.id}" title="Move to another section"><option value="">Move…</option>${secs.filter((x) => x.id !== s.id).map((x) => `<option value="${x.id}">${esc(x.title.slice(0, 30))}</option>`).join("")}</select>
      <div class="cs-acts"><button data-x="${it.id}" title="Delete">×</button></div></div>`).join("")}
    <div class="row" style="margin-top:6px;gap:6px"><input type="text" class="grow" data-new="${s.id}" placeholder="Add your own item (Enter)"><button class="btn small ghost" data-more="${s.id}">More from the AI</button></div>
    <div data-sg="${s.id}"></div></div>`).join("") || `<div class="empty">No sections.</div>`;
  csFit();
  $$("[data-all]", box).forEach((x) => x.onchange = () => csOp({ op: "set_section", section: x.dataset.all, on: x.checked }));
  $$("[data-st]", box).forEach((x) => x.oninput = () => { CS.st.sections.find((s) => s.id === x.dataset.st).title = x.value; csQueue(); });
  $$("[data-su]", box).forEach((b) => b.onclick = () => csOp({ op: "move_section", section: b.dataset.su, to: secs.findIndex((s) => s.id === b.dataset.su) - 1 }));
  $$("[data-sd]", box).forEach((b) => b.onclick = () => csOp({ op: "move_section", section: b.dataset.sd, to: secs.findIndex((s) => s.id === b.dataset.sd) + 1 }));
  $$("[data-sx]", box).forEach((b) => b.onclick = () => { const s = secs.find((x) => x.id === b.dataset.sx); if (!s.items.length || confirm(`Delete “${s.title}” and its ${s.items.length} items?`)) csOp({ op: "delete_section", section: s.id }); });
  $$("[data-on]", box).forEach((x) => x.onchange = () => csOp({ op: "toggle", item: x.dataset.on, on: x.checked }));
  $$("[data-x]", box).forEach((b) => b.onclick = () => csOp({ op: "delete_item", item: b.dataset.x }));
  $$("[data-k]", box).forEach((x) => x.onchange = () => { const it = csItem(x.dataset.k); it.kind = x.value; csQueue(); });
  $$("[data-mv]", box).forEach((x) => x.onchange = () => x.value && csOp({ op: "move_item", item: x.dataset.mv, to: x.value }));
  $$("[data-t]", box).forEach((x) => {
    x.oninput = () => { const it = csItem(x.dataset.t); it.text = x.innerText.replace(/\s*\n+\s*/g, " ").trim(); csQueue(); };
    x.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); x.blur(); } };
    x.onpaste = (e) => { e.preventDefault(); document.execCommand("insertText", false, (e.clipboardData || window.clipboardData).getData("text/plain")); };
  });
  $$("[data-new]", box).forEach((x) => x.onkeydown = (e) => { if (e.key === "Enter" && x.value.trim()) csOp({ op: "add_item", section: x.dataset.new, text: x.value }); });
  $$("[data-more]", box).forEach((b) => b.onclick = () => busy(b, () => csMore(b.dataset.more)));
  // drag an item onto another item (before it) or onto a section (at its end)
  $$(".cs-it", box).forEach((it) => {
    it.ondragstart = (e) => { CS.drag = it.dataset.i; e.dataTransfer.setData("text/plain", CS.drag); it.style.opacity = ".5"; };
    it.ondragend = () => { it.style.opacity = ""; CS.drag = null; };
    it.ondragover = (e) => { if (CS.drag) { e.preventDefault(); e.stopPropagation(); it.classList.add("over"); } };
    it.ondragleave = () => it.classList.remove("over");
    it.ondrop = (e) => { e.preventDefault(); e.stopPropagation(); it.classList.remove("over"); csDrop(it.closest(".cs-sec").dataset.s, it.dataset.i); };
  });
  $$(".cs-sec", box).forEach((sec) => {
    sec.ondragover = (e) => { if (CS.drag) { e.preventDefault(); sec.classList.add("over"); } };
    sec.ondragleave = (e) => { if (!sec.contains(e.relatedTarget)) sec.classList.remove("over"); };
    sec.ondrop = (e) => { e.preventDefault(); sec.classList.remove("over"); csDrop(sec.dataset.s, null); };
  });
}
const csItem = (iid) => CS.st.sections.flatMap((s) => s.items).find((i) => i.id === iid);
function csDrop(secId, beforeId) {
  const from = CS.drag; if (!from || from === beforeId) return;
  const sec = CS.st.sections.find((s) => s.id === secId);
  let idx = beforeId ? sec.items.findIndex((i) => i.id === beforeId) : sec.items.length;
  const own = sec.items.findIndex((i) => i.id === from); if (own >= 0 && own < idx) idx--;
  csOp({ op: "move_item", item: from, to: secId, index: idx });
}
async function csMore(secId) {
  await csFlush();
  const ask = prompt("Anything specific? (leave empty for a few more items that belong here)", "");
  if (ask === null) return;
  const r = await api(`/api/cheatsheet/${CS.id}/suggest`, { body: { section: secId, instruction: ask } });
  const host = $(`[data-sg="${secId}"]`);
  if (!r.items.length) { host.innerHTML = `<div class="small muted" style="margin-top:6px">Nothing new to suggest.</div>`; return; }
  host.innerHTML = `<div class="small muted" style="margin:6px 0 2px">Suggested: add the ones you want.</div>` + r.items.map((it, n) => `<div class="cs-it"><div class="cs-t" style="cursor:default">${esc(it.text)}</div><span class="cs-src">${esc(it.source)}</span><button class="btn small" data-add="${n}">Add</button></div>`).join("");
  $$("[data-add]", host).forEach((b) => b.onclick = () => { const it = r.items[+b.dataset.add]; b.disabled = true; csOp({ op: "add_item", section: secId, text: it.text, kind: it.kind }); });
}

// ================================================================== 4 · Make
async function csMake() {
  const el = $("#csbody"), st = CS.st, out = st.output;
  const live = ((await gsInfo().catch(() => null)) || {}).accounts || []; const accts = live.filter((a) => !a.needs_reconnect);
  if (st.result) return csDone();
  el.innerHTML = `<div class="split-wide" style="grid-template-columns:minmax(0,520px) minmax(0,1fr)"><div>
    <div class="card"><h3>Make the file</h3>
      <label class="row" style="gap:8px"><input type="checkbox" id="cso_docx" ${out.docx ? "checked" : ""}> <span>Word (.docx) <span class="muted small">real ${st.options.columns} columns, small margins</span></span></label>
      <label class="row" style="gap:8px;margin-top:4px"><input type="checkbox" id="cso_pdf" ${out.pdf ? "checked" : ""}> <span>PDF</span></label>
      <div class="notice small ${st.non_latin ? "" : "hidden"}" id="csnl" style="margin:6px 0 0 24px">The PDF can only print Latin letters. Other symbols in this sheet (like Σ, π or ≤) would show as “?”. Use Word for those.</div>
      <div class="small muted" style="margin-left:24px">The PDF is made by A.F.R.A's own small writer: plain text in two or three columns, Latin letters only.</div>
      <label class="row" style="gap:8px;margin-top:4px"><input type="checkbox" id="cso_g" ${out.google && accts.length ? "checked" : ""} ${accts.length ? "" : "disabled"}> <span>Google Doc in “A.F.R.A Decks”</span></label>
      ${accts.length ? (accts.length > 1 ? `<select id="csacct" style="margin:4px 0 0 24px;width:auto">${accts.map((a) => `<option ${a.email === out.account || (!out.account && a.default) ? "selected" : ""}>${esc(a.email)}</option>`).join("")}</select>` : `<div class="small muted" style="margin-left:24px">${esc(accts[0].email)}</div>`)
        : `<div class="small" style="margin-left:24px">${esc(CS_NO_GOOGLE)} <a href="#settings">Open Settings</a></div>`}
      <label class="f" style="margin-top:12px">Save in the Tugas folder</label><div class="row"><code id="csfolder">${out.folder == null ? "No folder chosen yet" : esc(out.folder || "All Tugas (top level)")}</code><button class="btn small" id="cschf">Choose folder…</button></div>
      <div class="small muted" style="margin-top:4px">If a file with the same name is there, the new one becomes “name (2)”.</div></div></div>
    <div><div class="card"><h3>${esc(st.title || "Cheatsheet")}</h3><div id="csfit"></div>
      <div class="small muted" style="margin-top:8px">${st.options.length === "1" ? "1 page" : st.options.length === "2" ? "2 pages" : "Both sides of A4"} · ${st.options.columns} columns · ${st.options.font} pt · ${st.estimate.items} items in ${st.sections.filter((s) => s.items.some((i) => i.on)).length} sections</div>
      <div class="row" style="margin-top:10px"><button class="btn small" data-dl="docx">Download Word</button><button class="btn small" data-dl="pdf">Download PDF</button></div></div></div></div>`;
  csFit();
  const saveOut = () => csPut({ output: { docx: $("#cso_docx").checked, pdf: $("#cso_pdf").checked, google: $("#cso_g").checked, account: ($("#csacct") || {}).value || "" } }).then(() => { $("#csnl").classList.toggle("hidden", !(CS.st.non_latin && $("#cso_pdf").checked)); }).catch((e) => toast(e.message, "bad"));
  ["#cso_docx", "#cso_pdf", "#cso_g"].forEach((s) => $(s).onchange = saveOut); const ac = $("#csacct"); if (ac) ac.onchange = saveOut;
  $("#csnl").classList.toggle("hidden", !(st.non_latin && out.pdf));
  $("#cschf").onclick = async () => { const f = await cwPickFolder(CS.st.output.folder); if (f != null) { await csPut({ output: { folder: f } }); csMake(); } };
  $$("[data-dl]").forEach((b) => b.onclick = () => busy(b, () => cwDownload(`/api/cheatsheet/${CS.id}/download?fmt=${b.dataset.dl}`)));
  csNav({ back: () => csGo("review"), label: "Make the cheatsheet", next: async () => {
    await saveOut(); const o = CS.st.output;
    if (!o.docx && !o.pdf && !o.google) return toast("Choose what to make", "bad");
    if ((o.docx || o.pdf) && o.folder == null) return toast("Choose a Tugas folder to save in", "bad");
    CS.st.result = await api(`/api/cheatsheet/${CS.id}/make`, { body: {} }); CS.st = await api("/api/cheatsheet/" + CS.id); csDone();
  } });
}
function csDone() {
  const el = $("#csbody"), r = CS.st.result;
  csNav({ back: () => { CS.st.result = null; csPut({ step: "review" }).then(csFrame); }, next: null, hide: true });
  el.innerHTML = `<div class="card"><div class="notice ok" style="margin-bottom:12px"><b>Your cheatsheet is ready.</b> ${r.files.length ? `Saved as ${r.files.map((f) => `<code>${esc(f)}</code>`).join(", ")}.` : ""}</div>
    ${r.google_error ? `<div class="notice small">The Google Doc was skipped. ${esc(r.google_error)} ${/Google account/.test(r.google_error) ? '<a href="#settings">Open Settings</a>' : ""}</div>` : ""}
    ${r.non_latin ? `<div class="notice small">Some symbols can't be printed in the PDF and show as “?”. The Word file has them all.</div>` : ""}
    ${r.estimate && !r.estimate.fits ? `<div class="notice small">${esc(r.estimate.tip)} Open the file to check how it printed.</div>` : ""}
    <div class="row" style="gap:8px">${r.files.map((f) => `<button class="btn primary" data-open="${esc(f)}">Open ${esc(f.split("/").pop())}</button>`).join("")}
      ${r.google ? `<a class="btn" href="${esc(r.google.url)}" target="_blank">Open the Google Doc</a>` : ""}
      ${r.files.length ? `<button class="btn" id="csshow">Show in Tugas folder</button>` : ""}</div>
    <div class="row" style="margin-top:14px"><a class="btn" href="#cheatsheet">Make another</a><button class="btn ghost" id="csagain">Back to the review</button></div></div>`;
  $$("[data-open]").forEach((b) => b.onclick = () => busy(b, () => api("/api/tugas/open", { body: { path: b.dataset.open } })));
  const sh = $("#csshow"); if (sh) sh.onclick = () => { location.hash = tgUrl(r.folder || ""); };
  $("#csagain").onclick = () => { CS.st.result = null; csPut({ step: "review" }).then(csFrame); };
}
