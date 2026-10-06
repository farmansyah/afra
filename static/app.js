/* A.F.R.A: Article Finder & Research Assistant. Single-page frontend (no build step).
   (c) 2026 danafarmansyah. All rights reserved. Crafted with love. */
const S = { projects: [], project: null, meta: null, settings: null, view: "home", pending: 0 };
// Local safety net: every edit is mirrored to localStorage until the server confirms the save.
const Draft = {
  put(k, v) { try { localStorage.setItem("rt_draft_" + k, JSON.stringify({ t: Date.now(), v })); } catch { } },
  get(k) { try { return JSON.parse(localStorage.getItem("rt_draft_" + k) || "null"); } catch { return null; } },
  drop(k) { try { localStorage.removeItem("rt_draft_" + k); } catch { } },
};
// debounced server save that retries and keeps the local draft until it succeeds
function autosaver(key, doSave, statusEl, ms = 800) {
  let timer = null, dirty = false;
  const run = async () => {
    timer = null;
    try { await doSave(); dirty = false; S.pending = Math.max(0, S.pending - 1); Draft.drop(key); const el = statusEl(); if (el) el.textContent = "Saved"; }
    catch (e) { const el = statusEl(); if (el) el.innerHTML = `<span style="color:var(--bad)">Not saved (kept locally) — retrying…</span>`; timer = setTimeout(run, 5000); }
  };
  return (snapshot) => {
    Draft.put(key, snapshot);
    if (!dirty) { dirty = true; S.pending++; }
    const el = statusEl(); if (el) el.textContent = "Saving…";
    clearTimeout(timer); timer = setTimeout(run, ms);
  };
}
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const main = () => $("#main");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ------------------------------------------------------------------ api
async function api(path, opts = {}) {
  const init = { method: opts.method || (opts.body ? "POST" : "GET"), headers: {} };
  if (opts.body instanceof FormData) init.body = opts.body;
  else if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers["content-type"] = "application/json"; }
  const r = await fetch(path, init);
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = j.detail || JSON.stringify(j); } catch { }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r;
}
async function streamText(path, body, onChunk) {
  const r = await fetch(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body || {}) });
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail; } catch { } throw new Error(m); }
  const reader = r.body.getReader(); const dec = new TextDecoder(); let full = "";
  while (true) {
    const { value, done } = await reader.read(); if (done) break;
    const piece = dec.decode(value, { stream: true }); full += piece; onChunk && onChunk(full, piece);
  }
  if (full.includes("[ERROR]")) toast(full.split("[ERROR]").pop().trim(), "bad");
  refreshMeter();
  return full.replace(/\n*\[ERROR\][\s\S]*$/, "");
}
function toast(msg, kind = "") {
  const t = document.createElement("div"); t.className = "toast " + kind; t.textContent = msg;
  $("#toasts").appendChild(t); setTimeout(() => t.remove(), kind === "bad" ? 7000 : 3500);
}
async function busy(btn, fn) {
  const html = btn.innerHTML, spinning = `<span class="spin"></span>${html}`;
  btn.disabled = true; btn.innerHTML = spinning;
  try { return await fn(); } catch (e) { toast(e.message, "bad"); console.error(e); }
  finally { if (btn.innerHTML === spinning) { btn.disabled = false; btn.innerHTML = html; } }  // keep a label the action set (e.g. "✓ added")
}
const debounce = (fn, ms = 800) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
function download(blob, name) { const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 4000); }
const fmtDate = (t) => new Date(t * 1000).toLocaleString();
const wordCount = (s) => (String(s || "").replace(/\[[^\]]*@[^\]]*\]/g, "").match(/\b\w+\b/g) || []).length;

// ------------------------------------------------------------------ markdown
let LIBKEYS = new Set();
function md(text) {
  let src = String(text || "");
  src = src.replace(/\[((?:\s*-?@[^\]]+?;?)+)\]/g, (m, inner) => {
    const keys = [...inner.matchAll(/@([\w:\-]+)/g)].map((x) => x[1]);
    const miss = LIBKEYS.size && keys.some((k) => !LIBKEYS.has(k));
    return `<span class="cite${miss ? " missing" : ""}" title="${esc(keys.join(", "))}">${esc(m)}</span>`;
  });
  src = src.replace(/\[TODO[^\]]*\]/g, (m) => `<span class="todo">${esc(m)}</span>`);
  const html = window.marked ? marked.parse(src) : `<pre>${esc(src)}</pre>`;
  return window.DOMPurify ? DOMPurify.sanitize(html) : html;
}
function authorsShort(a) {
  a = a || []; if (!a.length) return "";
  const f = (x) => x.family || x.literal || "";
  if (a.length === 1) return f(a[0]); if (a.length === 2) return `${f(a[0])} & ${f(a[1])}`; return `${f(a[0])} et al.`;
}
function authorsFull(a) { return (a || []).map((x) => [x.given, x.family].filter(Boolean).join(" ")).join(", "); }

// ------------------------------------------------------------------ modal
function modal(html, wide = false) {
  $("#modalBox").className = "modal-box" + (wide ? " wide" : ""); $("#modalBox").innerHTML = html;
  $("#modal").classList.remove("hidden"); return $("#modalBox");
}
function closeModal() { $("#modal").classList.add("hidden"); $("#modalBox").innerHTML = ""; }
$("#modal").addEventListener("mousedown", (e) => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

// ------------------------------------------------------------------ boot
async function loadProjects(selectId) {
  S.projects = await api("/api/projects");
  const saved = selectId || localStorage.getItem("rt_project");
  S.project = S.projects.find((p) => p.id === saved) || S.projects[0];
  $("#projectSelect").innerHTML = S.projects.map((p) => `<option value="${p.id}" ${p.id === S.project.id ? "selected" : ""}>${esc(p.name)}</option>`).join("");
  localStorage.setItem("rt_project", S.project.id);
  await refreshLibKeys();
}
async function refreshLibKeys() { const papers = await api(`/api/projects/${S.project.id}/papers`); LIBKEYS = new Set(papers.map((p) => p.key)); return papers; }
function llmReady() { const s = S.settings; return s && s.llm_model && (s.llm_model_src === "2" ? (s.llm2_api_key || /localhost|127\.0\.0\.1/.test(s.llm2_base_url || "")) : (s.llm_api_key || /localhost|127\.0\.0\.1/.test(s.llm_base_url || ""))); }
const fmtTok = (n) => n >= 1e6 ? (n / 1e6).toFixed(2) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(n || 0);
async function refreshMeter() {
  try {
    const u = (await api("/api/usage?days=1")).total;
    const el = $("#usageMeter"); if (!el) return;
    el.innerHTML = `<a href="#usage" style="color:#9fc3ff;text-decoration:none">Today: ${fmtTok(u.input + u.output)} tokens${u.cost ? ` · $${u.cost < 0.01 ? u.cost.toFixed(4) : u.cost.toFixed(2)}` : ""}</a>`;
  } catch { }
}
async function checkUpdates(force) {
  try {
    const last = +localStorage.getItem("rt_upd_checked") || 0;
    if (!force && Date.now() - last < 6 * 3600e3 && !localStorage.getItem("rt_upd_available")) return null;
    const r = await api("/api/update/check"); localStorage.setItem("rt_upd_checked", Date.now());
    if (r.available && !r.dev_copy) localStorage.setItem("rt_upd_available", r.latest); else localStorage.removeItem("rt_upd_available");
    showUpdateBadge(); return r;
  } catch { return null; }
}
function showUpdateBadge() {
  const v = localStorage.getItem("rt_upd_available"); const el = $("#updBadge");
  if (el) el.innerHTML = v ? `<a href="#settings" style="display:block;margin:8px 6px 0;padding:8px 10px;border-radius:8px;background:#ffc857;color:#14213d;font-weight:600;text-decoration:none;font-size:13px">⬆ Update available: tap to install</a>` : "";
}
async function runUpdate(btn) {
  if (!confirm("Update A.F.R.A now? Your library, documents and settings are kept. A.F.R.A restarts in about a minute.")) return;
  await busy(btn, async () => {
    const r = await api("/api/update/apply", { method: "POST" });
    localStorage.removeItem("rt_upd_available");
    main().insertAdjacentHTML("afterbegin", `<div class="notice info" id="updMsg">Updated to version ${esc(r.updated_to || "?")}. Restarting…${r.components_ok ? "" : " (some components could not be installed: check your internet)"}</div>`);
    await new Promise((res) => setTimeout(res, 3000));
    for (let i = 0; i < 60; i++) { try { await api("/api/meta"); location.reload(); return; } catch { await new Promise((res) => setTimeout(res, 2000)); } }
    $("#updMsg").innerHTML = "Update installed. Please close and reopen A.F.R.A.";
  });
}
const THEMES = [
  ["auto", "Auto (follow my computer)", ["#14213d", "#f6f5f1", "#1f4e79"], ["#0b1019", "#0f1420", "#6aa3e8"]],
  ["navy", "Navy (default)", ["#14213d", "#f6f5f1", "#1f4e79"]],
  ["dark", "Midnight (dark)", ["#0b1019", "#0f1420", "#6aa3e8"]],
  ["forest", "Forest", ["#12302a", "#f4f6f2", "#1f6f50"]],
  ["plum", "Plum", ["#23143d", "#f7f5fa", "#5b3fa6"]],
  ["paper", "Paper (warm)", ["#3b2a20", "#f3ede2", "#8a4b2a"]],
  ["ocean", "Ocean", ["#0f3d4a", "#f2f7f7", "#0f766e"]],
];
const LOGOS = [["classic", "Classic book serif"], ["soft", "Soft serif"], ["editorial", "Editorial serif"]];
const logoUrl = () => `/static/logos/${document.documentElement.getAttribute("data-logo") || "classic"}.svg`;
function applyLogo(l) {
  try { localStorage.setItem("rt_logo", l); } catch { }
  document.documentElement.setAttribute("data-logo", l);
  const fav = $("#favicon"); if (fav) fav.href = `/static/logos/${l}.svg`;
  $$(".brand-logo").forEach((img) => img.src = `/static/logos/${l}.svg`);
}
function applyTheme(t) {
  try { localStorage.setItem("rt_theme", t); } catch { }
  const real = t === "auto" ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "navy") : t;
  if (real === "navy") document.documentElement.removeAttribute("data-theme"); else document.documentElement.setAttribute("data-theme", real);
}
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if ((localStorage.getItem("rt_theme") || "navy") === "auto") applyTheme("auto"); });
function renderLLMStatus() {
  const s = S.settings;
  $("#llmStatus").innerHTML = (llmReady() ? `<span class="dot ok"></span>${esc(s.llm_model)}${s.llm_fast_model ? `<div class="small" style="opacity:.75;margin-left:14px">fast: ${esc(s.llm_fast_model)}</div>` : ""}${s.llm_review_model ? `<div class="small" style="opacity:.75;margin-left:14px">review: ${esc(s.llm_review_model)}</div>` : ""}` : `<span class="dot"></span>AI not configured — <a href="#settings" style="color:#9fc3ff">Settings</a>`) + `<div id="usageMeter" style="margin-top:6px"></div><div id="updBadge"></div>`;
  showUpdateBadge();
  refreshMeter();
}
async function boot() {
  [S.meta, S.settings] = await Promise.all([api("/api/meta"), api("/api/settings")]);
  applyTheme(S.settings.ui_theme || "navy");
  applyLogo(S.settings.ui_logo || "classic");
  await loadProjects(); renderLLMStatus();
  $("#projectSelect").onchange = async (e) => { await loadProjects(e.target.value); route(); };
  $("#newProjectBtn").onclick = newProjectDialog;
  window.addEventListener("hashchange", route); route();
  setInterval(refreshMeter, 20000);
  checkUpdates(false);
  // never lose typing: warn before leaving while a save is still pending (drafts are also kept in localStorage)
  window.addEventListener("beforeunload", (e) => { if (S.pending > 0) { e.preventDefault(); e.returnValue = ""; } });
}
function newProjectDialog() {
  const box = modal(`<h2>New project</h2><label class="f">Name</label><input type="text" id="npName" placeholder="e.g. CCUS in depleted reservoirs">
    <label class="f">Description</label><textarea id="npDesc" rows="2"></textarea>
    <div class="modal-foot"><button class="btn" onclick="closeModal()">Cancel</button><button class="btn primary" id="npOk">Create</button></div>`);
  $("#npName", box).focus();
  $("#npOk", box).onclick = async () => {
    const r = await api("/api/projects", { body: { name: $("#npName").value || "Untitled project", description: $("#npDesc").value } });
    closeModal(); await loadProjects(r.id); route(); toast("Project created", "ok");
  };
}
const VIEWS = {};
function route() {
  const v = (location.hash.slice(1) || "home").split("/")[0];
  S.view = VIEWS[v] ? v : "home";
  $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === S.view));
  if (S.es) { S.es.close(); S.es = null; }
  main().scrollTop = 0;
  Promise.resolve(VIEWS[S.view]()).finally(addFooter);
}
const defaultSource = (k) => S.settings.default_sources.includes(k) && (k !== "semanticscholar" || !!S.settings.semantic_scholar_api_key) && (k !== "tavily" || !!S.settings.tavily_api_key);
// copyright footer at the end of every page
const YEARS = (() => { const y = new Date().getFullYear(); return y > 2026 ? `2026–${y}` : "2026"; })();
function addFooter() {
  if (!main() || $("#appFooter", main())) return;
  main().insertAdjacentHTML("beforeend", `<footer id="appFooter" class="app-footer">
    <div><img class="brand-logo" src="${logoUrl()}" alt=""><b>A.F.R.A</b> · Article Finder &amp; Research Assistant</div>
    <div>© ${YEARS} <b>danafarmansyah</b>. All rights reserved. · Crafted with <button class="heart dedi-heart" type="button" title="♥" aria-label="A little note">♥</button></div></footer>`);
}
// a small note, tucked behind the heart
function dedication() {
  modal(`<div class="dedi"><img src="${logoUrl()}" alt=""><h2>For Raina Afra</h2><p>This little app carries your name. I built it so your long research nights feel a little lighter, and so every paper you finish has someone quietly cheering you on.</p><div class="sig">— Dana</div>
    <button class="btn" onclick="closeModal()">Close</button></div>`);
}
document.addEventListener("click", (e) => { if (e.target.closest(".dedi-heart")) dedication(); });
const needLLM = () => llmReady() ? "" : `<div class="notice">AI model is not configured yet. Go to <a href="#settings">Settings</a> and enter your API key (OpenAI, Anthropic, DeepSeek, OpenRouter, Gemini, or a local Ollama model).</div>`;

// ================================================================== HOME
VIEWS.home = async () => {
  const p = S.projects.find((x) => x.id === S.project.id) || {};
  main().innerHTML = `
  <div class="hero"><h1 style="display:flex;align-items:center;gap:14px"><img class="brand-logo" src="${logoUrl()}" alt="" style="width:46px;height:46px;border-radius:11px;box-shadow:0 4px 14px rgba(0,0,0,.25)">A.F.R.A</h1><div style="color:#9fc3ff;margin:-4px 0 8px;font-weight:600">Article Finder &amp; Research Assistant</div>
    <p>From question to a finished, properly formatted manuscript: deep research with real citations, a literature library,
    a guided paper-writing workflow with reviewer gates, and a DOCX export that follows APA, IEEE, journal and thesis formats.</p></div>
  ${needLLM()}
  <div class="grid g3" style="margin-bottom:16px">
    <div class="card"><div class="muted small">Library</div><div class="big-score">${p.n_papers || 0}</div><div class="muted small">sources in “${esc(p.name)}”</div></div>
    <div class="card"><div class="muted small">Documents & manuscripts</div><div class="big-score">${p.n_docs || 0}</div></div>
    <div class="card"><div class="muted small">Deep research runs</div><div class="big-score">${p.n_runs || 0}</div></div>
  </div>
  <h2>Workflow</h2>
  <div class="flow">
    <a href="#research"><div class="card"><div class="n">1 · EXPLORE</div><h3>Deep Research</h3><p class="muted small">Ask a question; the agent plans queries, searches OpenAlex, Semantic Scholar, arXiv & the web, and writes a cited literature report.</p></div></a>
    <a href="#search"><div class="card"><div class="n">2 · COLLECT</div><h3>Search & Library</h3><p class="muted small">Find papers, import DOIs, BibTeX or PDFs, take AI reading notes. Every source gets a citation key like <span class="key">@smith2020deep</span>.</p></div></a>
    <a href="#writer"><div class="card"><div class="n">3 · WRITE</div><h3>Paper Writer</h3><p class="muted small">Strategist → Composer: venue style guide, evidence-backed gaps, outline, 35-point reviewer gate, section drafting with quality checks.</p></div></a>
    <a href="#docs"><div class="card"><div class="n">4 · FINISH</div><h3>Edit & Export DOCX</h3><p class="muted small">Edit in Markdown with live preview and AI tools, then export to Word with the correct template, headings, citations and reference list.</p></div></a>
  </div>
  <div class="card" style="margin-top:16px"><h3>Citing sources</h3>
    <p class="muted" style="margin:0">Write citations as <code>[@key]</code>, <code>[@key, p. 12]</code>, <code>[@key1; @key2]</code> or narrative <code>@key</code>.
    On export they become “(Smith et al., 2020)”, “[1]”, etc. according to the chosen style, and the reference list is generated automatically from your library.</p></div>`;
};

// ================================================================== DEEP RESEARCH
VIEWS.research = async () => {
  const st = S.settings;
  main().innerHTML = `
  <div class="page-head"><div><h1>Deep Research</h1><p>An iterative research agent: it generates search queries, reads scholarly and web sources, extracts learnings tied to their sources, digs deeper with follow-up questions, then writes a cited report.</p></div></div>
  ${needLLM()}
  <div class="split">
    <div>
      <div class="card">
        <label class="f">Research question</label>
        <textarea id="rq" rows="4" placeholder="e.g. What is the current evidence on CO2 storage capacity and leakage risk in depleted gas reservoirs in Southeast Asia?"></textarea>
        <div id="clarifyBox"></div>
        <div class="grid g2" style="gap:10px">
          <div><label class="f">Breadth <small>queries per level</small></label><input type="number" id="breadth" value="3" min="1" max="8"></div>
          <div><label class="f">Depth <small>levels</small></label><input type="number" id="depth" value="2" min="1" max="4"></div>
          <div><label class="f">Year from</label><input type="number" id="yf" placeholder="any"></div>
          <div><label class="f">Year to</label><input type="number" id="yt" placeholder="any"></div>
        </div>
        <label class="f">Sources</label>
        <div class="checks" id="srcChecks">${Object.entries(S.meta.sources).map(([k, v]) => `<label><input type="checkbox" value="${k}" ${defaultSource(k) ? "checked" : ""}>${esc(v)}</label>`).join("")}</div>
        <div class="grid g2" style="gap:10px">
          <div><label class="f">Source quality <small>(no AI tokens)</small></label><select id="rqq"><option value="">Any source</option><option value="Q3">Q1–Q3 journals</option><option value="Q2">Q1–Q2 journals</option><option value="Q1">Q1 journals only</option></select></div>
          <div><label class="f">&nbsp;</label><div class="checks"><label><input type="checkbox" id="rqpre" checked>Allow preprints & web</label><label><input type="checkbox" id="rqunr">Allow unranked</label></div></div>
        </div>
        <div class="grid g2" style="gap:10px">
          <div><label class="f">Report type</label><select id="rtype">
            ${["Literature review", "State-of-the-art review", "Research report", "Background & related work section", "Research proposal background", "Policy / technical brief"].map((x) => `<option>${x}</option>`).join("")}</select></div>
          <div><label class="f">Language</label><select id="rlang">${["English", "Indonesian", "Malay", "Spanish", "French", "German", "Chinese", "Arabic", "Japanese"].map((l) => `<option ${l === st.default_language ? "selected" : ""}>${l}</option>`).join("")}</select></div>
          <div><label class="f">Target length (words)</label><input type="number" id="rwords" value="2000" step="250"></div>
          <div><label class="f">Results per query</label><input type="number" id="perq" value="6" min="2" max="15"></div>
        </div>
        <div class="checks" style="margin-top:10px"><label><input type="checkbox" id="readfull">Read full pages / open-access PDFs (slower, more evidence)</label></div>
        <label class="f">Extra instructions <small>(optional)</small></label><input type="text" id="rextra" placeholder="e.g. focus on field case studies; include a comparison table">
        <div class="row" style="margin-top:14px"><button class="btn" id="clarifyBtn">Ask clarifying questions</button><button class="btn primary" id="startBtn">Start research</button></div>
      </div>
      <div class="card"><h3>Previous runs</h3><div id="runList" class="small"></div></div>
    </div>
    <div id="runPane"><div class="card empty">Start a research run, or open a previous one.</div></div>
  </div>`;
  const loadRuns = async () => {
    const runs = await api(`/api/projects/${S.project.id}/runs`);
    $("#runList").innerHTML = runs.length ? runs.map((r) => `<div class="list-item" data-id="${r.id}"><div class="t">${esc(r.query.slice(0, 90))}</div>
      <div class="muted">${fmtDate(r.created)} · <span class="pill ${r.status === "done" ? "ok" : r.status === "error" ? "bad" : "warn"}">${r.status}</span>${r.status === "interrupted" ? ` <span class="small">progress saved</span>` : ""}</div></div>`).join("") : `<div class="muted">No runs yet.</div>`;
    $$("#runList .list-item").forEach((el) => el.onclick = () => openRun(el.dataset.id));
  };
  loadRuns();
  let clar = [];
  $("#clarifyBtn").onclick = (e) => busy(e.currentTarget, async () => {
    if (!$("#rq").value.trim()) return toast("Enter a research question first");
    const r = await api("/api/research/clarify", { body: { query: $("#rq").value, language: $("#rlang").value } });
    clar = r.questions;
    $("#clarifyBox").innerHTML = `<div class="notice info" style="margin-top:10px"><b>Clarifying questions</b> (optional — answer what you can)</div>` +
      clar.map((q, i) => `<label class="f">${esc(q)}</label><input type="text" data-ci="${i}">`).join("");
  });
  $("#startBtn").onclick = (e) => busy(e.currentTarget, async () => {
    const q = $("#rq").value.trim(); if (!q) return toast("Enter a research question first");
    const params = {
      breadth: +$("#breadth").value, depth: +$("#depth").value, year_from: +$("#yf").value || null, year_to: +$("#yt").value || null,
      sources: $$("#srcChecks input:checked").map((x) => x.value), report_type: $("#rtype").value, language: $("#rlang").value,
      report_words: +$("#rwords").value, per_query: +$("#perq").value, read_full: $("#readfull").checked, report_instructions: $("#rextra").value,
      max_quartile: $("#rqq").value || null, allow_preprints: $("#rqpre").checked, allow_unranked: $("#rqunr").checked,
      clarifications: clar.map((c, i) => ({ q: c, a: ($(`[data-ci="${i}"]`) || {}).value || "" })),
    };
    const r = await api("/api/research/start", { body: { project_id: S.project.id, query: q, params } });
    openRun(r.id); setTimeout(loadRuns, 500);
  });

  function openRun(id) {
    if (S.es) S.es.close();
    const run = { tree: [], report: "", sources: [], learnings: [], status: "running" };
    let tab = "report", statusMsg = "";
    const pane = $("#runPane");
    pane.innerHTML = `<div class="card">
      <div id="runHead"></div>
      <div class="tabs" id="runTabs">${["report", "tree", "learnings", "sources"].map((t) => `<button data-t="${t}" class="${t === tab ? "active" : ""}">${t[0].toUpperCase() + t.slice(1)}</button>`).join("")}</div>
      <div id="runBody"></div></div>`;
    $$("#runTabs button").forEach((b) => b.onclick = () => { tab = b.dataset.t; $$("#runTabs button").forEach((x) => x.classList.toggle("active", x === b)); draw(); });
    const head = () => {
      const running = run.status === "running";
      $("#runHead").innerHTML = `<h2 style="margin-bottom:6px">${esc(run.query || "")}</h2>
        ${running ? `<div class="statusbar"><span class="spin" style="width:14px;height:14px;border:2px solid var(--accent);border-right-color:transparent;border-radius:50%;display:inline-block;animation:spin .8s linear infinite"></span>${esc(statusMsg || "Working…")} · ${run.tree.length} queries · ${run.sources.length} sources · ${run.learnings.length} learnings</div>` :
          `<div class="row small muted" style="margin-bottom:10px"><span class="pill ${run.status === "done" ? "ok" : "bad"}">${run.status}</span>${run.tree.length} queries · ${run.sources.length} sources · ${run.learnings.length} learnings</div>`}
        ${!running && run.report ? `<div class="toolbar"><button class="btn primary small" id="rExport">Export DOCX</button><button class="btn small" id="rSave">Save as document</button>
          <button class="btn small" id="rAddAll">Add all ${run.sources.length} sources to library</button><button class="btn small" id="rCopy">Copy Markdown</button>
          <span class="sep"></span><button class="btn small danger" id="rDel">Delete run</button></div>` : ""}`;
      if (!running && run.report) {
        $("#rExport").onclick = () => exportDialog({ markdown: run.report, meta: {} });
        $("#rSave").onclick = async () => { const d = await api(`/api/projects/${S.project.id}/documents`, { body: { title: (run.report.match(/^#\s+(.+)$/m) || [, run.query])[1].slice(0, 120), content: run.report, kind: "doc" } }); toast("Saved to Documents", "ok"); location.hash = "#docs/" + d.id; };
        $("#rAddAll").onclick = (e) => busy(e.currentTarget, async () => { const r = await api(`/api/research/${id}/add-sources`, { method: "POST" }); await refreshLibKeys(); toast(`${r.added} sources in library`, "ok"); });
        $("#rCopy").onclick = () => { navigator.clipboard.writeText(run.report); toast("Copied"); };
        $("#rDel").onclick = async () => { if (!confirm("Delete this run?")) return; await api(`/api/research/${id}`, { method: "DELETE" }); pane.innerHTML = ""; loadRuns(); };
      }
    };
    const draw = () => {
      const body = $("#runBody"); if (!body) return;
      if (tab === "report") body.innerHTML = run.report ? `<div class="prose">${md(run.report)}</div>` : `<div class="empty">The report is written after the research tree completes.</div>`;
      if (tab === "tree") body.innerHTML = treeHTML(run.tree);
      if (tab === "learnings") body.innerHTML = run.learnings.length ? `<ol>${run.learnings.map((l) => `<li style="margin-bottom:6px">${esc(l.text)} <span class="muted small">${(l.sources || []).map((s) => `[${s}]`).join("")}</span></li>`).join("")}</ol>` : `<div class="empty">No learnings yet.</div>`;
      if (tab === "sources") body.innerHTML = run.sources.length ? run.sources.map((s) => `<div class="result"><div class="t">[${s.sid}] ${esc(s.title)}</div>
        <div class="m">${esc(authorsShort(s.authors))} ${s.year ? "(" + s.year + ")" : ""} · ${esc(s.venue || s.type)} ${s.key ? `· <span class="key">@${esc(s.key)}</span>` : ""} ${s.url ? `· <a href="${esc(s.url)}" target="_blank">open</a>` : ""}</div></div>`).join("") : `<div class="empty">No sources yet.</div>`;
      $$(".node-head", body).forEach((h) => h.onclick = () => { h.parentElement.classList.toggle("open"); });
    };
    const redraw = () => { head(); draw(); };
    S.es = new EventSource(`/api/research/${id}/events`);
    let pending = false;
    const schedule = () => { if (!pending) { pending = true; requestAnimationFrame(() => { pending = false; redraw(); }); } };
    S.es.onmessage = (m) => {
      const ev = JSON.parse(m.data);
      if (ev.type === "snapshot") { Object.assign(run, ev.run || {}); if (run.status === "running" && !run.report) tab = "tree"; $$("#runTabs button").forEach((x) => x.classList.toggle("active", x.dataset.t === tab)); }
      else if (ev.type === "node") { const i = run.tree.findIndex((n) => n.id === ev.node.id); if (i >= 0) run.tree[i] = ev.node; else run.tree.push(ev.node); run.learnings = run.tree.flatMap((n) => n.learnings || []); run.sources = run.sources || []; ev.node.results?.forEach((r) => { if (!run.sources.find((s) => s.sid === r.sid)) run.sources.push(r); }); }
      else if (ev.type === "status") statusMsg = ev.message;
      else if (ev.type === "report") { if (tab === "tree" && !run.report) { tab = "report"; $$("#runTabs button").forEach((x) => x.classList.toggle("active", x.dataset.t === tab)); } run.report += ev.delta; }
      else if (ev.type === "error") toast(ev.message, "bad");
      else if (ev.type === "done") { S.es.close(); S.es = null; api(`/api/research/${id}`).then((r) => { Object.assign(run, r); redraw(); refreshLibKeys(); loadRuns(); }); return; }
      schedule();
    };
    S.es.onerror = () => { if (S.es && run.status !== "running") S.es.close(); };
  }
};
function treeHTML(nodes) {
  if (!nodes.length) return `<div class="empty">Planning search queries…</div>`;
  const kids = (pid) => nodes.filter((n) => (n.parent || null) === pid);
  const icon = { searching: "…", analyzing: "…", done: "✓", error: "!" };
  const render = (pid) => `<ul class="tree">${kids(pid).map((n) => `<li class="node ${n.status === "error" ? "open" : ""}">
    <div class="node-head"><span class="st ${n.status}">${icon[n.status] || ""}</span><div><div class="node-q">${esc(n.query)}</div><div class="node-goal">${esc(n.goal)} · ${n.status}${n.results?.length ? ` · ${n.results.length} results` : ""}${n.learnings?.length ? ` · ${n.learnings.length} learnings` : ""}</div></div></div>
    <div class="node-body">${n.error ? `<div class="notice bad">${esc(n.error)}</div>` : ""}${(n.errors || []).map((e) => `<div class="muted small">⚠ ${esc(e)}</div>`).join("")}
      ${n.learnings?.length ? `<b>Learnings</b><ul>${n.learnings.map((l) => `<li>${esc(l.text)} <span class="muted">${(l.sources || []).map((s) => `[${s}]`).join("")}</span></li>`).join("")}</ul>` : ""}
      ${n.results?.length ? `<b>Sources</b><ul>${n.results.map((r) => `<li>[${r.sid}] <a href="${esc(r.url)}" target="_blank">${esc(r.title)}</a> ${r.year ? "(" + r.year + ")" : ""}</li>`).join("")}</ul>` : ""}
    </div>${render(n.id)}</li>`).join("")}</ul>`;
  return render(null);
}

// ================================================================== SEARCH
const QORD = { Q1: 1, Q2: 2, Q3: 3, Q4: 4 };
function qBadges(p) {
  const q = p.quality || {}; const out = [];
  if (p.retracted) out.push(`<span class="pill bad" title="This paper has been retracted — do not cite it as evidence">RETRACTED</span>`);
  if (q.quartile) out.push(`<span class="pill q${q.quartile[1]}" title="${q.q_source === "SJR" ? "SCImago Journal Rank best quartile" : "Estimated from OpenAlex citation statistics. Import the SJR file in Settings for official quartiles."}">${q.quartile}${q.q_source === "SJR" ? "" : " est."}</span>`);
  if (q.sjr) out.push(`<span class="pill gray" title="SCImago Journal Rank">SJR ${q.sjr}</span>`);
  if (q.impact2y != null) out.push(`<span class="pill gray" title="OpenAlex 2-year mean citedness (comparable to Impact Factor)">IF≈${q.impact2y}</span>`);
  if (q.h_index) out.push(`<span class="pill gray" title="Journal h-index">h ${q.h_index}</span>`);
  if (q.doaj) out.push(`<span class="pill ok" title="Listed in the Directory of Open Access Journals">DOAJ</span>`);
  if (p.is_oa || p.pdf_url) out.push(`<span class="pill ok">Open access</span>`);
  return out.join(" ");
}
const SR = { results: [], label: "", back: null };
VIEWS.search = async () => {
  main().innerHTML = `
  <div class="page-head"><div><h1>Literature Search</h1><p>Search scholarly databases at once, filter by journal quality (Q1–Q4), follow citation trails, and find where to publish. Only <b>Discover</b> and <b>Evidence</b> use the AI, with 1–2 small calls on the fast model.</p></div></div>
  <div class="tabs" id="stabs"><button class="active" data-t="papers">Find papers</button><button data-t="manuscript">From my manuscript</button><button data-t="evidence">Evidence for my argument</button><button data-t="journals">Journal finder (where to publish)</button></div>
  <div id="sbody"></div>`;
  const tabs = { papers: papersTab, manuscript: manuscriptTab, evidence: evidenceTab, journals: journalsTab };
  $$("#stabs button").forEach((b) => b.onclick = () => { $$("#stabs button").forEach((x) => x.classList.toggle("active", x === b)); tabs[b.dataset.t](); });
  papersTab();
  if (S.snowball) { const sb = S.snowball; S.snowball = null; runSnowball(sb.ident, sb.mode, sb.title); }
};
async function runSnowball(ident, mode, title) {
  const label = { references: "References cited by", citing: "Papers citing", related: "Papers related to" }[mode];
  $("#sres").innerHTML = `<div class="card empty"><span class="spin" style="display:inline-block;width:14px;height:14px;border:2px solid var(--accent);border-right-color:transparent;border-radius:50%;animation:spin .8s linear infinite"></span> Loading ${label.toLowerCase()} “${esc(title)}”…</div>`;
  try {
    const r = await api(`/api/snowball?ident=${encodeURIComponent(ident)}&mode=${mode}`);
    SR.back = SR.label || SR.results.length ? { results: SR.results, label: SR.label } : null;
    SR.results = r.results; SR.label = `${label} “${r.seed || title}”${mode === "citing" && r.total_citing ? ` — showing top ${r.results.length} of ${r.total_citing.toLocaleString()}` : ""}`;
    drawResults();
  } catch (e) { toast(e.message, "bad"); $("#sres").innerHTML = ""; }
}
function papersTab() {
  $("#sbody").innerHTML = `<div class="card">
    <div class="row"><select id="smode" style="width:270px"><option value="discover">Discover: everything on my title/topic</option><option value="quick">Quick keyword search (no AI)</option></select>
      <input type="text" class="grow" id="sq" placeholder="Paste your research title or topic…"><button class="btn primary" id="sgo">Search</button></div>
    <div class="small muted" id="smodehint" style="margin-top:6px">Discover splits your title into concepts and synonyms (1 small AI call). It then runs many searches across databases, follows citation trails from the best papers, and ranks up to 200 papers by how well they cover your topic. Takes about 20–60 s.</div>
    <div class="checks" id="sSrc" style="margin-top:10px">${Object.entries(S.meta.sources).map(([k, v]) => `<label><input type="checkbox" value="${k}" ${defaultSource(k) ? "checked" : ""}>${esc(v)}</label>`).join("")}</div>
    <div class="row" style="margin-top:8px"><label class="small">Years</label><input type="number" id="syf" placeholder="from" style="width:90px"><input type="number" id="syt" placeholder="to" style="width:90px">
      <label class="small">Per source</label><input type="number" id="slim" value="10" style="width:70px">
      <label class="small">Sort</label><select id="ssort" style="width:170px"><option value="rel">Relevance</option><option value="q">Journal quality</option><option value="cit">Most cited</option><option value="new">Newest</option></select></div>
    <div class="row" style="margin-top:10px"><b class="small">Journal quality</b><div class="checks" id="qf">${["Q1", "Q2", "Q3", "Q4", "Unranked"].map((q) => `<label><input type="checkbox" value="${q}" checked>${q}</label>`).join("")}</div>
      <span class="sep"></span><div class="checks"><label><input type="checkbox" id="fOA">Open access only</label><label><input type="checkbox" id="fPre">Hide preprints & web</label><label><input type="checkbox" id="fRet" checked>Hide retracted</label></div>
      <label class="small">Min. citations</label><input type="number" id="fCit" style="width:80px" min="0"></div>
    ${S.meta.sjr_journals ? `<div class="small muted" style="margin-top:6px">Official SJR quartiles loaded for ${S.meta.sjr_journals.toLocaleString()} journals.</div>` : `<div class="small muted" style="margin-top:6px">Quartiles marked “est.” are estimated from OpenAlex citation data. For official SJR quartiles, import the free SCImago file once in <a href="#settings">Settings</a>.</div>`}
  </div><div id="sres"></div>`;
  ["#ssort", "#fOA", "#fPre", "#fRet", "#fCit"].forEach((sel) => $(sel).onchange = drawResults);
  $$("#qf input").forEach((c) => c.onchange = drawResults);
  const go = () => busy($("#sgo"), async () => {
    const q = $("#sq").value.trim(); if (!q) return;
    const srcs = $$("#sSrc input:checked").map((x) => x.value);
    if ($("#smode").value === "discover") {
      $("#sres").innerHTML = `<div class="card empty">Discovering… splitting your title into concepts, searching ${srcs.length} databases with many queries, then following citations. This takes about 20–60 seconds.</div>`;
      const r = await api("/api/discover", { body: { title: q, sources: srcs, year_from: +$("#syf").value || null, year_to: +$("#syt").value || null } });
      SR.results = r.results; SR.back = null; SR.plan = r.plan;
      SR.label = `Discover: ${r.results.length} most relevant of ${r.total_found} papers found`;
      r.errors.forEach((e) => toast(e, "bad")); drawResults(); return;
    }
    SR.plan = null;
    const params = new URLSearchParams({ q, sources: $$("#sSrc input:checked").map((x) => x.value).join(","), limit: $("#slim").value });
    if ($("#syf").value) params.set("year_from", $("#syf").value); if ($("#syt").value) params.set("year_to", $("#syt").value);
    const r = await api("/api/search?" + params); SR.results = r.results; SR.label = ""; SR.back = null;
    r.errors.forEach((e) => toast(e, "bad")); drawResults();
  });
  $("#sgo").onclick = go; $("#sq").onkeydown = (e) => { if (e.key === "Enter") go(); }; $("#sq").focus();
  $("#smode").onchange = () => { $("#smodehint").style.display = $("#smode").value === "discover" ? "" : "none"; };
  if (SR.results.length) drawResults();
}
function filteredResults() {
  const qs = $$("#qf input:checked").map((x) => x.value), minC = +$("#fCit").value || 0;
  let list = SR.results.filter((p) => {
    if ($("#fRet").checked && p.retracted) return false;
    if ($("#fOA").checked && !(p.is_oa || p.pdf_url)) return false;
    if ((p.citations || 0) < minC) return false;
    if (p.type === "webpage" || p.type === "preprint") return !$("#fPre").checked;
    const q = p.quality?.quartile; return q ? qs.includes(q) : qs.includes("Unranked");
  });
  const sort = $("#ssort").value;
  if (sort === "cit") list.sort((a, b) => (b.citations || 0) - (a.citations || 0));
  if (sort === "new") list.sort((a, b) => (b.year || 0) - (a.year || 0));
  if (sort === "q") list.sort((a, b) => (QORD[a.quality?.quartile] || 9) - (QORD[b.quality?.quartile] || 9) || (b.citations || 0) - (a.citations || 0));
  return list;
}
function drawResults() {
  if (!$("#sres")) return;
  const list = filteredResults();
  $("#sres").innerHTML = SR.results.length ? `<div class="card"><div class="row" style="justify-content:space-between">
      <div>${SR.back ? `<button class="btn small ghost" id="sback">← Back</button> ` : ""}<b>${SR.label ? esc(SR.label) : `${list.length} of ${SR.results.length} results`}</b>${SR.label ? ` <span class="muted small">(${list.length} shown after filters)</span>` : ""}</div>
      <button class="btn small" id="addAll">Add ${list.length} shown to library</button></div>
    ${SR.plan && !SR.back ? `<details style="margin:8px 0"><summary class="small muted">Search strategy: ${SR.plan.concepts.map((c) => esc(c.name)).join(" · ")}${SR.plan.ai ? "" : " (keyword fallback — set up AI in Settings for synonym expansion)"}</summary>
      <div class="small">${SR.plan.concepts.map((c) => `<div><b>${esc(c.name)}</b>${c.essential === false ? " (optional)" : ""}: ${c.synonyms.map(esc).join(", ")}</div>`).join("")}${SR.plan.queries.length ? `<div style="margin-top:4px">Queries: ${SR.plan.queries.map(esc).join(" · ")}</div>` : ""}</div></details>` : ""}
    ${list.map((p) => { const i = SR.results.indexOf(p); const id = p.doi || p.openalex_id; return `<div class="result"><div class="t">${esc(p.title)}</div>
      <div class="m">${esc(authorsShort(p.authors))} ${p.year ? "(" + p.year + ")" : ""} · <i>${esc(p.venue || "")}</i> ${p.citations != null ? `· ${p.citations.toLocaleString()} citations` : ""} · <span class="pill gray">${p.source}</span> ${p.type !== "article" ? `<span class="pill gray">${p.type}</span>` : ""}</div>
      <div style="margin:-2px 0 6px">${qBadges(p)}${p._coverage ? ` <span class="small muted">covers: ${p._coverage.map(esc).join(", ")}</span>` : ""}</div>
      ${p.abstract ? `<div class="abs clamp" onclick="this.classList.toggle('clamp')">${esc(p.abstract)}</div>` : ""}
      <div class="row" style="margin-top:6px"><button class="btn small primary" data-add="${i}">${LIBKEYS.size && [...LIBKEYS].length && p._added ? "✓ In library" : "+ Library"}</button>
      ${p.url ? `<a class="btn small" href="${esc(p.url)}" target="_blank">Open</a>` : ""}${p.pdf_url ? `<a class="btn small" href="${esc(p.pdf_url)}" target="_blank">PDF</a>` : ""}
      ${id ? `<span class="sep"></span><button class="btn small ghost" data-sb="references" data-i="${i}" title="Backward snowballing">References</button><button class="btn small ghost" data-sb="citing" data-i="${i}" title="Forward snowballing">Cited by</button><button class="btn small ghost" data-sb="related" data-i="${i}">Related</button>` : ""}</div></div>`; }).join("") || `<div class="empty">No results match the filters.</div>`}</div>` : "";
  $$("[data-add]").forEach((b) => b.onclick = () => busy(b, async () => {
    const p = SR.results[+b.dataset.add]; const [stored] = await api(`/api/projects/${S.project.id}/papers`, { body: { paper: p } });
    LIBKEYS.add(stored.key); p._added = true; b.textContent = "✓ @" + stored.key; b.disabled = true;
  }));
  $$("[data-sb]").forEach((b) => b.onclick = () => { const p = SR.results[+b.dataset.i]; runSnowball(p.openalex_id || p.doi, b.dataset.sb, p.title); });
  const back = $("#sback"); if (back) back.onclick = () => { Object.assign(SR, SR.back, { back: null }); drawResults(); };
  const aa = $("#addAll"); if (aa) aa.onclick = () => busy(aa, async () => { const r = await api(`/api/projects/${S.project.id}/papers`, { body: { papers: filteredResults() } }); r.forEach((p) => LIBKEYS.add(p.key)); toast(`${r.length} papers in library`, "ok"); });
}
const STANCE = { supports: ["Supports", "ok"], partially: ["Partially supports", "warn"], contradicts: ["Contradicts", "bad"] };
function evidenceHTML(r) {
  if (!r.results.length) return `<div class="empty">No supporting or contradicting papers found among ${r.screened || 0} screened. Try rephrasing the argument more specifically.</div>`;
  return `<div class="small muted" style="margin-bottom:6px">${r.found} candidates found → ${r.screened} best abstracts screened by AI → ${r.results.length} relevant. Quotes marked ✓ were verified word-for-word in the abstract. Always read the paper before citing.</div>` +
    r.results.map((p, i) => `<div class="result"><div class="row" style="gap:8px"><span class="pill ${STANCE[p.stance][1]}">${STANCE[p.stance][0]}</span>${p.in_library ? '<span class="pill gray">in library</span>' : ""}<span class="t" style="font-size:15px">${esc(p.title)}</span></div>
      <div class="m">${esc(authorsShort(p.authors))} ${p.year ? "(" + p.year + ")" : ""} · <i>${esc(p.venue || "")}</i> ${p.citations != null ? `· ${p.citations} citations` : ""} ${qBadges(p)}</div>
      ${p.quote ? `<blockquote style="margin:6px 0;font-family:var(--serif)">“${esc(p.quote)}” <span class="small" title="${p.quote_verified ? "Found verbatim in the abstract" : "Could not verify verbatim — check the paper"}">${p.quote_verified ? "✓" : "⚠"}</span></blockquote>` : ""}
      <div class="small muted">${esc(p.reason || "")}</div>
      <div class="row" style="margin-top:6px"><button class="btn small primary" data-ev="${i}">Add & cite</button>${p.url ? `<a class="btn small" href="${esc(p.url)}" target="_blank">Open</a>` : ""}${p.pdf_url ? `<a class="btn small" href="${esc(p.pdf_url)}" target="_blank">PDF</a>` : ""}</div></div>`).join("");
}
function bindEvidence(root, r, onCite) {
  $$("[data-ev]", root).forEach((b) => b.onclick = () => busy(b, async () => {
    const p = r.results[+b.dataset.ev]; const [stored] = await api(`/api/projects/${S.project.id}/papers`, { body: { paper: p, tags: "evidence" } });
    LIBKEYS.add(stored.key); onCite(`[@${stored.key}]`, p); b.textContent = `✓ [@${stored.key}]`; b.disabled = true;
  }));
}
async function runEvidence(claim, root, onCite, sources) {
  root.innerHTML = `<div class="card empty">Searching for evidence… planning queries, searching databases, then screening the best abstracts (about 20–40 s).</div>`;
  const r = await api("/api/evidence", { body: { claim, project_id: S.project.id, sources } });
  r.errors.forEach((e) => toast(e, "bad"));
  root.innerHTML = `<div class="card"><div class="row" style="justify-content:space-between"><b>Evidence for: “${esc(claim.slice(0, 160))}${claim.length > 160 ? "…" : ""}”</b><button class="btn small ghost" onclick="this.closest('.card').remove()">✕</button></div>${evidenceHTML(r)}</div>`;
  bindEvidence(root, r, onCite);
}
function evidenceTab() {
  $("#sbody").innerHTML = `<div class="card"><p class="muted small" style="margin-top:0">Write your argument or opinion. The tool finds papers whose abstracts <b>support</b>, <b>partially support</b> or <b>contradict</b> it, quotes the sentence that backs it, and lets you add and cite each one with one click. Your library is checked too.</p>
    <textarea id="evc" rows="4" placeholder="e.g. Adding calcium sulfate to monetite granule cement shortens setting time while maintaining wash-out resistance."></textarea>
    <div class="checks" id="evSrc" style="margin-top:8px">${["openalex", "europepmc", "semanticscholar", "crossref"].map((k) => `<label><input type="checkbox" value="${k}" ${k === "openalex" || k === "europepmc" || (k === "semanticscholar" && S.settings.semantic_scholar_api_key) ? "checked" : ""}>${esc(S.meta.sources[k])}</label>`).join("")}</div>
    <div class="row" style="margin-top:10px"><button class="btn primary" id="evgo">Find evidence</button></div></div><div id="evres"></div>`;
  $("#evgo").onclick = (e) => busy(e.currentTarget, async () => {
    const claim = $("#evc").value.trim(); if (!claim) return toast("Write your argument first");
    await runEvidence(claim, $("#evres"), (cite) => { navigator.clipboard.writeText(cite).catch(() => { }); toast(`Added to library — ${cite} copied`, "ok"); }, $$("#evSrc input:checked").map((x) => x.value));
  });
}
function manuscriptTab() {
  $("#sbody").innerHTML = `<div class="card"><p class="muted small" style="margin-top:0">Upload the article you’re working on (.docx, .pdf or .md). It is saved to <b>Documents</b> so you can keep editing it here. Then you get:
    what the manuscript is about, what’s missing or weak, claims that still need a citation (with one-click <b>Find evidence</b>), references already in it to import, suggested searches, and a <b>Discover</b> run for what to read next. Cost: 1 small AI call.</p>
    <label class="btn primary">Upload my manuscript<input type="file" id="msf" accept=".docx,.pdf,.md,.txt" hidden></label></div><div id="msres"></div>`;
  $("#msf").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    $("#msres").innerHTML = `<div class="card empty">Reading and analysing “${esc(f.name)}”…</div>`;
    const fd = new FormData(); fd.append("file", f);
    try {
      const r = await api(`/api/manuscript/analyze?project_id=${S.project.id}`, { body: fd }); const a = r.analysis;
      $("#msres").innerHTML = `<div class="card"><div class="row" style="justify-content:space-between"><h3 style="margin:0">${esc(a.title || f.name)}</h3><a class="btn small" href="#docs/${r.doc_id}">Open in editor</a></div>
        <div class="small muted">${a.words.toLocaleString()} words · ${esc(a.stage || "")} · ${esc(a.paper_type || "")} · ${esc(a.field || "")}</div>
        <p>${esc(a.summary || "")}</p>${a.research_question ? `<p><b>Research question:</b> ${esc(a.research_question)}</p>` : ""}
        <div class="grid g2"><div><b>Next steps</b><ol class="small">${(a.next_steps || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ol></div>
        <div>${(a.missing_sections || []).length ? `<b>Missing sections</b><ul class="small">${a.missing_sections.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
        ${(a.weak_sections || []).length ? `<b>Weak sections</b><ul class="small">${a.weak_sections.map((x) => `<li><b>${esc(x.section)}</b>: ${esc(x.issue)}</li>`).join("")}</ul>` : ""}</div></div>
        <div class="row" style="margin-top:10px"><button class="btn primary" id="msdisc">Discover what to read next</button><button class="btn" id="msgap">Run Gap Finder on this</button>${a.dois.length ? `<button class="btn" id="msdoi">Import ${a.dois.length} references (DOIs found) to library</button>` : ""}</div></div>
        ${(a.claims_needing_sources || []).length ? `<div class="card"><h3>Claims that need a source (${a.claims_needing_sources.length})</h3>${a.claims_needing_sources.map((c, i) => `<div class="result"><div style="font-family:var(--serif)">“${esc(c.sentence)}”</div><div class="small muted">${esc(c.why || "")}</div><button class="btn small" data-cl="${i}">Find evidence</button><div id="clres${i}"></div></div>`).join("")}</div>` : ""}
        ${(a.search_topics || []).length ? `<div class="card"><h3>Suggested searches</h3>${a.search_topics.map((t) => `<div class="row" style="margin:4px 0"><button class="btn small ghost" data-st="${esc(t.topic)}">🔎 ${esc(t.topic)}</button><span class="small muted">${esc(t.why || "")}</span></div>`).join("")}</div>` : ""}`;
      const discoverFor = (q) => { $$("#stabs button").forEach((x) => x.classList.toggle("active", x.dataset.t === "papers")); papersTab(); $("#smode").value = "discover"; $("#sq").value = q; $("#sgo").click(); };
      $("#msdisc").onclick = () => discoverFor([a.title, a.research_question].filter(Boolean).join(". "));
      $("#msgap").onclick = () => { S.gapTopic = [a.title, a.research_question].filter(Boolean).join("\n"); location.hash = "#gaps"; };
      const md_ = $("#msdoi"); if (md_) md_.onclick = (ev) => busy(ev.currentTarget, async () => { const x = await api(`/api/projects/${S.project.id}/papers/identifier`, { body: { text: a.dois.join("\n") } }); await refreshLibKeys(); toast(`Imported ${x.added.length}${x.failed.length ? `, ${x.failed.length} not found` : ""}`, "ok"); });
      $$("[data-st]").forEach((b) => b.onclick = () => discoverFor(b.dataset.st));
      $$("[data-cl]").forEach((b) => b.onclick = () => busy(b, () => runEvidence(a.claims_needing_sources[+b.dataset.cl].sentence, $("#clres" + b.dataset.cl), (cite) => { navigator.clipboard.writeText(cite).catch(() => { }); toast(`Added — ${cite} copied; paste it after the sentence`, "ok"); }, ["openalex", "europepmc"])));
    } catch (err) { $("#msres").innerHTML = `<div class="notice bad">${esc(err.message)}</div>`; }
    e.target.value = "";
  };
}
function journalsTab() {
  $("#sbody").innerHTML = `<div class="card"><p class="muted small" style="margin-top:0">Describe your manuscript topic. You’ll see which journals published the most on it recently, with quality, open-access status and APC (author publication fee).</p>
    <div class="row"><input type="text" class="grow" id="jq" placeholder="e.g. machine learning for reservoir characterization"><select id="jy" style="width:140px"><option value="3">Last 3 years</option><option value="5" selected>Last 5 years</option><option value="10">Last 10 years</option></select>
    <select id="jsort" style="width:170px"><option value="n">Most on this topic</option><option value="q">Best quality</option><option value="apc">Lowest APC</option></select><button class="btn primary" id="jgo">Find journals</button></div></div><div id="jres"></div>`;
  let rows = [];
  const draw = () => {
    const s = $("#jsort").value, list = [...rows];
    if (s === "q") list.sort((a, b) => (QORD[a.quartile] || 9) - (QORD[b.quartile] || 9) || (b.impact2y || 0) - (a.impact2y || 0));
    if (s === "apc") list.sort((a, b) => (a.apc_usd ?? 1e9) - (b.apc_usd ?? 1e9));
    $("#jres").innerHTML = list.length ? `<div class="card scroll"><table class="lib"><thead><tr><th>Journal</th><th>Articles on topic</th><th>Quality</th><th>IF≈</th><th>h</th><th>Access</th><th>APC (USD)</th><th>Publisher</th></tr></thead><tbody>
      ${list.map((r) => `<tr><td><b>${r.homepage ? `<a href="${esc(r.homepage)}" target="_blank">${esc(r.name)}</a>` : esc(r.name)}</b>${r.categories ? `<div class="small muted">${esc(r.categories.slice(0, 90))}</div>` : ""}</td><td>${r.matching_articles}</td>
        <td>${r.quartile ? `<span class="pill q${r.quartile[1]}">${r.quartile}${r.q_source === "SJR" ? "" : " est."}</span>` : "–"}${r.sjr ? ` <span class="small muted">SJR ${r.sjr}</span>` : ""}</td><td>${r.impact2y ?? "–"}</td><td>${r.h_index ?? "–"}</td>
        <td>${r.oa_journal ? '<span class="pill ok">Open access</span>' : '<span class="pill gray">Subscription/hybrid</span>'} ${r.doaj ? '<span class="pill ok">DOAJ</span>' : ""}</td><td>${r.apc_usd != null ? "$" + r.apc_usd.toLocaleString() : "–"}</td><td class="small">${esc(r.publisher || "")}</td></tr>`).join("")}
      </tbody></table><p class="small muted">Always confirm scope, indexing and fees on the journal’s website. Beware of predatory journals: check DOAJ / Scopus / Web of Science listing.</p></div>` : "";
  };
  const go = () => busy($("#jgo"), async () => { const q = $("#jq").value.trim(); if (!q) return; rows = await api(`/api/journals?q=${encodeURIComponent(q)}&years=${$("#jy").value}`); if (!rows.length) toast("No journals found"); draw(); });
  $("#jgo").onclick = go; $("#jq").onkeydown = (e) => { if (e.key === "Enter") go(); }; $("#jsort").onchange = draw; $("#jq").focus();
}

// ================================================================== LIBRARY
VIEWS.library = async () => {
  main().innerHTML = `
  <div class="page-head"><div><h1>Library</h1><p>Sources for “${esc(S.project.name)}”. Cite with <span class="key">[@key]</span>. Import from DOI/arXiv, BibTeX (Zotero, Mendeley, EndNote export) or PDF.</p></div>
    <div class="row"><button class="btn" id="impId">Import DOI / arXiv</button><button class="btn" id="impBib">Import BibTeX</button>
    <label class="btn">Upload PDF<input type="file" id="impPdf" accept=".pdf" multiple hidden></label>
    <a class="btn" href="/api/projects/${S.project.id}/bibtex">Export BibTeX</a><a class="btn" href="/api/projects/${S.project.id}/ris" title="For EndNote / Mendeley">Export RIS</a><button class="btn" id="lsync">Sync Zotero / Mendeley</button><button class="btn" id="lcheck">Check references</button><button class="btn" id="ldup">Find duplicates</button><button class="btn" id="bibPrev">Bibliography</button></div></div>
  <div id="lpanel"></div>
  <div class="split-wide"><div class="card" id="lcols"></div>
  <div class="card"><div class="row" style="margin-bottom:8px"><input type="text" class="grow" id="lf" placeholder="Filter by title, author, key, tag…"><span class="muted small" id="lcount"></span></div>
    <div id="lbulk" class="toolbar" style="display:none;background:var(--accent-soft);padding:8px;border-radius:8px"></div><div id="ltable"></div></div></div>`;
  let papers = [], cols = [], active = S.libCol || "all";
  const selected = new Set();
  const ruleMatch = (p, r) => {
    const txt = [p.title, p.key, p.tags, p.venue, p.abstract, authorsFull(p.authors)].join(" ").toLowerCase();
    if (r.q && !r.q.toLowerCase().split(/\s+/).every((w) => txt.includes(w))) return false;
    if (r.tag && !(p.tags || "").toLowerCase().split(",").map((t) => t.trim()).includes(r.tag.toLowerCase())) return false;
    if (r.year_from && (p.year || 0) < +r.year_from) return false;
    if (r.year_to && (p.year || 9999) > +r.year_to) return false;
    if (r.type && p.type !== r.type) return false;
    if (r.has_pdf && !p.has_pdf) return false;
    if (r.no_pdf && p.has_pdf) return false;
    if (r.no_doi && p.doi) return false;
    if (r.has_quotes && !p.quotes) return false;
    return true;
  };
  const inActive = (p) => active === "all" ? true : active === "unfiled" ? !p.collections.length :
    (() => { const c = cols.find((x) => x.id === active); return !c ? true : c.rule ? ruleMatch(p, c.rule) : p.collections.includes(c.id); })();
  const load = async () => { [papers, cols] = await Promise.all([refreshLibKeys(), api(`/api/projects/${S.project.id}/collections`)]); drawCols(); draw(); };
  const drawCols = () => {
    const count = (fn) => papers.filter(fn).length;
    const item = (id, label, n, extra = "") => `<div class="list-item ${active === id ? "active" : ""}" data-col="${id}" style="padding:7px 10px;display:flex;justify-content:space-between;gap:6px"><span>${label}</span><span class="small muted">${n}${extra}</span></div>`;
    $("#lcols").innerHTML = `<h3>Collections</h3>${item("all", "All papers", papers.length)}${item("unfiled", "Unfiled", count((p) => !p.collections.length))}
      ${cols.filter((c) => !c.rule).map((c) => item(c.id, "📁 " + esc(c.name), count((p) => p.collections.includes(c.id)))).join("")}
      ${cols.some((c) => c.rule) ? `<div class="small muted" style="margin:10px 0 4px">Smart collections</div>` : ""}
      ${cols.filter((c) => c.rule).map((c) => item(c.id, "✨ " + esc(c.name), count((p) => ruleMatch(p, c.rule)))).join("")}
      <div class="row" style="margin-top:10px"><button class="btn small" id="ncol">+ Folder</button><button class="btn small" id="nsmart">+ Smart</button></div>
      ${active !== "all" && active !== "unfiled" ? `<div class="row" style="margin-top:6px"><button class="btn small ghost" id="ecol">Edit</button><button class="btn small ghost danger" id="dcol">Delete collection</button></div>` : ""}`;
    $$("[data-col]").forEach((el) => el.onclick = () => { active = el.dataset.col; S.libCol = active; selected.clear(); drawCols(); draw(); });
    $("#ncol").onclick = async () => { const name = prompt("Folder name (e.g. Chapter 2, To read, Methods)"); if (!name) return; const r = await api(`/api/projects/${S.project.id}/collections`, { body: { name } }); active = r.id; load(); };
    $("#nsmart").onclick = () => smartDialog(null);
    const ec = $("#ecol"); if (ec) ec.onclick = () => { const c = cols.find((x) => x.id === active); if (c.rule) smartDialog(c); else { const n = prompt("Rename folder", c.name); if (n) api(`/api/collections/${c.id}`, { method: "PUT", body: { name: n } }).then(load); } };
    const dc = $("#dcol"); if (dc) dc.onclick = async () => { if (!confirm("Delete this collection? The papers stay in your library.")) return; await api(`/api/collections/${active}`, { method: "DELETE" }); active = "all"; load(); };
  };
  const smartDialog = (c) => {
    const r = c?.rule || {};
    const box = modal(`<h2>${c ? "Edit" : "New"} smart collection</h2><p class="muted small">Updates itself: every paper matching all filled-in rules is shown.</p>
      <label class="f">Name</label><input type="text" id="sm_name" value="${esc(c?.name || "")}" placeholder="e.g. Recent cement studies with PDF">
      <label class="f">Words (all must appear in title/abstract/tags)</label><input type="text" id="sm_q" value="${esc(r.q || "")}">
      <div class="grid g3" style="gap:0 12px"><div><label class="f">Tag</label><input type="text" id="sm_tag" value="${esc(r.tag || "")}"></div>
      <div><label class="f">Year from</label><input type="number" id="sm_yf" value="${esc(r.year_from || "")}"></div><div><label class="f">Year to</label><input type="number" id="sm_yt" value="${esc(r.year_to || "")}"></div></div>
      <label class="f">Type</label><select id="sm_type"><option value="">Any</option>${["article", "conference", "book", "chapter", "thesis", "report", "preprint", "webpage"].map((t) => `<option ${r.type === t ? "selected" : ""}>${t}</option>`).join("")}</select>
      <div class="checks" style="margin-top:10px"><label><input type="checkbox" id="sm_pdf" ${r.has_pdf ? "checked" : ""}>Has PDF</label><label><input type="checkbox" id="sm_nopdf" ${r.no_pdf ? "checked" : ""}>Missing PDF</label><label><input type="checkbox" id="sm_nodoi" ${r.no_doi ? "checked" : ""}>Missing DOI</label><label><input type="checkbox" id="sm_q2" ${r.has_quotes ? "checked" : ""}>Has my quotes/notes</label></div>
      <div class="modal-foot"><button class="btn" onclick="closeModal()">Cancel</button><button class="btn primary" id="sm_ok">Save</button></div>`);
    $("#sm_ok", box).onclick = async () => {
      const rule = { q: $("#sm_q").value.trim(), tag: $("#sm_tag").value.trim(), year_from: $("#sm_yf").value, year_to: $("#sm_yt").value, type: $("#sm_type").value,
        has_pdf: $("#sm_pdf").checked, no_pdf: $("#sm_nopdf").checked, no_doi: $("#sm_nodoi").checked, has_quotes: $("#sm_q2").checked };
      const name = $("#sm_name").value.trim() || "Smart collection";
      if (c) await api(`/api/collections/${c.id}`, { method: "PUT", body: { name, rule } }); else active = (await api(`/api/projects/${S.project.id}/collections`, { body: { name, rule } })).id;
      closeModal(); load();
    };
  };
  const drawBulk = () => {
    const bar = $("#lbulk"); bar.style.display = selected.size ? "flex" : "none"; if (!selected.size) return;
    const manual = cols.filter((c) => !c.rule), cur = cols.find((c) => c.id === active && !c.rule);
    bar.innerHTML = `<b class="small">${selected.size} selected</b><select id="bk_col" style="width:200px"><option value="">Add to folder…</option>${manual.map((c) => `<option value="${c.id}">📁 ${esc(c.name)}</option>`).join("")}<option value="__new">+ New folder…</option></select>
      ${cur ? `<button class="btn small" id="bk_rm">Remove from “${esc(cur.name)}”</button>` : ""}<button class="btn small" id="bk_tag">Add tag</button><button class="btn small" id="bk_bib">Export .bib</button><button class="btn small danger" id="bk_del">Delete</button><button class="btn small ghost" id="bk_clr">Clear</button>`;
    const ids = [...selected];
    $("#bk_col").onchange = async (e) => {
      let cid = e.target.value; if (!cid) return;
      if (cid === "__new") { const name = prompt("Folder name"); if (!name) return; cid = (await api(`/api/projects/${S.project.id}/collections`, { body: { name } })).id; }
      await api(`/api/collections/${cid}/papers`, { body: { paper_ids: ids } }); toast(`${ids.length} added to folder`, "ok"); load();
    };
    if (cur) $("#bk_rm").onclick = async () => { await api(`/api/collections/${cur.id}/papers`, { body: { paper_ids: ids, remove: true } }); selected.clear(); load(); };
    $("#bk_tag").onclick = async () => { const t = prompt("Tag to add"); if (!t) return; await api("/api/papers/bulk", { body: { ids, action: "tag", value: t } }); load(); };
    $("#bk_bib").onclick = async () => { const r = await fetch(`/api/projects/${S.project.id}/bibtex/selected`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ ids }) }); download(await r.blob(), "selection.bib"); };
    $("#bk_del").onclick = async () => { if (!confirm(`Delete ${ids.length} papers from the library? (A backup is made first.)`)) return; await api("/api/papers/bulk", { body: { ids, action: "delete" } }); selected.clear(); load(); };
    $("#bk_clr").onclick = () => { selected.clear(); draw(); };
  };
  const draw = () => {
    const f = $("#lf").value.toLowerCase();
    const list = papers.filter(inActive).filter((p) => !f || [p.title, p.key, p.tags, p.venue, authorsFull(p.authors)].join(" ").toLowerCase().includes(f));
    $("#lcount").textContent = `${list.length} of ${papers.length}`;
    $("#ltable").innerHTML = list.length ? `<table class="lib"><thead><tr><th style="width:28px"><input type="checkbox" id="lall" ${list.every((p) => selected.has(p.id)) ? "checked" : ""}></th><th>Key</th><th>Title</th><th>Authors</th><th>Year</th><th>Venue</th><th>Tags</th></tr></thead><tbody>
      ${list.map((p) => `<tr data-id="${p.id}"><td><input type="checkbox" data-sel="${p.id}" ${selected.has(p.id) ? "checked" : ""}></td><td class="key">@${esc(p.key)}</td><td>${esc(p.title)} ${p.has_pdf ? '<span class="pill ok" title="PDF attached">PDF</span>' : p.has_fulltext ? '<span class="pill ok">full text</span>' : ""}${p.quotes ? ` <span class="pill gray" title="Your quotes & notes">✎ ${p.quotes}</span>` : ""}</td><td>${esc(authorsShort(p.authors))}</td><td>${p.year || ""}</td><td><i>${esc(p.venue || "")}</i></td><td>${esc(p.tags || "")}</td></tr>`).join("")}
      </tbody></table>` : `<div class="empty">${papers.length ? "No papers in this view." : "Library is empty. Use Literature Search, Deep Research, or the import buttons above."}</div>`;
    $$("#ltable tr[data-id]").forEach((tr) => tr.onclick = (e) => { if (e.target.matches("input")) return; paperDialog(tr.dataset.id, load); });
    $$("[data-sel]").forEach((c) => c.onchange = () => { c.checked ? selected.add(c.dataset.sel) : selected.delete(c.dataset.sel); drawBulk(); });
    const all = $("#lall"); if (all) all.onchange = () => { list.forEach((p) => all.checked ? selected.add(p.id) : selected.delete(p.id)); draw(); };
    drawBulk();
  };
  $("#lf").oninput = draw;
  $("#ldup").onclick = (e) => busy(e.currentTarget, () => duplicatesDialog(load));
  $("#lsync").onclick = () => syncDialog(load);
  $("#lcheck").onclick = (e) => busy(e.currentTarget, () => refCheck($("#lpanel"), ""));
  $("#impId").onclick = () => {
    const box = modal(`<h2>Import by DOI or arXiv ID</h2><p class="muted">One per line, e.g. <code>10.1038/nature14539</code>, <code>https://doi.org/…</code>, <code>2303.08774</code>, or arXiv URLs.</p>
      <textarea id="ids" rows="8" class="mono"></textarea><div class="modal-foot"><button class="btn" onclick="closeModal()">Cancel</button><button class="btn primary" id="go">Import</button></div>`);
    $("#go", box).onclick = (e) => busy(e.currentTarget, async () => {
      const r = await api(`/api/projects/${S.project.id}/papers/identifier`, { body: { text: $("#ids").value } });
      closeModal(); toast(`Imported ${r.added.length}${r.failed.length ? `, failed: ${r.failed.join(", ")}` : ""}`, r.failed.length ? "bad" : "ok"); load();
    });
  };
  $("#impBib").onclick = () => {
    const box = modal(`<h2>Import BibTeX</h2><p class="muted">Paste BibTeX or choose a .bib file. Existing citation keys are kept.</p>
      <input type="file" id="bibf" accept=".bib,.txt"><textarea id="bibt" rows="10" class="mono" style="margin-top:8px"></textarea>
      <div class="modal-foot"><button class="btn" onclick="closeModal()">Cancel</button><button class="btn primary" id="go">Import</button></div>`);
    $("#bibf", box).onchange = async (e) => { $("#bibt").value = await e.target.files[0].text(); };
    $("#go", box).onclick = (e) => busy(e.currentTarget, async () => {
      const r = await api(`/api/projects/${S.project.id}/papers/bibtex`, { body: { text: $("#bibt").value } }); closeModal(); toast(`Imported ${r.added.length} entries`, "ok"); load();
    });
  };
  $("#impPdf").onchange = async (e) => {
    for (const file of e.target.files) {
      toast(`Reading ${file.name}…`);
      const fd = new FormData(); fd.append("file", file);
      try { const p = await api(`/api/projects/${S.project.id}/papers/pdf`, { body: fd }); toast(`Added @${p.key}`, "ok"); } catch (err) { toast(`${file.name}: ${err.message}`, "bad"); }
    }
    e.target.value = ""; load();
  };
  $("#bibPrev").onclick = async () => {
    const box = modal(`<h2>Bibliography</h2><div class="row"><select id="bst" style="width:220px">${Object.entries(S.meta.styles).map(([k, v]) => `<option value="${k}" ${k === S.settings.default_citation_style ? "selected" : ""}>${v}</option>`).join("")}</select><button class="btn small" id="bcopy">Copy</button></div><div id="bl" class="prose" style="font-size:14px;margin-top:12px"></div>`, true);
    const show = async () => { const r = await api(`/api/projects/${S.project.id}/bibliography?style=${$("#bst").value}`); $("#bl").innerHTML = r.entries.map((x) => `<p style="padding-left:2em;text-indent:-2em;margin:0 0 8px">${md(x).replace(/^<p>|<\/p>\s*$/g, "")}</p>`).join(""); };
    $("#bst", box).onchange = show; $("#bcopy", box).onclick = () => { navigator.clipboard.writeText($("#bl").innerText); toast("Copied"); }; show();
  };
  load();
};
async function paperDialog(id, onChange) {
  const p = await api(`/api/papers/${id}`);
  const authors = (p.authors || []).map((a) => a.literal || [a.family, a.given].filter(Boolean).join(", ")).join("; ");
  const types = ["article", "conference", "book", "chapter", "thesis", "report", "preprint", "webpage"];
  const box = modal(`<h2 style="font-size:18px">${esc(p.title)}</h2>
    <div class="row small muted" style="margin-bottom:6px"><span class="key">@${esc(p.key)}</span><button class="btn small ghost" id="cpk">copy [@${esc(p.key)}]</button>${p.url ? `<a href="${esc(p.url)}" target="_blank">open source</a>` : ""}${p.pdf_url ? ` · <a href="${esc(p.pdf_url)}" target="_blank">PDF</a>` : ""}</div>
    <div class="grid g2" style="gap:0 12px">
      <div><label class="f">Citation key</label><input type="text" id="e_key" value="${esc(p.key)}"></div>
      <div><label class="f">Type</label><select id="e_type">${types.map((t) => `<option ${t === p.type ? "selected" : ""}>${t}</option>`).join("")}</select></div>
    </div>
    <label class="f">Title</label><input type="text" id="e_title" value="${esc(p.title)}">
    <label class="f">Authors <small>Family, Given; Family, Given</small></label><input type="text" id="e_authors" value="${esc(authors)}">
    <div class="grid g3" style="gap:0 12px">
      <div><label class="f">Year</label><input type="text" id="e_year" value="${esc(p.year || "")}"></div>
      <div><label class="f">Volume</label><input type="text" id="e_volume" value="${esc(p.volume || "")}"></div>
      <div><label class="f">Issue</label><input type="text" id="e_issue" value="${esc(p.issue || "")}"></div>
      <div><label class="f">Pages</label><input type="text" id="e_pages" value="${esc(p.pages || "")}"></div>
      <div><label class="f">DOI</label><input type="text" id="e_doi" value="${esc(p.doi || "")}"></div>
      <div><label class="f">Publisher</label><input type="text" id="e_publisher" value="${esc(p.publisher || "")}"></div>
    </div>
    <label class="f">Journal / venue / site</label><input type="text" id="e_venue" value="${esc(p.venue || "")}">
    <label class="f">URL</label><input type="text" id="e_url" value="${esc(p.url || "")}">
    <label class="f">Tags</label><input type="text" id="e_tags" value="${esc(p.tags || "")}">
    <label class="f">Abstract</label><textarea id="e_abstract" rows="4">${esc(p.abstract || "")}</textarea>
    <label class="f">Notes <small>(used by the AI when writing)</small></label><textarea id="e_notes" rows="6">${esc(p.notes || "")}</textarea>
    <div class="card" style="margin:12px 0 0;padding:10px 12px"><div class="row" style="justify-content:space-between"><div><b>PDF</b> <span class="small muted">${p.has_pdf ? "attached" : "not attached"}${p.fulltext ? ` · full text ${p.fulltext.length.toLocaleString()} characters` : ""}${p.quotes ? ` · ${p.quotes} quotes/notes` : ""}</span></div>
      <div class="row">${p.has_pdf ? `<button class="btn small primary" id="pread">Open reader</button>` : `<button class="btn small" id="pfetch">Find open-access PDF</button>`}<label class="btn small">${p.has_pdf ? "Replace" : "Attach"} PDF<input type="file" id="pattach" accept=".pdf" hidden></label></div></div></div>
    <div class="modal-foot" style="justify-content:space-between"><div class="row"><button class="btn danger" id="pdel">Delete</button>
      <button class="btn" id="psum">AI reading notes</button>${p.doi || p.openalex_id ? `<button class="btn ghost" data-psb="references">References</button><button class="btn ghost" data-psb="citing">Cited by</button><button class="btn ghost" data-psb="related">Related</button>` : ""}${!p.fulltext && (p.pdf_url || p.url) ? `<button class="btn" id="pft">Fetch full text</button>` : ""}</div>
      <div class="row"><button class="btn" onclick="closeModal()">Close</button><button class="btn primary" id="psave">Save</button></div></div>`, true);
  $("#cpk", box).onclick = () => { navigator.clipboard.writeText(`[@${p.key}]`); toast("Copied"); };
  $$("[data-psb]", box).forEach((b) => b.onclick = () => { S.snowball = { ident: p.openalex_id || p.doi, mode: b.dataset.psb, title: p.title }; closeModal(); if (location.hash === "#search") route(); else location.hash = "#search"; });
  $("#psave", box).onclick = (e) => busy(e.currentTarget, async () => {
    const val = (k) => $("#e_" + k).value.trim();
    const body = { key: val("key").replace(/[^\w:\-]/g, ""), type: val("type"), title: val("title"), year: parseInt(val("year")) || null, volume: val("volume"), issue: val("issue"), pages: val("pages"), doi: val("doi"), publisher: val("publisher"), venue: val("venue"), url: val("url"), tags: val("tags"), abstract: val("abstract"), notes: $("#e_notes").value,
      authors: val("authors").split(";").map((s) => s.trim()).filter(Boolean).map((s) => s.includes(",") ? { family: s.split(",")[0].trim(), given: s.split(",").slice(1).join(",").trim() } : { literal: s, family: s, given: "" }) };
    await api(`/api/papers/${id}`, { method: "PUT", body }); closeModal(); toast("Saved", "ok"); onChange && onChange();
  });
  $("#pdel", box).onclick = async () => { if (!confirm("Remove this paper from the library?")) return; await api(`/api/papers/${id}`, { method: "DELETE" }); closeModal(); onChange && onChange(); };
  $("#psum", box).onclick = (e) => busy(e.currentTarget, async () => {
    const ta = $("#e_notes"); const before = ta.value ? ta.value + "\n\n" : "";
    await streamText(`/api/papers/${id}/summarize`, {}, (full) => { ta.value = before + full; ta.scrollTop = ta.scrollHeight; });
    toast("Notes generated — click Save to keep them");
  });
  const rd = $("#pread", box); if (rd) rd.onclick = () => readerDialog(p);
  const pf = $("#pfetch", box); if (pf) pf.onclick = (e) => busy(e.currentTarget, async () => { await api(`/api/papers/${id}/pdf/fetch`, { method: "POST" }); toast("Open-access PDF attached", "ok"); onChange && onChange(); paperDialog(id, onChange); });
  $("#pattach", box).onchange = async (e) => { const f = e.target.files[0]; if (!f) return; const fd = new FormData(); fd.append("file", f); try { await api(`/api/papers/${id}/pdf`, { body: fd }); toast("PDF attached", "ok"); onChange && onChange(); paperDialog(id, onChange); } catch (err) { toast(err.message, "bad"); } };
  const ft = $("#pft", box); if (ft) ft.onclick = (e) => busy(e.currentTarget, async () => { const r = await api(`/api/papers/${id}/fulltext`, { method: "POST" }); toast(`Stored ${r.chars.toLocaleString()} characters`, "ok"); });
}

// ================================================================== PDF READER with quotes & notes
async function readerDialog(p) {
  const box = modal(`<div class="row" style="justify-content:space-between;margin-bottom:8px"><div><b>${esc(p.title)}</b> <span class="key">@${esc(p.key)}</span></div><button class="btn small" onclick="closeModal()">Close</button></div>
    <div style="display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:12px;height:76vh">
      <iframe src="/api/papers/${p.id}/pdf" style="width:100%;height:100%;border:1px solid var(--line);border-radius:8px"></iframe>
      <div style="display:flex;flex-direction:column;min-height:0">
        <div class="small muted">Copy a passage from the PDF, paste it below with its page number. Saved quotes are searchable and insert with the right citation.</div>
        <label class="f">Page</label><input type="text" id="rq_page" style="width:90px">
        <label class="f">Quote</label><textarea id="rq_quote" rows="4"></textarea>
        <label class="f">My note</label><textarea id="rq_note" rows="2"></textarea>
        <div class="row" style="margin-top:6px"><button class="btn small primary" id="rq_add">Save quote</button></div>
        <div id="rq_list" style="overflow:auto;margin-top:10px;flex:1"></div></div></div>`, true);
  box.style.width = "min(1300px, 100%)";
  const cite = (q) => `[@${p.key}${q.page ? ", p. " + q.page : ""}]`;
  const load = async () => {
    const qs = await api(`/api/papers/${p.id}/quotes`);
    $("#rq_list").innerHTML = qs.map((q) => `<div class="result" style="padding:8px 2px">${q.quote ? `<div style="font-family:var(--serif);font-size:14px">“${esc(q.quote)}”</div>` : ""}${q.comment ? `<div class="small">✎ ${esc(q.comment)}</div>` : ""}
      <div class="row small" style="margin-top:4px"><span class="muted">${q.page ? "p. " + esc(q.page) : ""}</span><button class="btn small ghost" data-qc="${q.id}">Copy quote + citation</button><button class="btn small ghost" data-qk="${q.id}">Copy citation</button><button class="btn small ghost danger" data-qd="${q.id}">✕</button></div></div>`).join("") || `<div class="small muted">No quotes yet.</div>`;
    const byId = Object.fromEntries(qs.map((q) => [q.id, q]));
    $$("[data-qc]").forEach((b) => b.onclick = () => { const q = byId[b.dataset.qc]; navigator.clipboard.writeText(`“${q.quote}” ${cite(q)}`); toast("Copied: paste it into your document"); });
    $$("[data-qk]").forEach((b) => b.onclick = () => { navigator.clipboard.writeText(cite(byId[b.dataset.qk])); toast("Citation copied"); });
    $$("[data-qd]").forEach((b) => b.onclick = async () => { if (!confirm("Delete this quote?")) return; await api(`/api/quotes/${b.dataset.qd}`, { method: "DELETE" }); load(); });
  };
  $("#rq_add", box).onclick = async () => {
    if (!$("#rq_quote").value.trim() && !$("#rq_note").value.trim()) return toast("Paste a quote or write a note");
    await api(`/api/papers/${p.id}/quotes`, { body: { page: $("#rq_page").value.trim(), quote: $("#rq_quote").value.trim(), comment: $("#rq_note").value.trim() } });
    $("#rq_quote").value = ""; $("#rq_note").value = ""; load();
  };
  load();
}

// ================================================================== DUPLICATES
async function duplicatesDialog(onDone) {
  const groups = await api(`/api/projects/${S.project.id}/duplicates`);
  const score = (p) => (p.doi ? 4 : 0) + (p.has_pdf ? 2 : 0) + (p.quotes ? 2 : 0) + (p.venue ? 1 : 0) + (p.authors?.length ? 1 : 0);
  const box = modal(`<h2>Duplicates</h2>${groups.length ? `<p class="muted small" style="margin-top:0">${groups.length} group(s) found (same DOI or nearly identical title and year). Choose the record to keep. Merging fills its missing details from the others, joins tags/notes/quotes/folders/PDF, and <b>updates the citation keys in all your documents</b>. A backup is made first.</p>` : `<div class="empty">✓ No duplicates in this library.</div>`}
    <div id="dg">${groups.map((g, gi) => { const best = g.reduce((a, b) => score(b) > score(a) ? b : a); return `<div class="card" data-g="${gi}"><b class="small">Group ${gi + 1}</b>${g.map((p) => `<label class="list-item" style="display:flex;gap:10px;margin:6px 0"><input type="radio" name="m${gi}" value="${p.id}" ${p.id === best.id ? "checked" : ""}><div><div class="t">${esc(p.title)}</div><div class="small muted"><span class="key">@${esc(p.key)}</span> · ${esc(authorsShort(p.authors))} ${p.year || ""} · ${esc(p.venue || "")} ${p.doi ? "· DOI" : "· no DOI"} ${p.has_pdf ? "· PDF" : ""} ${p.quotes ? `· ${p.quotes} quotes` : ""}</div></div></label>`).join("")}<button class="btn small primary" data-merge="${gi}">Merge into selected</button></div>`; }).join("")}</div>
    <div class="modal-foot">${groups.length > 1 ? `<button class="btn primary" id="mall">Merge all groups</button>` : ""}<button class="btn" onclick="closeModal()">Close</button></div>`, true);
  const mergeGroup = async (gi) => {
    const master = $(`input[name=m${gi}]:checked`).value, others = groups[gi].map((p) => p.id).filter((x) => x !== master);
    const r = await api("/api/papers/merge", { body: { master_id: master, other_ids: others } });
    const card = $(`[data-g="${gi}"]`); card.innerHTML = `<div class="small" style="color:var(--ok)">✓ Merged ${r.merged} into <span class="key">@${esc(r.key)}</span>${r.documents_updated ? ` · ${r.documents_updated} document(s) updated` : ""}</div>`;
  };
  $$("[data-merge]", box).forEach((b) => b.onclick = () => busy(b, async () => { await mergeGroup(+b.dataset.merge); onDone && onDone(); }));
  const ma = $("#mall", box); if (ma) ma.onclick = () => busy(ma, async () => { for (let gi = 0; gi < groups.length; gi++) if ($(`input[name=m${gi}]`)) await mergeGroup(gi); onDone && onDone(); toast("All duplicates merged", "ok"); });
}

// ================================================================== citation picker
async function citePicker(onPick) {
  const papers = await refreshLibKeys();
  const chosen = new Set();
  const box = modal(`<h2>Insert citation</h2><input type="text" id="cf" placeholder="Filter…"><div id="cl" class="scroll" style="max-height:50vh;margin-top:10px"></div>
    <label class="f">Locator <small>(optional, e.g. p. 12)</small></label><input type="text" id="cloc" style="width:200px">
    <div class="modal-foot"><button class="btn" onclick="closeModal()">Cancel</button><button class="btn primary" id="cins">Insert</button></div>`, true);
  const draw = () => {
    const f = $("#cf").value.toLowerCase();
    $("#cl").innerHTML = papers.filter((p) => !f || (p.title + p.key + authorsFull(p.authors)).toLowerCase().includes(f)).map((p) =>
      `<label class="list-item ${chosen.has(p.key) ? "active" : ""}" style="display:flex;gap:10px"><input type="checkbox" value="${esc(p.key)}" ${chosen.has(p.key) ? "checked" : ""}><div><div class="t">${esc(p.title)}</div><div class="small muted">${esc(authorsShort(p.authors))} (${p.year || "n.d."}) · <span class="key">@${esc(p.key)}</span></div></div></label>`).join("") || `<div class="empty">No papers in library.</div>`;
    $$("#cl input").forEach((c) => c.onchange = () => { c.checked ? chosen.add(c.value) : chosen.delete(c.value); c.closest(".list-item").classList.toggle("active", c.checked); });
  };
  $("#cf", box).oninput = draw; draw(); $("#cf").focus();
  $("#cins", box).onclick = () => {
    if (!chosen.size) return closeModal();
    const loc = $("#cloc").value.trim(); const keys = [...chosen];
    onPick(`[${keys.map((k, i) => "@" + k + (loc && i === keys.length - 1 ? ", " + loc : "")).join("; ")}]`); closeModal();
  };
}
function insertAtCursor(ta, text) {
  const s = ta.selectionStart, e = ta.selectionEnd; const pre = ta.value.slice(0, s);
  const sp = pre && !/\s$/.test(pre) ? " " : "";
  ta.value = pre + sp + text + ta.value.slice(e); ta.selectionStart = ta.selectionEnd = s + sp.length + text.length; ta.focus(); ta.dispatchEvent(new Event("input"));
}

// ================================================================== EXPORT
function exportDialog({ markdown, meta = {}, template, style, filenameHint }) {
  const st = S.settings;
  template = template || st.default_template; style = style || st.default_citation_style;
  const box = modal(`<h2>Export to Word (.docx)</h2>
    <div class="grid g2" style="gap:0 14px">
      <div><label class="f">Template / format</label><select id="x_tpl">${Object.entries(S.meta.templates).map(([k, v]) => `<option value="${k}" ${k === template ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></div>
      <div><label class="f">Citation style</label><select id="x_style">${Object.entries(S.meta.styles).map(([k, v]) => `<option value="${k}" ${k === style ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></div>
    </div>
    <label class="f">Title <small>(blank = first # heading)</small></label><input type="text" id="x_title" value="${esc(meta.title || "")}">
    <div class="grid g2" style="gap:0 14px">
      <div><label class="f">Author(s)</label><input type="text" id="x_authors" value="${esc(meta.authors || localStorage.getItem("rt_author") || "")}"></div>
      <div><label class="f">Affiliation / institution</label><input type="text" id="x_aff" value="${esc(meta.affiliation || localStorage.getItem("rt_aff") || "")}"></div>
      <div><label class="f">Date</label><input type="text" id="x_date" value="${esc(meta.date || new Date().toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" }))}"></div>
      <div><label class="f">Keywords</label><input type="text" id="x_kw" value="${esc(Array.isArray(meta.keywords) ? meta.keywords.join(", ") : meta.keywords || "")}"></div>
    </div>
    <label class="f">Abstract <small>(blank = use the “Abstract” section in the text, if any)</small></label><textarea id="x_abs" rows="3">${esc(meta.abstract || "")}</textarea>
    <div id="x_thesis" class="grid g2" style="gap:0 14px">
      <div><label class="f">Degree line <small>thesis</small></label><input type="text" id="x_degree" placeholder="SKRIPSI / TESIS / Diajukan untuk memenuhi…" value="${esc(meta.degree || "")}"></div>
      <div><label class="f">Student ID (NIM) <small>thesis</small></label><input type="text" id="x_nim" value="${esc(meta.student_id || "")}"></div>
      <div><label class="f">Department / Program studi</label><input type="text" id="x_dept" value="${esc(meta.department || "")}"></div>
      <div><label class="f">City</label><input type="text" id="x_city" value="${esc(meta.city || "")}"></div>
      <div><label class="f">Course <small>APA student paper</small></label><input type="text" id="x_course" value="${esc(meta.course || "")}"></div>
      <div><label class="f">Instructor <small>APA student paper</small></label><input type="text" id="x_instr" value="${esc(meta.instructor || "")}"></div>
    </div>
    <div class="grid g3" style="gap:0 14px">
      <div><label class="f">Labels language</label><select id="x_lang"><option value="en">English</option><option value="id" ${meta.lang === "id" || S.settings.default_language === "Indonesian" ? "selected" : ""}>Bahasa Indonesia</option></select></div>
      <div><label class="f">Paper size</label><select id="x_page"><option value="">Template default</option><option value="a4">A4</option><option value="letter">US Letter</option></select></div>
      <div><label class="f">Heading numbering</label><select id="x_num"><option value="__">Template default</option><option value="">None</option><option value="decimal">1. / 1.1</option><option value="ieee">I. / A.</option><option value="chapter">BAB I / 1.1</option></select></div>
      <div><label class="f">Font</label><input type="text" id="x_font" placeholder="Template default"></div>
      <div><label class="f">Font size</label><input type="number" id="x_size" placeholder="default"></div>
      <div><label class="f">Line spacing</label><input type="number" id="x_spacing" step="0.05" placeholder="default"></div>
    </div>
    <div class="checks" style="margin-top:12px"><label><input type="checkbox" id="x_toc" checked>Table of contents (thesis/report)</label><label><input type="checkbox" id="x_uncited">Include uncited library sources in references</label></div>
    <div id="x_msg"></div>
    <div class="modal-foot"><button class="btn" onclick="closeModal()">Cancel</button><button class="btn primary" id="x_go">Download .docx</button></div>`, true);
  $("#x_go", box).onclick = (e) => busy(e.currentTarget, async () => {
    const v = (id) => $("#" + id).value.trim();
    localStorage.setItem("rt_author", v("x_authors")); localStorage.setItem("rt_aff", v("x_aff"));
    const options = { lang: v("x_lang"), toc: $("#x_toc").checked, include_uncited: $("#x_uncited").checked };
    if (v("x_page")) options.page = v("x_page"); if ($("#x_num").value !== "__") options.numbering = $("#x_num").value || null;
    if (v("x_font")) options.font = v("x_font"); if (v("x_size")) options.size = +v("x_size"); if (v("x_spacing")) options.spacing = +v("x_spacing");
    const body = { project_id: S.project.id, markdown, template: v("x_tpl"), style: v("x_style"), options,
      meta: { title: v("x_title"), authors: v("x_authors"), affiliation: v("x_aff"), date: v("x_date"), keywords: v("x_kw"), abstract: v("x_abs"),
        degree: v("x_degree"), student_id: v("x_nim"), department: v("x_dept"), city: v("x_city"), course: v("x_course"), instructor: v("x_instr") } };
    const r = await fetch("/api/export/docx", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const missing = decodeURIComponent(r.headers.get("X-Missing") || "");
    const title = body.meta.title || (markdown.match(/^#\s+(.+)$/m) || [, filenameHint || "document"])[1];
    download(await r.blob(), title.replace(/[^\w\- ]+/g, "").slice(0, 80).trim() + ".docx");
    $("#x_msg").innerHTML = `<div class="notice ${missing ? "bad" : "ok"}" style="margin-top:12px">Exported with ${r.headers.get("X-Cited")} cited sources.${missing ? ` Citation keys not found in library (shown as “?key”): <b>${esc(missing)}</b>` : ""}</div>`;
  });
}

// ================================================================== REFERENCE CHECKER (no AI)
const RSTAT = { error: ["Problem", "bad"], fix: ["Fixable", "warn"], warn: ["Check", "warn"], info: ["Info", "gray"], ok: ["Verified", "ok"] };
async function refCheck(panel, text) {
  panel.innerHTML = `<div class="card empty">Checking every reference against Crossref and OpenAlex…</div>`;
  const r = await api("/api/refcheck", { body: { project_id: S.project.id, text: text || "" } });
  const fixable = r.results.filter((x) => Object.keys(x.fix || {}).length);
  const n = (st) => r.results.filter((x) => x.status === st).length;
  panel.innerHTML = `<div class="card"><div class="row" style="justify-content:space-between"><b>Reference check: ${r.checked} references</b><div class="row">${fixable.length ? `<button class="btn small primary" id="rfall">Apply all ${fixable.length} fixes</button>` : ""}<button class="btn small ghost" onclick="this.closest('.card').remove()">✕</button></div></div>
    <div style="margin:8px 0"><span class="metric"><b style="color:var(--bad)">${n("error")}</b><span>problems</span></span><span class="metric"><b style="color:var(--warn)">${n("fix") + n("warn")}</b><span>to fix / check</span></span><span class="metric"><b style="color:var(--ok)">${n("ok")}</b><span>verified</span></span></div>
    ${r.missing_keys.length ? `<div class="notice bad">Cited but not in your library: ${r.missing_keys.map((k) => `<span class="key">@${esc(k)}</span>`).join(", ")}</div>` : ""}
    ${r.results.filter((x) => x.status !== "ok" || x.issues.length).map((x) => `<div class="result"><div class="row" style="gap:8px"><span class="pill ${RSTAT[x.status][1]}">${RSTAT[x.status][0]}</span><span class="key">@${esc(x.key)}</span><span class="small">${esc((x.title || "").slice(0, 110))}</span></div>
      <ul class="small" style="margin:4px 0">${x.issues.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>${Object.keys(x.fix || {}).length ? `<button class="btn small" data-rf="${esc(x.id)}">Apply fix (${Object.keys(x.fix).join(", ")})</button>` : ""}</div>`).join("") || `<div class="small" style="color:var(--ok)">✓ All references verified</div>`}</div>`;
  const apply = async (x) => { await api(`/api/papers/${x.id}`, { method: "PUT", body: x.fix }); };
  $$("[data-rf]", panel).forEach((b) => b.onclick = () => busy(b, async () => { await apply(r.results.find((x) => x.id === b.dataset.rf)); b.textContent = "✓ Fixed"; b.disabled = true; }));
  const all = $("#rfall", panel); if (all) all.onclick = () => busy(all, async () => { for (const x of fixable) await apply(x); all.textContent = "✓ All fixed"; all.disabled = true; toast(`${fixable.length} references corrected`, "ok"); });
}

// ================================================================== JOURNAL FIT
function journalFitDialog(markdown) {
  let picked = null;
  const box = modal(`<h2>Journal fit check</h2><p class="muted small" style="margin-top:0">Checks your manuscript against the journal: word, abstract, keyword and reference limits; required sections; journal metrics; scope fit; and the journal's own recent papers on your topic (worth citing).</p>
    <label class="f">Target journal</label><div class="row"><input type="text" class="grow" id="jfq" placeholder="Type the journal name…"><button class="btn" id="jfs">Search</button></div><div id="jfl"></div>
    <label class="f">Author guidelines <small>(optional but recommended: paste the journal's "Guide for Authors" text once; AFRA remembers it)</small></label><textarea id="jfg" rows="5" placeholder="Paste the guidelines text here…"></textarea>
    <div class="checks" style="margin-top:8px"><label><input type="checkbox" id="jfai" checked>Include AI scope-fit opinion (1 small fast-model call)</label></div>
    <div class="modal-foot"><button class="btn" onclick="closeModal()">Close</button><button class="btn primary" id="jfgo" disabled>Check fit</button></div><div id="jfr"></div>`, true);
  const search = () => busy($("#jfs"), async () => {
    const q = $("#jfq").value.trim(); if (!q) return;
    const list = await api(`/api/journal-search?q=${encodeURIComponent(q)}`);
    $("#jfl").innerHTML = list.map((j, i) => `<label class="list-item" style="display:flex;gap:8px;margin:6px 0"><input type="radio" name="jfp" value="${i}"><div><b>${esc(j.name)}</b> <span class="small muted">${esc(j.publisher || "")} · ISSN ${esc(j.issn || "–")} · ${(j.works || 0).toLocaleString()} articles</span></div></label>`).join("") || `<div class="muted small">No journal found.</div>`;
    $$("[name=jfp]").forEach((r) => r.onchange = () => { picked = list[+r.value]; $("#jfgo").disabled = false; });
  });
  $("#jfs", box).onclick = search; $("#jfq", box).onkeydown = (e) => { if (e.key === "Enter") search(); };
  $("#jfgo", box).onclick = (e) => busy(e.currentTarget, async () => {
    const r = await api("/api/journal-fit", { body: { source_id: picked.id, markdown, guidelines: $("#jfg").value, ai: $("#jfai").checked } });
    const j = r.journal, ok = (v) => v === true ? "✅" : v === false ? "❌" : "–";
    $("#jfr").innerHTML = `<div class="card" style="margin-top:12px"><h3>${esc(j.name)}</h3><div>${qBadges({ quality: j })} ${j.oa_journal ? '<span class="pill ok">Open access</span>' : ""} ${j.apc_usd != null ? `<span class="pill gray">APC $${j.apc_usd.toLocaleString()}</span>` : ""} <span class="small muted">${esc(j.publisher || "")} · topics: ${j.topics.map(esc).join(", ")}</span></div>
      ${r.fit ? `<div class="notice ${r.fit.fit === "high" ? "ok" : r.fit.fit === "low" ? "bad" : "info"}" style="margin-top:10px"><b>Scope fit: ${esc(r.fit.fit)}</b><ul class="small">${(r.fit.reasons || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>${(r.fit.suggestions || []).length ? `<b class="small">To improve your chances</b><ul class="small">${r.fit.suggestions.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}</div>` : ""}
      <h3 style="margin-top:12px">Requirements</h3>${Object.keys(r.requirements).length ? "" : `<div class="small muted">No guidelines pasted yet for this journal: limits show “not stated”. Paste the Guide for Authors above and check again.</div>`}
      <table class="lib"><thead><tr><th>Item</th><th>Your manuscript</th><th>Journal</th><th></th></tr></thead><tbody>${r.checks.map((c) => `<tr><td>${esc(c.item)}</td><td>${esc(c.yours)}</td><td>${esc(c.required)}</td><td>${ok(c.ok)}</td></tr>`).join("")}</tbody></table>
      ${r.requirements.reference_style ? `<p class="small">Reference style: <b>${esc(r.requirements.reference_style)}</b></p>` : ""}${(r.requirements.other_requirements || []).filter(Boolean).length ? `<b class="small">Other requirements</b><ul class="small">${r.requirements.other_requirements.filter(Boolean).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      <h3 style="margin-top:12px">This journal's recent papers on your topic (${r.matching_recent ?? 0} in 5 years)</h3><p class="small muted" style="margin-top:0">Editors like to see you engage with their journal. Consider citing the most relevant ones.</p>
      ${r.recent.map((p, i) => `<div class="result"><div class="t" style="font-size:14px">${esc(p.title)}</div><div class="m">${esc(authorsShort(p.authors))} (${p.year}) · ${p.citations} citations</div><button class="btn small" data-jr="${i}">+ Library</button></div>`).join("") || `<div class="small muted">None found: a sign the topic may be outside this journal's usual scope.</div>`}</div>`;
    $$("[data-jr]").forEach((b) => b.onclick = () => busy(b, async () => { const [st] = await api(`/api/projects/${S.project.id}/papers`, { body: { paper: r.recent[+b.dataset.jr], tags: "target-journal" } }); LIBKEYS.add(st.key); b.textContent = `✓ @${st.key}`; b.disabled = true; }));
  });
}

// ================================================================== SYNC (Zotero, Mendeley via linked files)
async function syncDialog(onDone) {
  const fs = await api(`/api/projects/${S.project.id}/filesync`);
  const hasWeb = S.settings.zotero_user_id && S.settings.zotero_api_key;
  const box = modal(`<h2>Sync with Zotero / Mendeley</h2>
    <div class="card"><h3>Zotero</h3>
      <div class="row"><select id="zm" style="width:300px"><option value="local">Zotero desktop app (no key, read only)</option><option value="web" ${hasWeb ? "" : "disabled"}>Zotero online (read + write)${hasWeb ? "" : ": add key in Settings"}</option></select><button class="btn small" id="zcl">Load collections</button></div>
      <p class="small muted">Desktop: open Zotero, then enable <b>Settings → Advanced → "Allow other applications on this computer to communicate with Zotero"</b>. Online: add your user ID + API key (zotero.org/settings/keys) in AFRA Settings.</p>
      <label class="f">Collection</label><select id="zc"><option value="">Whole library</option></select>
      <div class="row" style="margin-top:10px"><button class="btn primary" id="zimp">Import from Zotero</button><button class="btn" id="zexp" ${hasWeb ? "" : "disabled"} title="Requires Zotero online mode">Send AFRA library to Zotero</button></div><div id="zout" class="small" style="margin-top:6px"></div></div>
    <div class="card"><h3>Mendeley, JabRef, EndNote (linked file)</h3>
      <p class="small muted" style="margin-top:0"><b>Auto-export:</b> AFRA rewrites this .bib/.ris file a few seconds after any library change. In Mendeley use <i>File → Import → BibTeX/RIS</i> on it. JabRef and EndNote can open it directly.<br>
      <b>Import file:</b> point to a .bib/.ris exported from Mendeley (or auto-exported by Zotero's Better BibTeX). Click <i>Import now</i> whenever it changes; duplicates are skipped.</p>
      <label class="f">Auto-export library to</label><input type="text" id="fexp" value="${esc(fs.export_path)}" placeholder="e.g. C:\\Users\\you\\Documents\\AFRA library.bib">
      <label class="f">Import from file</label><input type="text" id="fimp" value="${esc(fs.import_path)}" placeholder="e.g. C:\\Users\\you\\Documents\\Mendeley export.bib">
      <div class="row" style="margin-top:10px"><button class="btn primary" id="fsave">Save & export now</button><button class="btn" id="fnow">Import now</button></div><div id="fout" class="small" style="margin-top:6px"></div></div>
    <div class="modal-foot"><button class="btn" onclick="closeModal()">Close</button></div>`, true);
  $("#zcl", box).onclick = (e) => busy(e.currentTarget, async () => {
    const cols = await api(`/api/zotero/collections?mode=${$("#zm").value}`);
    $("#zc").innerHTML = `<option value="">Whole library</option>` + cols.map((c) => `<option value="${esc(c.key)}">${esc(c.name)}${c.items != null ? ` (${c.items})` : ""}</option>`).join("");
    toast(`${cols.length} collections loaded`, "ok");
  });
  $("#zimp", box).onclick = (e) => busy(e.currentTarget, async () => { const r = await api(`/api/projects/${S.project.id}/zotero/import`, { body: { mode: $("#zm").value, collection: $("#zc").value } }); $("#zout").innerHTML = `<span style="color:var(--ok)">✓ Read ${r.read} items: ${r.added} added, ${r.already_in_library} already in AFRA.</span>`; onDone && onDone(); });
  $("#zexp", box).onclick = (e) => busy(e.currentTarget, async () => { const r = await api(`/api/projects/${S.project.id}/zotero/export`, { body: { collection: $("#zc").value } }); $("#zout").innerHTML = `<span style="color:${r.failed ? "var(--warn)" : "var(--ok)"}">✓ Sent ${r.sent} to Zotero (tag “AFRA”), ${r.already_synced} were already there${r.failed ? `, ${r.failed} failed: ${esc(r.errors.join("; "))}` : ""}.</span>`; });
  $("#fsave", box).onclick = (e) => busy(e.currentTarget, async () => { const r = await api(`/api/projects/${S.project.id}/filesync`, { method: "PUT", body: { export_path: $("#fexp").value, import_path: $("#fimp").value } }); $("#fout").innerHTML = r.export ? `<span style="color:var(--ok)">✓ Exported ${r.export.exported} references to ${esc(r.export.path)}. It will stay updated automatically.</span>` : "Saved."; });
  $("#fnow", box).onclick = (e) => busy(e.currentTarget, async () => { await api(`/api/projects/${S.project.id}/filesync`, { method: "PUT", body: { export_path: $("#fexp").value, import_path: $("#fimp").value } }); const r = await api(`/api/projects/${S.project.id}/filesync/import`, { method: "POST" }); $("#fout").innerHTML = `<span style="color:var(--ok)">✓ Read ${r.read} references, ${r.added} new.</span>`; onDone && onDone(); });
}

// ================================================================== DOCUMENTS
VIEWS.docs = async () => {
  const docId = location.hash.split("/")[1];
  if (docId) return docEditor(docId);
  const docs = await api(`/api/projects/${S.project.id}/documents?kind=doc`);
  main().innerHTML = `<div class="page-head"><div><h1>Documents</h1><p>Free-form Markdown documents with live preview, AI tools, citation picker and DOCX export. Research reports can be saved here for editing.</p></div>
    <div class="row"><label class="btn">Import .md / .txt<input type="file" id="dimp" accept=".md,.txt,.markdown" hidden></label><button class="btn primary" id="dnew">New document</button></div></div>
    <div class="card">${docs.length ? docs.map((d) => `<div class="list-item" data-id="${d.id}"><div class="t">${esc(d.title)}</div><div class="small muted">Updated ${fmtDate(d.updated)} · ${esc((d.preview || "").replace(/[#*\n]+/g, " ").slice(0, 160))}</div></div>`).join("") : `<div class="empty">No documents yet.</div>`}</div>`;
  $$(".list-item[data-id]").forEach((el) => el.onclick = () => location.hash = "#docs/" + el.dataset.id);
  $("#dnew").onclick = async () => { const d = await api(`/api/projects/${S.project.id}/documents`, { body: { title: "Untitled document", content: "# Untitled document\n\n## Introduction\n\n" } }); location.hash = "#docs/" + d.id; };
  $("#dimp").onchange = async (e) => { const f = e.target.files[0]; const d = await api(`/api/projects/${S.project.id}/documents`, { body: { title: f.name.replace(/\.\w+$/, ""), content: await f.text() } }); location.hash = "#docs/" + d.id; };
};
async function docEditor(id) {
  const doc = await api(`/api/documents/${id}`);
  await refreshLibKeys();
  main().innerHTML = `
  <div class="page-head"><div class="row grow"><a href="#docs" class="btn ghost">← Documents</a><input type="text" id="dtitle" value="${esc(doc.title)}" style="font:600 18px var(--serif);max-width:640px"></div><span class="muted small" id="dsaved">Saved</span></div>
  <div class="toolbar">
    <button class="btn small primary" id="dexp">Export DOCX</button><button class="btn small" id="dcite">Insert citation</button>
    <button class="btn small" id="dsugg">Suggest citations</button><button class="btn small" id="dqc">Quality check</button><button class="btn small" id="dsim">Similarity</button><button class="btn small" id="dev" title="Select an argument in the editor; find papers that back it">Find evidence</button><button class="btn small" id="dref">Check references</button><button class="btn small" id="dfit">Journal fit</button>
    <span class="sep"></span>
    <select id="dtool" style="width:210px">${Object.entries(S.meta.tools).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select>
    <button class="btn small" id="drun">Run on selection</button>
    <span class="sep"></span>
    <select id="dlayout" style="width:140px"><option value="split">Editor + preview</option><option value="edit">Editor only</option><option value="preview">Preview only</option></select>
    <span class="muted small" id="dwc"></span>
    <span class="sep"></span><button class="btn small" id="dmd">Download .md</button><button class="btn small danger" id="ddel">Delete</button>
  </div>
  <div id="dpanel"></div>
  <div class="editor-wrap" id="dwrap"><textarea id="dta" spellcheck="true"></textarea><div class="preview prose" id="dprev"></div></div>`;
  const ta = $("#dta"); ta.value = doc.content;
  const draft = Draft.get("doc_" + id);
  if (draft && draft.t > doc.updated * 1000 && (draft.v.content !== doc.content || draft.v.title !== doc.title)) {
    ta.value = draft.v.content; $("#dtitle").value = draft.v.title; toast("Recovered unsaved changes from this browser", "ok");
  }
  const preview = () => { $("#dprev").innerHTML = md(ta.value); $("#dwc").textContent = `${wordCount(ta.value).toLocaleString()} words`; };
  const saver = autosaver("doc_" + id, () => api(`/api/documents/${id}`, { method: "PUT", body: { title: $("#dtitle").value, content: ta.value } }), () => $("#dsaved"));
  const changed = () => saver({ title: $("#dtitle").value, content: ta.value });
  if (draft && ta.value !== doc.content) changed();
  ta.oninput = () => { preview(); changed(); }; $("#dtitle").oninput = changed; preview();
  $("#dlayout").onchange = (e) => { const v = e.target.value; $("#dwrap").className = "editor-wrap" + (v === "split" ? "" : " single"); ta.style.display = v === "preview" ? "none" : ""; $("#dprev").style.display = v === "edit" ? "none" : ""; };
  $("#dexp").onclick = () => exportDialog({ markdown: ta.value, meta: { title: "" }, filenameHint: $("#dtitle").value });
  $("#dcite").onclick = () => citePicker((c) => insertAtCursor(ta, c));
  $("#dmd").onclick = () => download(new Blob([ta.value], { type: "text/markdown" }), ($("#dtitle").value || "document") + ".md");
  $("#ddel").onclick = async () => { if (!confirm("Delete this document?")) return; await api(`/api/documents/${id}`, { method: "DELETE" }); location.hash = "#docs"; };
  $("#dsim").onclick = (e) => busy(e.currentTarget, () => similarityCheck(ta.value, $("#dpanel")));
  $("#dref").onclick = (e) => busy(e.currentTarget, () => refCheck($("#dpanel"), ta.value));
  $("#dfit").onclick = () => journalFitDialog(ta.value);
  $("#dev").onclick = (e) => busy(e.currentTarget, async () => {
    const s0 = ta.selectionStart, e0 = ta.selectionEnd; const claim = ta.value.slice(s0, e0).trim();
    if (!claim) return toast("Select the sentence(s) with your argument first");
    let at = e0;  // insert citations at the end of the selected argument (before its final period)
    if (/[.!?]$/.test(ta.value.slice(s0, e0).trimEnd())) at = s0 + ta.value.slice(s0, e0).trimEnd().length - 1;
    await runEvidence(claim, $("#dpanel"), (cite) => {
      const before = ta.value.slice(0, at);
      const m = before.match(/\[(@[^\]]+)\]\s*$/);  // merge with a citation group already placed there
      if (m) { const start = at - m[0].length; ta.value = before.slice(0, start) + `[${m[1]}; ${cite.slice(1, -1)}]` + ta.value.slice(at); at = start + m[1].length + cite.length + 1; }
      else { ta.value = before + " " + cite + ta.value.slice(at); at += cite.length + 1; }
      ta.dispatchEvent(new Event("input")); toast(`Inserted ${cite}`, "ok");
    }, ["openalex", "europepmc"]);
  });
  $("#dqc").onclick = (e) => busy(e.currentTarget, async () => { const r = await api("/api/quality", { body: { project_id: S.project.id, text: ta.value } }); $("#dpanel").innerHTML = qualityHTML(r, true); });
  $("#drun").onclick = (e) => busy(e.currentTarget, async () => {
    const s = ta.selectionStart, en = ta.selectionEnd; const sel = ta.value.slice(s, en);
    if (!sel.trim()) return toast("Select some text in the editor first");
    const tool = $("#dtool").value; const replaceable = ["polish", "paraphrase", "shorten", "expand", "translate_en", "translate_id"].includes(tool);
    $("#dpanel").innerHTML = `<div class="card"><div class="row" style="justify-content:space-between"><b>${esc(S.meta.tools[tool])}</b><div class="row">${replaceable ? `<button class="btn small primary" id="prep">Replace selection</button>` : ""}<button class="btn small" id="pins">Insert below</button><button class="btn small" id="pcopy">Copy</button><button class="btn small ghost" id="pclose">✕</button></div></div><div class="prose" id="pout" style="font-size:15px;margin-top:8px"></div></div>`;
    $("#pclose").onclick = () => $("#dpanel").innerHTML = "";
    const out = await streamText("/api/tools/run", { tool, text: sel }, (full) => $("#pout").innerHTML = md(full));
    if ($("#prep")) $("#prep").onclick = () => { ta.value = ta.value.slice(0, s) + out.trim() + ta.value.slice(en); ta.dispatchEvent(new Event("input")); $("#dpanel").innerHTML = ""; };
    $("#pins").onclick = () => { ta.value = ta.value.slice(0, en) + "\n\n" + out.trim() + "\n\n" + ta.value.slice(en); ta.dispatchEvent(new Event("input")); $("#dpanel").innerHTML = ""; };
    $("#pcopy").onclick = () => { navigator.clipboard.writeText(out); toast("Copied"); };
  });
  $("#dsugg").onclick = (e) => busy(e.currentTarget, async () => {
    const s = ta.selectionStart, en = ta.selectionEnd; const text = (en > s ? ta.value.slice(s, en) : ta.value).slice(0, 12000);
    const r = await api("/api/tools/suggest-citations", { body: { project_id: S.project.id, text } });
    showSuggestions(r.suggestions || [], ta, $("#dpanel"));
  });
}
function showSuggestions(list, ta, panel) {
  panel.innerHTML = `<div class="card"><div class="row" style="justify-content:space-between"><b>Citation suggestions (${list.length})</b><button class="btn small ghost" id="sclose">✕</button></div>
    ${list.map((s, i) => `<div class="result"><div style="font-family:var(--serif)">“${esc(s.sentence)}”</div><div class="small muted">${(s.keys || []).map((k) => `<span class="key">@${esc(k)}</span>`).join(" ")} — ${esc(s.reason || "")}</div><button class="btn small" data-si="${i}">Insert after sentence</button></div>`).join("") || `<div class="empty">No suggestions.</div>`}</div>`;
  $("#sclose").onclick = () => panel.innerHTML = "";
  $$("[data-si]", panel).forEach((b) => b.onclick = () => {
    const s = list[+b.dataset.si]; const cite = ` [${s.keys.map((k) => "@" + k).join("; ")}]`;
    const sent = s.sentence.trim().replace(/[.!?]$/, ""); const idx = ta.value.indexOf(sent);
    if (idx < 0) return toast("Sentence not found exactly in text", "bad");
    const at = idx + sent.length; ta.value = ta.value.slice(0, at) + cite + ta.value.slice(at); ta.dispatchEvent(new Event("input")); b.textContent = "✓ Inserted"; b.disabled = true;
  });
}
async function similarityCheck(text, panel) {
  const r = await api("/api/similarity", { body: { project_id: S.project.id, text } });
  panel.innerHTML = `<div class="card"><div class="row" style="justify-content:space-between"><b>Similarity check</b><button class="btn small ghost" onclick="this.closest('.card').remove()">✕</button></div>
    <div style="margin-top:8px"><span class="metric"><b style="color:${r.overall_pct > 15 ? "var(--bad)" : r.overall_pct > 5 ? "var(--warn)" : "var(--ok)"}">${r.overall_pct}%</b><span>text overlapping sources</span></span><span class="metric"><b>${r.flagged.length}</b><span>sentences flagged</span></span><span class="metric"><b>${r.sources_checked}</b><span>library sources checked</span></span></div>
    <p class="small muted" style="margin:0 0 6px">Compares 7-word sequences with abstracts and full texts in your library (no AI tokens). Paraphrase flagged sentences, or quote and cite them. This is not a replacement for Turnitin/iThenticate.</p>
    ${r.flagged.map((f) => `<div class="result"><div style="font-family:var(--serif)">“${esc(f.sentence)}”</div><div class="small muted">${f.pct}% matches <span class="key">@${esc(f.source)}</span></div></div>`).join("") || `<div class="small" style="color:var(--ok)">✓ No overlapping sentences found</div>`}</div>`;
}
function qualityHTML(r, closable) {
  return `<div class="card"><div class="row" style="justify-content:space-between"><b>Quality check</b>${closable ? `<button class="btn small ghost" onclick="this.closest('.card').remove()">✕</button>` : ""}</div>
    <div style="margin-top:8px"><span class="metric"><b>${r.words}</b><span>words</span></span><span class="metric"><b>${r.citations}</b><span>citations</span></span><span class="metric"><b>${r.unique_sources}</b><span>unique sources</span></span><span class="metric"><b>${r.citations_per_100_words}</b><span>cites / 100 words</span></span><span class="metric"><b>${r.long_sentences}</b><span>long sentences</span></span><span class="metric"><b>${r.todos}</b><span>TODOs</span></span><span class="metric" title="Flesch reading ease: academic prose is typically 10–40; higher is easier to read"><b>${r.readability}</b><span>readability</span></span><span class="metric"><b>${r.avg_sentence_words}</b><span>words / sentence</span></span></div>
    ${r.gaps.length ? `<div style="margin:6px 0"><b>Gap evidence gate (3–5 citations each)</b>${r.gaps.map((g) => `<div class="small">${g.ok ? "✅" : "❌"} ${esc(g.gap)} — ${g.citations} citations</div>`).join("")}</div>` : ""}
    ${r.warnings.length ? r.warnings.map((w) => `<div class="small" style="color:var(--warn)">⚠ ${esc(w)}</div>`).join("") : `<div class="small" style="color:var(--ok)">✓ No issues found</div>`}</div>`;
}

// ================================================================== WRITING TOOLS
VIEWS.tools = async () => {
  main().innerHTML = `<div class="page-head"><div><h1>Writing Tools</h1><p>Polish, paraphrase, translate, critique, and ask questions answered from your own library with citations.</p></div></div>
  ${needLLM()}
  <div class="tabs" id="ttabs"><button class="active" data-t="tools">Text tools</button><button data-t="ask">Ask your library</button></div>
  <div id="tbody"></div>`;
  const tabs = {
    tools: () => {
      $("#tbody").innerHTML = `<div class="grid g2"><div class="card"><div class="row"><select id="ttool" class="grow">${Object.entries(S.meta.tools).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select>
        <select id="tlang" style="width:150px"><option value="">Output: same</option><option>English</option><option>Indonesian</option></select></div>
        <label class="f">Text</label><textarea id="tin" rows="16" placeholder="Paste your text here…"></textarea>
        <label class="f">Additional instruction <small>(optional)</small></label><input type="text" id="textra" placeholder="e.g. target journal is Energy Policy; keep under 200 words">
        <div class="row" style="margin-top:10px"><button class="btn primary" id="tgo">Run</button><span class="muted small" id="twc"></span></div></div>
        <div class="card"><div class="row" style="justify-content:space-between"><b>Output</b><button class="btn small" id="tcopy">Copy</button></div><div class="prose" id="tout" style="font-size:15px"></div></div></div>`;
      $("#tin").oninput = () => $("#twc").textContent = wordCount($("#tin").value) + " words";
      let out = "";
      $("#tgo").onclick = (e) => busy(e.currentTarget, async () => { out = await streamText("/api/tools/run", { tool: $("#ttool").value, text: $("#tin").value, extra: $("#textra").value, language: $("#tlang").value }, (f) => $("#tout").innerHTML = md(f)); });
      $("#tcopy").onclick = () => { navigator.clipboard.writeText(out); toast("Copied"); };
    },
    ask: () => {
      $("#tbody").innerHTML = `<div class="card"><p class="muted" style="margin-top:0">Answers use only the abstracts, notes and stored full texts in this project’s library, with <span class="key">[@key]</span> citations.</p>
        <div class="row"><input type="text" class="grow" id="aq" placeholder="e.g. What methods have been used to estimate CO2 storage efficiency factors?"><button class="btn primary" id="ago">Ask</button></div>
        <div class="prose" id="aout" style="margin-top:14px"></div><div class="row" id="aact" style="display:none;margin-top:10px"><button class="btn small" id="asave">Save as document</button></div></div>`;
      let out = "";
      const go = () => busy($("#ago"), async () => { await refreshLibKeys(); out = await streamText("/api/tools/ask-library", { project_id: S.project.id, question: $("#aq").value }, (f) => $("#aout").innerHTML = md(f)); $("#aact").style.display = "flex"; });
      $("#ago").onclick = go; $("#aq").onkeydown = (e) => { if (e.key === "Enter") go(); };
      $("#asave").onclick = async () => { const d = await api(`/api/projects/${S.project.id}/documents`, { body: { title: $("#aq").value.slice(0, 100), content: `# ${$("#aq").value}\n\n${out}` } }); location.hash = "#docs/" + d.id; };
    },
  };
  $$("#ttabs button").forEach((b) => b.onclick = () => { $$("#ttabs button").forEach((x) => x.classList.toggle("active", x === b)); tabs[b.dataset.t](); });
  tabs.tools();
};

// ================================================================== PAPER WRITER
const PAPER_TYPES = ["Empirical research article", "Review article (narrative)", "Systematic literature review", "Conference paper", "Case study", "Short communication", "Thesis / dissertation chapter", "Research proposal", "Technical report"];
VIEWS.writer = async () => {
  const docId = location.hash.split("/")[1];
  if (docId) return paperWorkspace(docId);
  const docs = await api(`/api/projects/${S.project.id}/documents?kind=paper`);
  main().innerHTML = `<div class="page-head"><div><h1>Paper Writer</h1><p>A guided two-phase workflow. <b>Strategist</b>: venue style analysis → literature & evidence-based gaps → outline → reviewer gate (≥ ${S.meta.gate}/35). <b>Composer</b>: section-by-section drafting with quality checks → polish → formatted DOCX.</p></div><button class="btn primary" id="pnew">New manuscript</button></div>
    ${needLLM()}
    <div class="card">${docs.length ? docs.map((d) => `<div class="list-item" data-id="${d.id}"><div class="t">${esc(d.title)}</div><div class="small muted">Updated ${fmtDate(d.updated)}</div></div>`).join("") : `<div class="empty">No manuscripts yet. Tip: first collect 15–40 relevant sources in the Library (via Deep Research or Search).</div>`}</div>`;
  $$(".list-item[data-id]").forEach((el) => el.onclick = () => location.hash = "#writer/" + el.dataset.id);
  $("#pnew").onclick = async () => {
    const meta = { title: "Untitled manuscript", language: S.settings.default_language, citation_style: S.settings.default_citation_style, template: S.settings.default_template, paper_type: PAPER_TYPES[0], word_target: 7000, step: "setup", outline: [], sections: [] };
    const d = await api(`/api/projects/${S.project.id}/documents`, { body: { title: meta.title, kind: "paper", meta } });
    location.hash = "#writer/" + d.id;
  };
};
async function paperWorkspace(id) {
  const doc = await api(`/api/documents/${id}`);
  await refreshLibKeys();
  let P = doc.meta;
  const draft = Draft.get("paper_" + id);
  const recovered = draft && draft.t > doc.updated * 1000 && JSON.stringify(draft.v) !== JSON.stringify(P);
  if (recovered) { P = draft.v; toast("Recovered unsaved changes from this browser", "ok"); }
  P.outline = P.outline || []; P.sections = P.sections || [];
  const saver = autosaver("paper_" + id, () => api(`/api/documents/${id}`, { method: "PUT", body: { title: P.title || "Untitled manuscript", meta: P, content: assemble() } }), () => $("#psaved"), 700);
  const save = () => saver(P);
  if (recovered) save();
  const assemble = () => P.sections.filter((s) => s.content).map((s) => `# ${s.title}\n\n${s.content.trim()}`).join("\n\n");
  const steps = [
    ["setup", "Setup", "Strategist"], ["style", "Style guide"], ["lit", "Literature & gaps"], ["outline", "Outline"], ["review", "Reviewer gate"],
    ["write", "Write sections", "Composer"], ["final", "Polish & export"],
  ];
  const done = { setup: !!(P.research_question), style: !!P.style_guide, lit: !!P.literature, outline: P.outline.length > 0, review: !!P.outline_review?.passed, write: P.sections.length && P.sections.every((s) => s.content), final: !!P.abstract };
  main().innerHTML = `<div class="page-head"><div class="row grow"><a href="#writer" class="btn ghost">← Manuscripts</a><h2 style="margin:0">${esc(P.title)}</h2></div><span class="muted small" id="psaved">Saved</span></div>
    ${needLLM()}
    <div class="stepper" id="steps">${steps.map(([k, label, phase], i) => `${phase ? `<span class="phase">${phase}</span>` : ""}<button data-s="${k}" class="${P.step === k ? "active" : ""} ${done[k] ? "done" : ""}"><b>${done[k] ? "✓" : i + 1}</b>${label}</button>`).join("")}</div>
    <div id="pstep"></div>`;
  $$("#steps button").forEach((b) => b.onclick = () => { P.step = b.dataset.s; save(); paperWorkspace.go(b.dataset.s); $$("#steps button").forEach((x) => x.classList.toggle("active", x === b)); });
  const box = () => $("#pstep");
  const field = (k, label, type = "text", extra = "") => `<label class="f">${label}</label>${type === "textarea" ? `<textarea data-k="${k}" rows="3" ${extra}>${esc(P[k] || "")}</textarea>` : `<input type="${type}" data-k="${k}" value="${esc(P[k] ?? "")}" ${extra}>`}`;
  const bindFields = (root) => $$("[data-k]", root).forEach((el) => el.oninput = el.onchange = () => { P[el.dataset.k] = el.type === "number" ? +el.value : el.value; save(); });
  const libCount = LIBKEYS.size;

  const views = {
    setup() {
      box().innerHTML = `<div class="grid g2"><div class="card"><h3>Manuscript</h3>
        ${field("title", "Working title")}${field("research_question", "Research question(s) / objective", "textarea")}
        <div class="grid g2" style="gap:0 12px"><div>${field("field", "Field / discipline")}</div>
          <div><label class="f">Paper type</label><select data-k="paper_type">${PAPER_TYPES.map((t) => `<option ${t === P.paper_type ? "selected" : ""}>${t}</option>`).join("")}</select></div>
          <div>${field("venue", "Target journal / conference / platform")}</div><div>${field("word_target", "Target length (words)", "number")}</div></div>
        ${field("contribution", "Intended contribution / novelty", "textarea")}</div>
        <div class="card"><h3>Study details <small class="muted">(what you actually did — the AI will never invent results)</small></h3>
        ${field("method", "Method / design", "textarea")}${field("data", "Data / sample / setting", "textarea")}${field("findings", "Key findings / results (if available)", "textarea", 'rows="4"')}
        <div class="grid g3" style="gap:0 12px">
          <div><label class="f">Language</label><select data-k="language">${["English", "Indonesian"].map((l) => `<option ${l === P.language ? "selected" : ""}>${l}</option>`).join("")}</select></div>
          <div><label class="f">Citation style</label><select data-k="citation_style">${Object.entries(S.meta.styles).map(([k, v]) => `<option value="${k}" ${k === P.citation_style ? "selected" : ""}>${v}</option>`).join("")}</select></div>
          <div><label class="f">DOCX template</label><select data-k="template">${Object.entries(S.meta.templates).map(([k, v]) => `<option value="${k}" ${k === P.template ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></div></div>
        <div class="notice info" style="margin-top:14px">Library has <b>${libCount}</b> sources. ${libCount < 15 ? "Add more (15–40 recommended) via Deep Research or Literature Search before the literature step." : "Good."}</div>
        <div class="row"><button class="btn primary" id="next">Next: style guide →</button></div></div></div>`;
      bindFields(box());
      $("#next").onclick = () => $(`#steps [data-s=style]`).click();
    },
    style() {
      box().innerHTML = `<div class="grid g2"><div class="card"><h3>Phase 1 · Platform analysis</h3>
        <p class="muted small">Paste author guidelines and/or 3–10 abstracts or introductions from recent papers in <b>${esc(P.venue || "your target venue")}</b>. The AI extracts the venue’s conventions. Leave empty to use general conventions.</p>
        <textarea id="samples" rows="16" placeholder="Paste author guidelines, sample abstracts, introductions…">${esc(P.samples || "")}</textarea>
        <div class="row" style="margin-top:10px"><button class="btn primary" id="gen">Generate style guide</button></div></div>
        <div class="card"><h3>Style guide <small class="muted">(editable)</small></h3><textarea id="sg" rows="24">${esc(P.style_guide || "")}</textarea>
        <div class="row" style="margin-top:10px"><button class="btn" id="next">Next: literature →</button></div></div></div>`;
      $("#samples").oninput = (e) => { P.samples = e.target.value; save(); };
      $("#sg").oninput = (e) => { P.style_guide = e.target.value; save(); };
      $("#gen").onclick = (e) => busy(e.currentTarget, async () => { P.style_guide = await streamText(`/api/paper/${id}/style-guide`, { samples: P.samples || "" }, (f) => { $("#sg").value = f; }); save(); });
      $("#next").onclick = () => $(`#steps [data-s=lit]`).click();
    },
    lit() {
      box().innerHTML = `<div class="card"><h3>Phase 2 · Theoretical framework & evidence-based gaps</h3>
        <p class="muted small">Synthesizes your library (${libCount} sources) into themes, a theoretical framework and research gaps. <b>Gate:</b> every gap must be supported by 3–5 citations.</p>
        <div class="toolbar"><button class="btn primary" id="gen">Generate literature analysis</button><button class="btn" id="qc">Run gap evidence check</button><button class="btn" id="cite">Insert citation</button><a class="btn" href="#research">Find more sources</a>
        <select id="lay" style="width:150px;margin-left:auto"><option value="split">Edit + preview</option><option value="edit">Edit</option><option value="preview">Preview</option></select></div>
        <div id="qcout"></div>
        <div class="editor-wrap" id="lw"><textarea id="lit">${esc(P.literature || "")}</textarea><div class="preview prose" id="lprev"></div></div>
        <div class="row" style="margin-top:10px"><button class="btn" id="next">Next: outline →</button></div></div>`;
      const ta = $("#lit"); const pv = () => $("#lprev").innerHTML = md(ta.value); pv();
      ta.oninput = () => { P.literature = ta.value; pv(); save(); };
      layoutSwitch($("#lay"), $("#lw"), ta, $("#lprev"));
      const qc = async () => { const r = await api("/api/quality", { body: { project_id: S.project.id, text: ta.value } }); $("#qcout").innerHTML = qualityHTML(r, true); };
      $("#gen").onclick = (e) => busy(e.currentTarget, async () => { P.literature = await streamText(`/api/paper/${id}/literature`, {}, (f) => { ta.value = f; pv(); }); save(); await qc(); });
      $("#qc").onclick = (e) => busy(e.currentTarget, qc);
      $("#cite").onclick = () => citePicker((c) => insertAtCursor(ta, c));
      $("#next").onclick = () => $(`#steps [data-s=outline]`).click();
    },
    outline() {
      const total = () => P.outline.reduce((a, s) => a + (+s.words || 0), 0);
      const draw = () => {
        box().innerHTML = `<div class="card"><h3>Phase 3 · Outline</h3>
          <div class="toolbar"><button class="btn primary" id="gen">${P.outline.length ? "Regenerate" : "Generate"} outline</button><button class="btn" id="add">+ Section</button>
          <span class="muted small">Total: <b id="otot">${total()}</b> / ${P.word_target || "?"} words</span><button class="btn" id="next" style="margin-left:auto">Next: reviewer gate →</button></div>
          <div id="olist">${P.outline.map((s, i) => `<div class="outline-sec" data-i="${i}">
            <div class="row"><b class="muted">${i + 1}.</b><input type="text" class="grow" data-f="title" value="${esc(s.title)}"><input type="number" data-f="words" value="${s.words || 0}" style="width:90px" title="words">
            <button class="btn small ghost" data-m="up">↑</button><button class="btn small ghost" data-m="down">↓</button><button class="btn small ghost danger" data-m="del">✕</button></div>
            <label class="f">Key points <small>(one per line)</small></label><textarea data-f="points" rows="${Math.max(3, (s.points || []).length)}">${esc((s.points || []).join("\n"))}</textarea>
            <label class="f">Citations <small>(keys, comma-separated)</small></label><input type="text" data-f="citations" value="${esc((s.citations || []).join(", "))}"></div>`).join("") || `<div class="empty">Generate an outline from your style guide and literature analysis.</div>`}</div></div>`;
        $$(".outline-sec").forEach((el) => {
          const s = P.outline[+el.dataset.i];
          $$("[data-f]", el).forEach((inp) => inp.oninput = () => {
            const f = inp.dataset.f;
            s[f] = f === "points" ? inp.value.split("\n").filter((x) => x.trim()) : f === "citations" ? inp.value.split(",").map((x) => x.trim().replace(/^@/, "")).filter(Boolean) : f === "words" ? +inp.value : inp.value;
            $("#otot").textContent = total(); save();
          });
          $$("[data-m]", el).forEach((b) => b.onclick = () => {
            const i = +el.dataset.i, m = b.dataset.m;
            if (m === "del") P.outline.splice(i, 1);
            if (m === "up" && i > 0) [P.outline[i - 1], P.outline[i]] = [P.outline[i], P.outline[i - 1]];
            if (m === "down" && i < P.outline.length - 1) [P.outline[i + 1], P.outline[i]] = [P.outline[i], P.outline[i + 1]];
            save(); draw();
          });
        });
        $("#add").onclick = () => { P.outline.push({ title: "New section", words: 500, points: [], citations: [] }); save(); draw(); };
        $("#gen").onclick = (e) => busy(e.currentTarget, async () => { const r = await api(`/api/paper/${id}/outline`, { body: {} }); P.outline = r.sections; P.outline_review = null; save(); draw(); });
        $("#next").onclick = () => $(`#steps [data-s=review]`).click();
      };
      draw();
    },
    review() {
      const draw = () => {
        const r = P.outline_review;
        box().innerHTML = `<div class="card"><h3>Phase 3b · Reviewer simulation (gate)</h3>
          <p class="muted small">A strict simulated reviewer scores the outline on 7 dimensions (35 points). You need <b>${S.meta.gate}/35</b> to pass the gate before drafting.</p>
          <div class="toolbar"><button class="btn primary" id="rev">Run review</button>${r ? `<button class="btn" id="fix">Revise outline using feedback</button>` : ""}<button class="btn" id="next" style="margin-left:auto">${r?.passed ? "Next: write sections →" : "Skip gate & write →"}</button></div>
          ${r ? reviewHTML(r) : `<div class="empty">No review yet.</div>`}</div>`;
        $("#rev").onclick = (e) => busy(e.currentTarget, async () => { P.outline_review = await api(`/api/paper/${id}/review`, { body: { target: "outline" } }); save(); draw(); });
        if ($("#fix")) $("#fix").onclick = (e) => busy(e.currentTarget, async () => { const res = await api(`/api/paper/${id}/outline`, { body: { revise: true } }); P.outline = res.sections; P.outline_review = null; save(); toast("Outline revised — run the review again", "ok"); $(`#steps [data-s=outline]`).click(); });
        $("#next").onclick = () => $(`#steps [data-s=write]`).click();
      };
      draw();
    },
    write() {
      // sync sections with outline (keep content by title)
      const byTitle = Object.fromEntries(P.sections.map((s) => [s.title, s]));
      if (P.outline.length) P.sections = P.outline.map((o) => ({ ...o, content: byTitle[o.title]?.content || "" }));
      let cur = P.cur || 0; if (cur >= P.sections.length) cur = 0;
      if (!P.sections.length) { box().innerHTML = `<div class="card empty">Create an outline first.</div>`; return; }
      const gateWarn = !P.outline_review?.passed ? `<div class="notice">The outline has not passed the reviewer gate (${P.outline_review ? P.outline_review.total + "/35" : "not reviewed"}). You can still write, but consider revising first.</div>` : "";
      box().innerHTML = `${gateWarn}<div class="split-wide"><div class="card" id="slist"></div><div class="card" id="sedit"></div></div>`;
      const list = () => {
        $("#slist").innerHTML = `<h3>Sections</h3>${P.sections.map((s, i) => { const w = wordCount(s.content); const pct = s.words ? Math.min(100, Math.round(w * 100 / s.words)) : 0;
          return `<div class="list-item ${i === cur ? "active" : ""}" data-i="${i}"><div class="t">${i + 1}. ${esc(s.title)}</div><div class="small muted">${w} / ${s.words || "?"} words</div><div class="bar" style="margin-top:4px"><span style="width:${pct}%;background:${pct >= 75 ? "var(--ok)" : "var(--accent-2)"}"></span></div></div>`; }).join("")}
          <div class="small muted" style="margin-top:8px">Total ${wordCount(assemble()).toLocaleString()} / ${P.word_target || "?"} words</div>
          <button class="btn small" id="draftAll" style="margin-top:8px;width:100%">Draft all empty sections</button>`;
        $$("#slist .list-item").forEach((el) => el.onclick = () => { cur = +el.dataset.i; P.cur = cur; list(); editor(); });
        $("#draftAll").onclick = (e) => busy(e.currentTarget, async () => {
          for (let i = 0; i < P.sections.length; i++) { if (P.sections[i].content) continue; cur = i; list(); editor(); await draft(""); }
        });
      };
      const draft = async (instruction) => {
        const s = P.sections[cur]; const ta = $("#sta");
        const before = s.content || "";
        const res = await streamText(`/api/paper/${id}/section/${cur}`, { instruction, current: before }, (f) => { ta.value = f; $("#sprev").innerHTML = md(f); s.content = f; save(); });
        if (!res.trim()) { s.content = before; ta.value = before; save(); return; }
        s.content = res.trim(); ta.value = s.content; save(); list(); await qc();
      };
      const qc = async () => { const s = P.sections[cur]; const r = await api("/api/quality", { body: { project_id: S.project.id, text: s.content || "", target_words: s.words } }); $("#sqc").innerHTML = qualityHTML(r, true); };
      const editor = () => {
        const s = P.sections[cur];
        $("#sedit").innerHTML = `<div class="row" style="justify-content:space-between"><h3 style="margin:0">${cur + 1}. ${esc(s.title)}</h3><span class="muted small">target ${s.words} words</span></div>
          <details style="margin:8px 0"><summary class="small muted">Plan: ${(s.points || []).length} points · ${(s.citations || []).length} suggested citations</summary><ul class="small">${(s.points || []).map((p) => `<li>${esc(p)}</li>`).join("")}</ul><div class="small">${(s.citations || []).map((k) => `<span class="key">@${esc(k)}</span>`).join(" ")}</div></details>
          <div class="toolbar"><button class="btn small primary" id="sdraft">${s.content ? "Redraft" : "Draft with AI"}</button>
            <input type="text" id="sinstr" placeholder="Revision instruction, e.g. ‘add a comparison table’, ‘strengthen the gap argument’" style="flex:1;min-width:220px"><button class="btn small" id="srevise">Revise</button>
            <span class="sep"></span><button class="btn small" id="scite">Cite</button><button class="btn small" id="ssugg">Suggest citations</button><button class="btn small" id="sqcb">Check</button><button class="btn small" id="ssim">Similarity</button>
            <select id="slay" style="width:130px"><option value="split">Edit + preview</option><option value="edit">Edit</option><option value="preview">Preview</option></select></div>
          <div id="sqc"></div><div id="ssug"></div>
          <div class="editor-wrap" id="sw"><textarea id="sta">${esc(s.content || "")}</textarea><div class="preview prose" id="sprev"></div></div>`;
        const ta = $("#sta"); $("#sprev").innerHTML = md(ta.value);
        ta.oninput = () => { s.content = ta.value; $("#sprev").innerHTML = md(ta.value); save(); };
        ta.onblur = list;
        layoutSwitch($("#slay"), $("#sw"), ta, $("#sprev"));
        $("#sdraft").onclick = (e) => busy(e.currentTarget, () => draft(""));
        $("#srevise").onclick = (e) => busy(e.currentTarget, () => { const ins = $("#sinstr").value.trim(); if (!ins) return toast("Type a revision instruction"); return draft(ins); });
        $("#scite").onclick = () => citePicker((c) => insertAtCursor(ta, c));
        $("#sqcb").onclick = (e) => busy(e.currentTarget, qc);
        $("#ssim").onclick = (e) => busy(e.currentTarget, () => similarityCheck(ta.value, $("#sqc")));
        $("#ssugg").onclick = (e) => busy(e.currentTarget, async () => { const r = await api("/api/tools/suggest-citations", { body: { project_id: S.project.id, text: ta.value } }); showSuggestions(r.suggestions || [], ta, $("#ssug")); });
      };
      list(); editor();
    },
    final() {
      const draw = () => {
        const r = P.manuscript_review;
        box().innerHTML = `<div class="grid g2"><div class="card"><h3>Phase 6 · Polish</h3>
          <div class="toolbar"><button class="btn primary" id="abs">Generate title, abstract & keywords</button></div>
          <div id="titles"></div>
          ${field("title", "Final title")}${field("abstract", "Abstract", "textarea", 'rows="9"')}${field("keywords", "Keywords (comma-separated)")}
          <div class="grid g2" style="gap:0 12px"><div>${field("authors", "Author(s)")}</div><div>${field("affiliation", "Affiliation")}</div></div>
          <div class="row" style="margin-top:14px"><button class="btn primary" id="exp">Export DOCX</button><button class="btn" id="pfit">Journal fit</button><button class="btn" id="pref">Check references</button><button class="btn" id="todoc">Open as editable document</button><button class="btn" id="mdl">Download .md</button></div>
          <p class="muted small">Manuscript: ${wordCount(assemble()).toLocaleString()} words · ${P.sections.filter((s) => s.content).length}/${P.sections.length} sections drafted.</p></div>
          <div class="card"><h3>Final manuscript review</h3><p class="muted small">Full-manuscript reviewer simulation against the same 35-point rubric.</p>
          <button class="btn" id="mrev">Review manuscript</button>${r ? reviewHTML(r) : ""}</div></div>`;
        bindFields(box());
        $("#abs").onclick = (e) => busy(e.currentTarget, async () => {
          const res = await api(`/api/paper/${id}/abstract`, { method: "POST" });
          P.abstract = res.abstract || P.abstract; P.keywords = (res.keywords || []).join(", ") || P.keywords; save(); draw();
          $("#titles").innerHTML = `<div class="notice info"><b>Title options</b> (click to use)${(res.titles || []).map((t) => `<div><a href="#" data-t="${esc(t)}">${esc(t)}</a></div>`).join("")}</div>`;
          $$("[data-t]").forEach((a) => a.onclick = (ev) => { ev.preventDefault(); P.title = a.dataset.t; $("[data-k=title]").value = P.title; save(); });
        });
        $("#mrev").onclick = (e) => busy(e.currentTarget, async () => { P.manuscript_review = await api(`/api/paper/${id}/review`, { body: { target: "manuscript" } }); save(); draw(); });
        $("#exp").onclick = () => exportDialog({ markdown: assemble(), meta: { title: P.title, abstract: P.abstract, keywords: P.keywords, authors: P.authors, affiliation: P.affiliation, lang: P.language === "Indonesian" ? "id" : "en" }, template: P.template, style: P.citation_style });
        $("#todoc").onclick = async () => { const md_ = `# ${P.title}\n\n## Abstract\n\n${P.abstract || ""}\n\n` + P.sections.filter((s) => s.content).map((s) => `## ${s.title}\n\n${s.content.trim().replace(/^### /gm, "### ")}`).join("\n\n");
          const d = await api(`/api/projects/${S.project.id}/documents`, { body: { title: P.title, content: md_ } }); location.hash = "#docs/" + d.id; };
        const fullMd = () => `# ${P.title}\n\n## Abstract\n\n${P.abstract || ""}\n\nKeywords: ${P.keywords || ""}\n\n` + assemble();
        $("#pfit").onclick = () => journalFitDialog(fullMd());
        $("#pref").onclick = (e) => busy(e.currentTarget, async () => { const host = $("#pref").closest(".card"); let panel = $("#prefout"); if (!panel) { host.insertAdjacentHTML("beforeend", `<div id="prefout"></div>`); panel = $("#prefout"); } await refCheck(panel, fullMd()); });
        $("#mdl").onclick = () => download(new Blob([`# ${P.title}\n\n${assemble()}`], { type: "text/markdown" }), (P.title || "manuscript") + ".md");
      };
      draw();
    },
  };
  paperWorkspace.go = (k) => views[k]();
  views[P.step || "setup"]();
}
function layoutSwitch(sel, wrap, ta, prev) {
  sel.onchange = () => { const v = sel.value; wrap.className = "editor-wrap" + (v === "split" ? "" : " single"); ta.style.display = v === "preview" ? "none" : ""; prev.style.display = v === "edit" ? "none" : ""; if (v !== "edit") prev.innerHTML = md(ta.value); };
}
function reviewHTML(r) {
  const rubric = S.meta.rubric;
  const sc = (k) => { const v = (r.scores || {})[k]; return typeof v === "object" ? v : { score: v, comment: "" }; };
  return `<div class="row" style="margin:14px 0;gap:18px"><div class="big-score" style="color:${r.passed ? "var(--ok)" : "var(--bad)"}">${r.total}<span class="muted" style="font-size:18px">/35</span></div>
    <div><span class="pill ${r.passed ? "ok" : "bad"}">${r.passed ? "GATE PASSED" : `BELOW ${S.meta.gate} — REVISE`}</span><div class="small muted" style="margin-top:4px">Verdict: ${esc(r.verdict || "")}</div></div></div>
    ${rubric.map(([k, label]) => { const s = sc(k); return `<div class="score-row"><div>${esc(label)}</div><div class="bar"><span style="width:${(+s.score || 0) * 20}%"></span></div><b>${s.score ?? "–"}/5</b></div>${s.comment ? `<div class="small muted" style="margin:-2px 0 8px 220px">${esc(s.comment)}</div>` : ""}`; }).join("")}
    <div class="grid g2" style="margin-top:12px"><div><b>Strengths</b><ul class="small">${(r.strengths || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div><div><b>Weaknesses</b><ul class="small">${(r.weaknesses || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div></div>
    <b>Required revisions</b><ol class="small">${(r.revisions || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ol>`;
}

// ================================================================== GAP FINDER
VIEWS.gaps = async () => {
  const past = await api(`/api/projects/${S.project.id}/documents?kind=gap`);
  main().innerHTML = `<div class="page-head"><div><h1>Gap Finder</h1><p>From your title or proposed research: break it into facets → measure what is already published for each combination → collect the closest prior work → write the <b>state of the art, research gaps (3–5 citations each), novelty statement, overlap risk and research questions</b>. Cost: 2 small fast-model calls + 1 writer call.</p></div></div>
  ${needLLM()}
  <div class="split"><div><div class="card">
    <label class="f">Title or proposed research <small>(more detail = sharper gaps)</small></label><textarea id="gt" rows="7" placeholder="e.g. Effect of monetite granule composition on setting time and wash-out resistance of injectable self-setting monetite granule–calcium sulfate cement">${esc(S.gapTopic || "")}</textarea>
    <div class="grid g2" style="gap:10px"><div><label class="f">Recent = since</label><input type="number" id="gry" value="${new Date().getFullYear() - 5}"></div>
    <div><label class="f">Language</label><select id="glang">${["English", "Indonesian"].map((l) => `<option ${l === S.settings.default_language ? "selected" : ""}>${l}</option>`).join("")}</select></div></div>
    <div class="row" style="margin-top:12px"><button class="btn primary" id="ggo">Find gaps</button></div></div>
    <div class="card"><h3>Previous analyses</h3>${past.map((d) => `<div class="list-item" data-g="${d.id}"><div class="t">${esc(d.title.replace("Gap analysis: ", ""))}</div><div class="small muted">${fmtDate(d.updated)}</div></div>`).join("") || `<div class="muted small">None yet.</div>`}</div></div>
    <div id="gres"><div class="card empty">Describe your research and click <b>Find gaps</b>.</div></div></div>`;
  S.gapTopic = "";
  const show = async (doc) => {
    const m = doc.meta;
    const cov = Object.entries(m.coverage || {});
    const maxC = Math.max(1, ...cov.map(([, v]) => v.total || 0));
    $("#gres").innerHTML = `<div class="card"><h3>1 · Facets of your research</h3>${m.facets.map((f) => `<div class="small"><b>${esc(f.name)}</b> <span class="pill gray">${esc(f.role || "")}</span> ${f.synonyms.map(esc).join(", ")}</div>`).join("")}
      ${m.contribution ? `<p class="small"><b>Intended contribution:</b> ${esc(m.contribution)}</p>` : ""}</div>
      <div class="card"><h3>2 · What is already published</h3><p class="small muted" style="margin-top:0">Number of papers in OpenAlex matching each facet and each combination (with synonyms). Low counts for a combination = a likely gap.</p>
      ${cov.map(([k, v]) => `<div class="score-row" style="grid-template-columns:minmax(160px,40%) 1fr 130px"><div class="small">${esc(k)}</div><div class="bar"><span style="width:${Math.max(2, Math.log10((v.total || 0) + 1) / Math.log10(maxC + 1) * 100)}%;background:${(v.total ?? 99) < 20 ? "var(--ok)" : "var(--accent-2)"}"></span></div><div class="small"><b>${v.total ?? "?"}</b>${v.recent != null ? ` · ${v.recent} recent` : ""}</div></div>`).join("")}</div>
      <div class="card"><h3>3 · Closest prior work (${m.table.length} of ${m.total_found || m.papers.length} found)</h3><div class="scroll" style="max-height:45vh"><table class="lib"><thead><tr><th>Paper</th><th>System</th><th>Variables</th><th>Method</th><th>Finding</th><th>Not studied</th></tr></thead><tbody>
      ${m.table.map((r) => { const p = m.papers[r.idx] || {}; return `<tr><td><span class="key">@${esc(p.key || "")}</span><div class="small">${esc((p.title || "").slice(0, 90))} (${r.year || ""})</div></td><td class="small">${esc(r.system)}</td><td class="small">${esc(r.variables)}</td><td class="small">${esc(r.method)}</td><td class="small">${esc(r.finding)}</td><td class="small">${esc(r.not_studied)}</td></tr>`; }).join("")}</tbody></table></div></div>
      <div class="card"><div class="row" style="justify-content:space-between"><h3 style="margin:0">4 · Gap, novelty & research questions</h3><div class="row"><button class="btn primary" id="gwrite">${doc.content ? "Rewrite" : "Write it"}</button>${doc.content ? `<a class="btn" href="#docs/${doc.id}">Open in editor</a>` : ""}</div></div>
      <div class="prose" id="gout" style="margin-top:10px">${doc.content ? md(doc.content) : `<div class="muted small">The writer model drafts the text from the facts above, citing only these papers. Cited papers are added to your library.</div>`}</div></div>`;
    $("#gwrite").onclick = (e) => busy(e.currentTarget, async () => {
      const txt = await streamText(`/api/gaps/${doc.id}/write`, {}, (f) => { $("#gout").innerHTML = md(f); });
      if (!txt.trim()) return;
      const r = await api(`/api/gaps/${doc.id}/finalize`, { body: { content: txt } }); await refreshLibKeys();
      toast(`Saved to Documents · ${r.added} cited papers added to library`, "ok");
      show(await api(`/api/documents/${doc.id}`));
    });
  };
  $$("[data-g]").forEach((el) => el.onclick = async () => show(await api(`/api/documents/${el.dataset.g}`)));
  $("#ggo").onclick = (e) => busy(e.currentTarget, async () => {
    const topic = $("#gt").value.trim(); if (!topic) return toast("Describe your research first");
    $("#gres").innerHTML = `<div class="card empty">Breaking down your research, counting published work for every facet combination, and collecting the closest papers… (about 30–60 s)</div>`;
    const r = await api("/api/gaps/analyze", { body: { topic, project_id: S.project.id, recent_from: +$("#gry").value || null, language: $("#glang").value } });
    show(await api(`/api/documents/${r.doc_id}`));
  });
};

// ================================================================== TOKEN USAGE
VIEWS.usage = async () => {
  main().innerHTML = `<div class="page-head"><div><h1>Token Usage</h1><p>Every AI call is logged locally with its task, model, tokens and cost. Exact counts are used when the provider reports them; otherwise they are estimated (~4 characters per token).</p></div>
    <select id="ud" style="width:160px"><option value="1">Last 24 hours</option><option value="7">Last 7 days</option><option value="30" selected>Last 30 days</option><option value="0">All time</option></select></div><div id="ubody"></div>`;
  const draw = async () => {
    const r = await api(`/api/usage?days=${$("#ud").value}`); const t = r.total;
    const est = r.recent.some((x) => x.estimated);
    const tbl = (rows, label) => `<table class="lib"><thead><tr><th>${label}</th><th>Calls</th><th>Input</th><th>Output</th><th>Cached</th><th>Cost</th></tr></thead><tbody>${rows.map((x) => `<tr><td>${esc(x.k)}</td><td>${x.calls}</td><td>${fmtTok(x.input)}</td><td>${fmtTok(x.output)}</td><td>${fmtTok(x.cached)}</td><td>${x.cost ? "$" + x.cost.toFixed(4) : "–"}</td></tr>`).join("") || `<tr><td colspan="6" class="muted">No calls yet</td></tr>`}</tbody></table>`;
    const maxDay = Math.max(1, ...r.daily.map((d) => d.input + d.output));
    $("#ubody").innerHTML = `<div class="card"><span class="metric"><b>${t.calls}</b><span>AI calls</span></span><span class="metric"><b>${fmtTok(t.input)}</b><span>input tokens</span></span><span class="metric"><b>${fmtTok(t.output)}</b><span>output tokens</span></span>
        <span class="metric" title="Input tokens served from the prompt cache (billed ~10%)"><b>${fmtTok(t.cached)}</b><span>cached input</span></span><span class="metric"><b>${t.cost ? "$" + t.cost.toFixed(4) : "–"}</b><span>cost (reported)</span></span>
        ${est ? `<div class="small muted">Some calls show estimated counts because the provider didn’t report usage.</div>` : ""}${!t.cost && t.calls ? `<div class="small muted">Cost is reported automatically by OpenRouter. For other providers, multiply tokens by your model’s price.</div>` : ""}</div>
      <div class="grid g2"><div class="card"><h3>By task</h3>${tbl(r.by_task, "Task")}</div><div class="card"><h3>By model</h3>${tbl(r.by_model, "Model")}</div></div>
      <div class="card"><h3>Per day</h3>${r.daily.map((d) => `<div class="score-row" style="grid-template-columns:110px 1fr 160px"><div class="small">${d.k}</div><div class="bar"><span style="width:${(d.input + d.output) * 100 / maxDay}%"></span></div><div class="small">${fmtTok(d.input + d.output)}${d.cost ? ` · $${d.cost.toFixed(3)}` : ""}</div></div>`).join("") || `<div class="muted">No data</div>`}</div>
      <div class="card"><h3>Recent calls</h3><div class="scroll" style="max-height:40vh"><table class="lib"><thead><tr><th>Time</th><th>Task</th><th>Model</th><th>In</th><th>Out</th><th>Cached</th><th>Cost</th></tr></thead><tbody>
        ${r.recent.map((x) => `<tr><td class="small">${fmtDate(x.ts)}</td><td>${esc(x.task)}</td><td class="small">${esc(x.model)}</td><td>${fmtTok(x.input)}${x.estimated ? "*" : ""}</td><td>${fmtTok(x.output)}${x.estimated ? "*" : ""}</td><td>${fmtTok(x.cached)}</td><td>${x.cost ? "$" + x.cost.toFixed(4) : "–"}</td></tr>`).join("")}</tbody></table></div></div>
      <div class="card"><h3>How to spend fewer tokens</h3><ul class="small" style="margin:0;padding-left:18px">
        <li>Set a cheap <b>Fast model</b> in Settings: query planning, source extraction, citation suggestions and PDF metadata then use it, while writing uses your main model.</li>
        <li>Use Claude models (directly or via OpenRouter) for the Paper Writer: the shared manuscript context is <b>prompt-cached</b> across sections (billed ~10%).</li>
        <li>Literature Search, quality filters, snowballing, Journal finder, Similarity and Quality checks use <b>no AI tokens</b>.</li>
        <li>In Deep Research, breadth 2–3 × depth 2 is usually enough; the quality filter removes weak sources before they reach the AI.</li></ul></div>`;
  };
  $("#ud").onchange = draw; draw();
};

// ================================================================== SETTINGS
const PRESETS = {
  openrouter: { provider: "openai", base: "https://openrouter.ai/api/v1", model: "anthropic/claude-sonnet-5.5", fast: "google/gemini-3.8-flash", note: "Recommended: one key, every model, exact cost tracking. openrouter.ai/keys" },
  anthropic: { provider: "anthropic", base: "https://api.anthropic.com", model: "claude-sonnet-5-5", fast: "claude-haiku-4-5-20251001", note: "console.anthropic.com" },
  openai: { provider: "openai", base: "https://api.openai.com/v1", model: "gpt-5.6-terra", fast: "gpt-5.6-luna", note: "platform.openai.com/api-keys" },
  gemini: { provider: "openai", base: "https://generativelanguage.googleapis.com/v1beta/openai", model: "gemini-3.8-flash", fast: "gemini-3.5-flash-lite", note: "aistudio.google.com/apikey" },
  deepseek: { provider: "openai", base: "https://api.deepseek.com/v1", model: "deepseek-chat", fast: "", note: "platform.deepseek.com" },
  groq: { provider: "openai", base: "https://api.groq.com/openai/v1", model: "llama-3.3-70b-versatile", fast: "", note: "console.groq.com" },
  ollama: { provider: "openai", base: "http://localhost:11434/v1", model: "qwen2.5:14b", fast: "", note: "Local & free: install Ollama, then run `ollama pull qwen2.5:14b`. No key needed." },
  lmstudio: { provider: "openai", base: "http://localhost:1234/v1", model: "local-model", fast: "", note: "Local: start the LM Studio server. No key needed." },
};
VIEWS.settings = async () => {
  const s = S.settings = await api("/api/settings");
  main().innerHTML = `<div class="page-head"><div><h1>Settings</h1><p>Stored locally in <code>data/settings.json</code>. Nothing leaves your computer except calls to the AI and search providers you choose.</p></div></div>
  <div class="grid g2">
    <div class="card"><h3>AI connection 1</h3>
      <label class="f">Quick preset</label><select id="preset"><option value="">— choose a provider —</option>${Object.keys(PRESETS).map((k) => `<option value="${k}">${k}</option>`).join("")}</select>
      <div class="small muted" id="pnote" style="margin-top:4px"></div>
      <label class="f">API type</label><select id="s_llm_provider"><option value="openai" ${s.llm_provider === "openai" ? "selected" : ""}>OpenAI-compatible</option><option value="anthropic" ${s.llm_provider === "anthropic" ? "selected" : ""}>Anthropic (Claude)</option></select>
      <label class="f">Base URL</label><input type="text" id="s_llm_base_url" value="${esc(s.llm_base_url)}">
      <label class="f">API key</label><input type="password" id="s_llm_api_key" value="${esc(s.llm_api_key)}" autocomplete="off">
      <h3 style="margin-top:18px">AI connection 2 <small class="muted">(optional, e.g. DeepSeek direct next to OpenRouter)</small></h3>
      <div class="row"><button class="btn small" id="c2ds">Fill in DeepSeek</button><button class="btn small ghost" id="c2clear">Clear</button></div>
      <label class="f">API type</label><select id="s_llm2_provider"><option value="openai" ${s.llm2_provider !== "anthropic" ? "selected" : ""}>OpenAI-compatible</option><option value="anthropic" ${s.llm2_provider === "anthropic" ? "selected" : ""}>Anthropic (Claude)</option></select>
      <label class="f">Base URL</label><input type="text" id="s_llm2_base_url" value="${esc(s.llm2_base_url || "")}" placeholder="e.g. https://api.deepseek.com">
      <label class="f">API key</label><input type="password" id="s_llm2_api_key" value="${esc(s.llm2_api_key || "")}" autocomplete="off">
      <div class="grid g2" style="gap:0 12px"><div><label class="f">Temperature</label><input type="number" step="0.1" id="s_llm_temperature" value="${s.llm_temperature}"></div>
      <div><label class="f">Max output tokens</label><input type="number" id="s_llm_max_tokens" value="${s.llm_max_tokens}"></div></div>
      <div class="row" style="margin-top:12px"><button class="btn primary" id="save1">Save</button></div>
    </div>
    <div>
      <div class="card"><h3>Appearance</h3><label class="f">Logo &amp; heading style</label><div class="logo-pick" id="logos"></div><label class="f">Colour theme</label><div class="themes" id="themes"></div></div>
      <div class="card"><h3>Which model does which job</h3>
        <p class="small muted" style="margin-top:0">Lists are loaded live from your providers (refreshed every 6 hours), so new models appear automatically. ★ marks the current recommendation. Prices are USD per 1M tokens (input / output).</p>
        <div class="row"><b class="small">One-click combination:</b><button class="btn small" data-combo="balanced">Balanced (recommended)</button><button class="btn small" data-combo="budget">Budget</button><button class="btn small" data-combo="quality">Max quality</button><button class="btn small ghost" id="mrefresh">↻ Refresh lists</button></div>
        <div id="roles"><div class="muted small" style="margin-top:10px">Loading model lists…</div></div>
        <div class="row" style="margin-top:12px"><button class="btn primary" id="save3">Save</button><button class="btn" id="test">Test all models</button></div><div id="tres" class="small" style="margin-top:6px"></div>
      </div>
      <div class="card"><h3>Search providers</h3>
        <p class="muted small">OpenAlex, Semantic Scholar, arXiv and Crossref work without keys.</p>
        <label class="f">Tavily API key <small>(web search — tavily.com, free tier)</small></label><input type="password" id="s_tavily_api_key" value="${esc(s.tavily_api_key)}">
        <label class="f">Semantic Scholar API key(s) <small>(optional; paste several separated by commas: A.F.R.A rotates them, about 1 request/s each)</small></label><input type="password" id="s_semantic_scholar_api_key" value="${esc(s.semantic_scholar_api_key)}">
        <label class="f">Zotero user ID <small>(optional, for online sync; from zotero.org/settings/keys)</small></label><input type="text" id="s_zotero_user_id" value="${esc(s.zotero_user_id || "")}">
        <label class="f">Zotero API key <small>(tick “Allow library access” + “Allow write access”)</small></label><input type="password" id="s_zotero_api_key" value="${esc(s.zotero_api_key || "")}">
        <label class="f">Contact email <small>(optional — faster “polite pool” on OpenAlex/Crossref)</small></label><input type="email" id="s_contact_email" value="${esc(s.contact_email)}">
        <label class="f">Default sources</label><div class="checks" id="s_srcs">${Object.entries(S.meta.sources).map(([k, v]) => `<label><input type="checkbox" value="${k}" ${s.default_sources.includes(k) ? "checked" : ""}>${esc(v)}</label>`).join("")}</div>
      </div>
      <div class="card"><h3>Writing defaults</h3>
        <div class="grid g3" style="gap:0 12px">
        <div><label class="f">Language</label><select id="s_default_language">${["English", "Indonesian"].map((l) => `<option ${l === s.default_language ? "selected" : ""}>${l}</option>`).join("")}</select></div>
        <div><label class="f">Citation style</label><select id="s_default_citation_style">${Object.entries(S.meta.styles).map(([k, v]) => `<option value="${k}" ${k === s.default_citation_style ? "selected" : ""}>${v}</option>`).join("")}</select></div>
        <div><label class="f">DOCX template</label><select id="s_default_template">${Object.entries(S.meta.templates).map(([k, v]) => `<option value="${k}" ${k === s.default_template ? "selected" : ""}>${esc(v.split("(")[0])}</option>`).join("")}</select></div></div>
        <div class="row" style="margin-top:12px"><button class="btn primary" id="save2">Save</button></div>
      </div>
      <div class="card"><h3>Journal quartiles (SJR)</h3>
        <p class="small muted" style="margin-top:0">${S.meta.sjr_journals ? `<b style="color:var(--ok)">✓ ${S.meta.sjr_journals.toLocaleString()} journals loaded.</b> ` : ""}For official Q1–Q4 quartiles: open <a href="https://www.scimagojr.com/journalrank.php" target="_blank">scimagojr.com/journalrank.php</a>, click <b>Download data</b> (top right), then choose that file here. Do this once a year. Without it, quartiles are estimated from OpenAlex.</p>
        <label class="btn">Import SCImago file (.csv)<input type="file" id="sjrf" accept=".csv,.txt" hidden></label></div>
      <div class="card"><h3>Updates</h3><div id="upd" class="small muted">Checking…</div></div>
      <div class="card"><h3>Your data & backups</h3><div id="bk" class="small muted">Loading…</div></div>
      <div class="card"><h3>Project</h3><label class="f">Name</label><input type="text" id="pname" value="${esc(S.project.name)}">
        <div class="row" style="margin-top:10px"><button class="btn" id="prename">Rename</button><button class="btn danger" id="pdel">Delete project…</button></div></div>
    </div>
  </div>`;
  const ROLE_KEYS = { main: ["llm_model", "llm_model_src"], fast: ["llm_fast_model", "llm_fast_src"], review: ["llm_review_model", "llm_review_src"] };
  let cats = { 1: [], 2: [] }, rec = null;
  const priceTxt = (m) => m && m.in != null ? ` — $${m.in}/$${m.out}` : "";
  const setRole = (role, src, model) => {
    const sel = $("#role_" + role); if (!sel) return;
    const v = model ? `${src}|${model}` : "";
    if (v && ![...sel.options].some((o) => o.value === v)) sel.insertAdjacentHTML("beforeend", `<option value="${esc(v)}">${esc(model)} (connection ${src})</option>`);
    sel.value = v; sel.dispatchEvent(new Event("change"));
  };
  const drawRoles = () => {
    const star = new Set(rec ? Object.values(rec.combos).flatMap((c) => Object.values(c).map((x) => `${x.src}|${x.model}`)) : []);
    const host = (src) => { try { return new URL(src === "2" ? $("#s_llm2_base_url").value : $("#s_llm_base_url").value).host; } catch { return "connection " + src; } };
    const opts = (role) => {
      const recOpts = rec ? ["balanced", "budget", "quality"].map((k) => rec.combos[k][role]).filter(Boolean) : [];
      const seen = new Set();
      const recHTML = recOpts.filter((x) => !seen.has(x.src + x.model) && seen.add(x.src + x.model)).map((x) => `<option value="${esc(x.src + "|" + x.model)}">★ ${esc(x.model)}${priceTxt(x)}</option>`).join("");
      return `<option value="">${role === "main" ? "— choose —" : "Same as writer"}</option>${recHTML ? `<optgroup label="★ Recommended">${recHTML}</optgroup>` : ""}` +
        ["1", "2"].filter((k) => cats[k].length).map((k) => `<optgroup label="Connection ${k}: ${esc(host(k))} (${cats[k].length} models)">${cats[k].map((m) => `<option value="${esc(k + "|" + m.id)}">${star.has(k + "|" + m.id) ? "★ " : ""}${esc(m.id)}${priceTxt(m)}</option>`).join("")}</optgroup>`).join("") +
        `<option value="__custom">Type a model name…</option>`;
    };
    $("#roles").innerHTML = Object.entries(rec ? rec.roles : { main: "Writer", fast: "Fast", review: "Reviewer" }).map(([role, label]) => `
      <label class="f">${esc(label)}</label><select id="role_${role}">${opts(role)}</select>
      <input type="text" id="rolec_${role}" placeholder="exact model id" style="display:none;margin-top:4px">
      ${rec ? `<div class="small muted">${esc(rec.why[role])}</div>` : ""}`).join("") +
      `<details style="margin-top:10px"><summary class="small">What each combination costs and why</summary><table class="lib" style="margin-top:6px"><thead><tr><th></th><th>Writer</th><th>Fast</th><th>Reviewer</th></tr></thead><tbody>
       ${rec ? ["balanced", "budget", "quality"].map((k) => `<tr><td><b>${k}</b></td>${["main", "fast", "review"].map((r) => { const x = rec.combos[k][r]; return `<td class="small">${x ? esc(x.model) + priceTxt(x) : "–"}</td>`; }).join("")}</tr>`).join("") : ""}</tbody></table>
       <p class="small muted">Typical spend with <b>Balanced</b>: Deep Research run ≈ $0.05–0.20, Discover ≈ $0.001, Evidence ≈ $0.01, a drafted paper section ≈ $0.02–0.06 (cached context). The Token Usage page shows your real numbers.</p></details>`;
    for (const role of Object.keys(ROLE_KEYS)) {
      const [mk, sk] = ROLE_KEYS[role];
      const sel = $("#role_" + role), cust = $("#rolec_" + role);
      sel.onchange = () => { cust.style.display = sel.value === "__custom" ? "" : "none"; };
      setRole(role, S.settings[sk] || "1", S.settings[mk] || "");
    }
  };
  const loadModels = async (refresh) => {
    const q = refresh ? "&refresh=1" : "";
    const [a, b] = await Promise.all([api(`/api/models?src=1${q}`), $("#s_llm2_base_url").value && $("#s_llm2_api_key").value ? api(`/api/models?src=2${q}`) : { models: [] }]);
    cats = { 1: a.models, 2: b.models };
    if (a.error) toast("Connection 1 model list: " + a.error, "bad"); if (b.error) toast("Connection 2 model list: " + b.error, "bad");
    try { rec = await api("/api/models/recommend"); } catch { rec = null; }
    drawRoles();
  };
  $("#mrefresh").onclick = (e) => busy(e.currentTarget, async () => { await doSave(true); await loadModels(true); });
  $$("[data-combo]").forEach((b) => b.onclick = () => {
    if (!rec) return toast("Model lists not loaded yet");
    const c = rec.combos[b.dataset.combo]; for (const role of Object.keys(ROLE_KEYS)) { const x = c[role]; if (x) setRole(role, x.src, x.model); }
    toast(`${b.textContent} combination selected — click Save`, "ok");
  });
  $("#c2ds").onclick = () => { $("#s_llm2_provider").value = "openai"; $("#s_llm2_base_url").value = "https://api.deepseek.com"; $("#s_llm2_api_key").focus(); toast("Paste your DeepSeek API key, then Save"); };
  $("#c2clear").onclick = () => { $("#s_llm2_base_url").value = ""; $("#s_llm2_api_key").value = ""; };
  $("#preset").onchange = (e) => { const p = PRESETS[e.target.value]; if (!p) return; $("#s_llm_provider").value = p.provider; $("#s_llm_base_url").value = p.base; $("#pnote").textContent = p.note; setRole("main", "1", p.model); setRole("fast", "1", p.fast); };
  const collect = () => {
    const out = {}; $$("[id^=s_]").forEach((el) => { if (el.id === "s_srcs") return; const k = el.id.slice(2); out[k] = el.type === "number" ? +el.value : el.value; });
    out.default_sources = $$("#s_srcs input:checked").map((x) => x.value);
    for (const [role, [mk, sk]] of Object.entries(ROLE_KEYS)) {
      const sel = $("#role_" + role); if (!sel) continue;
      if (sel.value === "__custom") { out[mk] = $("#rolec_" + role).value.trim(); out[sk] = "1"; }
      else { const [src, ...rest] = sel.value.split("|"); out[mk] = rest.join("|"); out[sk] = src || "1"; }
    }
    return out;
  };
  const doSave = async (quiet) => { S.settings = await api("/api/settings", { method: "PUT", body: collect() }); renderLLMStatus(); if (quiet !== true) toast("Settings saved", "ok"); };
  $("#save1").onclick = async () => { await doSave(); loadModels(true); };
  $("#save2").onclick = $("#save3").onclick = doSave;
  $("#test").onclick = (e) => busy(e.currentTarget, async () => {
    await doSave(true); $("#tres").textContent = "";
    try {
      const r = await api("/api/settings/test", { method: "POST" });
      $("#tres").innerHTML = ["main", "fast", "review"].filter((k) => r[k]).map((k) => `<div style="color:${r[k].startsWith("ERROR") ? "var(--bad)" : "var(--ok)"}">${r[k].startsWith("ERROR") ? "✗" : "✓"} ${{ main: "Writer", fast: "Fast", review: "Reviewer" }[k]}: ${esc(r[k])}</div>`).join("");
    } catch (err) { $("#tres").innerHTML = `<span style="color:var(--bad)">✗ ${esc(err.message)}</span>`; }
  });
  loadModels(false).catch((e) => { $("#roles").innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; });
  $("#sjrf").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return; toast("Importing journal rankings…");
    const fd = new FormData(); fd.append("file", f);
    try { const r = await api("/api/quality/sjr", { body: fd }); S.meta = await api("/api/meta"); toast(`Loaded ${r.journals.toLocaleString()} journals`, "ok"); VIEWS.settings(); } catch (err) { toast(err.message, "bad"); }
  };
  const loadBk = async () => {
    const b = await api("/api/backups");
    $("#bk").innerHTML = `Everything is stored on this computer in:<br><code style="word-break:break-all">${esc(b.db)}</code><br>Changes are saved automatically. A full backup is made every 6 hours and before deleting anything (newest 20 kept in <code style="word-break:break-all">${esc(b.dir)}</code>).
      <div class="row" style="margin:8px 0"><button class="btn small primary" id="bknow">Back up now</button></div>
      ${b.backups.slice(0, 6).map((x) => `<div>${fmtDate(x.ts)} · ${(x.size / 1048576).toFixed(1)} MB · <a href="/api/backups/${encodeURIComponent(x.name)}">download</a></div>`).join("") || "No backups yet."}
      <div style="margin-top:6px">To restore: stop the app, replace <code>research.db</code> with a backup file (rename it to research.db), start again.</div>`;
    $("#bknow").onclick = (e) => busy(e.currentTarget, async () => { await api("/api/backups", { method: "POST" }); toast("Backup created", "ok"); loadBk(); });
  };
  loadBk();
  const drawThemes = () => {
    const cur = S.settings.ui_theme || "navy";
    $("#themes").innerHTML = THEMES.map(([k, name, c, c2]) => `<button class="theme-sw ${k === cur ? "on" : ""}" data-theme-pick="${k}"><div class="pv">${c2 ? `<i style="background:linear-gradient(135deg,${c[0]} 50%,${c2[0]} 50%)"></i><span style="background:linear-gradient(135deg,${c[1]} 50%,${c2[1]} 50%)"><b style="background:${c[2]};width:70%"></b><b style="background:${c2[2]};width:45%"></b></span>` : `<i style="background:${c[0]}"></i><span style="background:${c[1]}"><b style="background:${c[2]};width:70%"></b><b style="background:${c[2]};opacity:.35;width:45%"></b></span>`}</div><div class="nm">${esc(name)}</div></button>`).join("");
    $$("[data-theme-pick]").forEach((b) => b.onclick = async () => {
      applyTheme(b.dataset.themePick);
      S.settings = await api("/api/settings", { method: "PUT", body: { ui_theme: b.dataset.themePick } }); drawThemes();
    });
  };
  drawThemes();
  const drawLogos = () => {
    const cur = S.settings.ui_logo || "classic";
    $("#logos").innerHTML = LOGOS.map(([k, name]) => `<button class="${k === cur ? "on" : ""}" data-logo-pick="${k}"><img src="/static/logos/${k}.svg" alt=""><span style="font-family:${k === "soft" ? "Fraunces" : k === "editorial" ? "'DM Serif Display'" : "'Libre Baskerville'"},serif;font-size:14px">${esc(name)}</span></button>`).join("");
    $$("[data-logo-pick]").forEach((b) => b.onclick = async () => {
      applyLogo(b.dataset.logoPick);
      S.settings = await api("/api/settings", { method: "PUT", body: { ui_logo: b.dataset.logoPick, ui_logo_chosen: true } }); drawLogos();
    });
  };
  drawLogos();
  const drawUpd = (r, ver) => {
    const box = $("#upd"); if (!box) return;
    if (!r) { box.innerHTML = `Version ${esc(ver.version)}. Could not reach GitHub to check for updates (no internet?). <button class="btn small" id="updchk">Check again</button>`; }
    else if (ver.dev_copy) { box.innerHTML = `Version ${esc(ver.version)}. This is the developer copy: updates come from git, not from this button.`; }
    else if (r.available) { box.innerHTML = `<div style="color:var(--ink)">Version <b>${esc(r.latest)}</b> is available (you have ${esc(r.current)}).</div><div class="row" style="margin-top:8px"><button class="btn primary" id="updgo">⬆ Update A.F.R.A</button><button class="btn small" id="updchk">Check again</button></div><div style="margin-top:6px">Your library, documents, settings and keys are kept. The old version is backed up.</div>`; }
    else { box.innerHTML = `✓ You have the latest version (${esc(r.current)}). <button class="btn small" id="updchk">Check again</button>`; }
    const g = $("#updgo"); if (g) g.onclick = () => runUpdate(g);
    const c = $("#updchk"); if (c) c.onclick = (e) => busy(e.currentTarget, async () => drawUpd(await checkUpdates(true), ver));
  };
  api("/api/version").then(async (ver) => drawUpd(await checkUpdates(true), ver));
  $("#prename").onclick = async () => { await api(`/api/projects/${S.project.id}`, { method: "PUT", body: { name: $("#pname").value } }); await loadProjects(S.project.id); toast("Renamed", "ok"); };
  $("#pdel").onclick = async () => {
    if (S.projects.length < 2) return toast("Can't delete the only project", "bad");
    if (prompt(`Type the project name to delete it permanently (library, documents, runs):\n${S.project.name}`) !== S.project.name) return;
    await api(`/api/projects/${S.project.id}`, { method: "DELETE" }); localStorage.removeItem("rt_project"); await loadProjects(); location.hash = "#home";
  };
};

boot().catch((e) => { main().innerHTML = `<div class="notice bad">Failed to start: ${esc(e.message)}</div>`; });
