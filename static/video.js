// ================================================================== VIDEO SUMMARY
// Paste YouTube links (and add documents), get study notes with screenshots, review them, export them. The draft lives on the server
// (data/video/), so closing the page and coming back resumes it. Loaded after app.js and create.js; uses their helpers (api, modal, cwPickFolder, startWizard...).
const VD = { id: "", st: null, poll: 0, jobs: {}, batch: [], saveT: 0 };
const VD_NOTICE = "For your own study notes. Videos are downloaded only temporarily to take screenshots.";
const vdMin = (t) => { t = Math.max(0, Math.floor(t || 0)); const h = Math.floor(t / 3600), m = Math.floor(t % 3600 / 60), s = t % 60; return (h ? h + ":" + String(m).padStart(2, "0") : String(m).padStart(2, "0")) + ":" + String(s).padStart(2, "0"); };
const vdLink = (m, t) => m && m.video_id ? `https://youtu.be/${m.video_id}?t=${Math.floor(t || 0)}` : "";
const vdFrame = (st, f) => `/api/video/${st.id}/frame/${f.id}.jpg`;
const vdMat = (st, id) => st.materials.find((m) => m.id === id);
const vdMany = (st) => st.materials.filter((m) => m.status === "ok").length > 1;
const vdTitle = (st, id) => { const m = vdMat(st, id); return m ? ((m.meta && m.meta.title) || m.label) : "Source"; };

VIEWS.video = async () => {
  VD.poll++;  // stops the pollers of an earlier visit
  const id = location.hash.split("/")[1];
  return id ? vdOpen(id) : vdStart();
};

// ---- the start page
async function vdStart() {
  const [drafts, docs] = await Promise.all([api("/api/video").catch(() => []), api(`/api/projects/${S.project.id}/documents?kind=doc`).catch(() => [])]);
  main().innerHTML = `<div class="page-head"><div><h1>Video summary</h1><p>Paste YouTube links and get study notes with screenshots from the videos. Review them, then export to Word, Markdown or Google Docs.</p></div></div>
    <div class="notice info small">${VD_NOTICE} Playlists aren't supported, and a video needs captions (the CC button on YouTube).</div>
    ${needLLM()}
    ${drafts.length ? `<div class="card"><h3>Continue where you left off</h3>${drafts.slice(0, 8).map((d) => `<div class="list-item" style="cursor:default"><div class="row" style="justify-content:space-between">
      <div><div class="t">${esc(d.title)}</div><div class="small muted">${d.step === "review" || d.step === "done" ? "Ready to review" : d.step === "questions" ? "Waiting for your answers" : "Not finished"} · ${d.materials} source${d.materials === 1 ? "" : "s"} · ${ago(d.updated)}</div></div>
      <div class="row" style="gap:4px"><a class="btn small primary" href="#video/${d.id}">Open</a><button class="btn small ghost" data-vdel="${d.id}">Delete</button></div></div></div>`).join("")}</div>` : ""}
    <div class="card"><h3>What should be summarised?</h3>
      <label class="f">YouTube links <small>one per line; paste a whole list. Up to 10 sources in a run.</small></label>
      <textarea id="vlinks" rows="5" placeholder="https://www.youtube.com/watch?v=...&#10;https://youtu.be/..."></textarea>
      <label class="f">Documents too <small>optional: your A.F.R.A documents, or .txt .md .docx .pdf files</small></label>
      ${docs.length ? `<details style="margin-bottom:6px"><summary class="small">Pick A.F.R.A documents (<span id="vdn">0</span> picked)</summary><div class="scroll" style="max-height:160px;margin-top:6px">${docs.map((d) => `<label class="row small" style="gap:8px;margin:2px 0"><input type="checkbox" class="vdoc" value="${esc(d.id)}"> ${esc(d.title)}</label>`).join("")}</div></details>` : ""}
      <input type="file" id="vfiles" accept=".txt,.md,.markdown,.docx,.pdf" multiple>
      <div id="vmode" class="hidden" style="margin-top:10px"><b class="small">Several sources: how should the notes come out?</b>
        <label class="row small" style="gap:8px;margin:4px 0"><input type="radio" name="vmode" value="combined" checked> <span><b>One combined resume</b>: grouped by theme, every point tagged with its source</span></label>
        <label class="row small" style="gap:8px;margin:4px 0"><input type="radio" name="vmode" value="separate"> <span><b>A separate resume for each</b></span></label></div>
    </div>
    <div class="card"><h3>Options</h3>
      <div class="grid g2" style="gap:12px">
        <div><label class="f">How much detail</label><select id="vdetail"><option value="short">Short</option><option value="normal" selected>Normal</option><option value="detailed">Detailed</option></select></div>
        <div><label class="f">Language of the notes <small>empty = same as the source</small></label><input type="text" id="vlang" placeholder="e.g. English, Indonesian"></div>
        <div><label class="f">Screenshots</label><select id="vframes"><option value="ai" selected>At key moments (the AI picks)</option><option value="scenes">At scene changes</option><option value="interval">Every few minutes</option><option value="none">None</option></select></div>
        <div><label class="f">Most screenshots</label><input type="number" id="vmax" min="1" max="40" value="12"></div>
        <div id="veveryw" class="hidden"><label class="f">One every (minutes)</label><input type="number" id="vevery" min="1" max="30" value="2"></div>
      </div>
      <label class="row small" style="gap:8px;margin-top:10px"><input type="checkbox" id="vlinksopt" checked> Add timestamps that link to the exact second in the video</label>
      <label class="row small" style="gap:8px;margin-top:4px"><input type="checkbox" id="vask" checked> Ask me a few questions first if it helps (purpose, focus)</label>
      <div class="row" style="margin-top:14px"><button class="btn primary" id="vgo">Make the notes</button></div>
    </div>`;
  const count = () => {
    const links = new Set((($("#vlinks").value.match(/(?:youtu\.be\/|v=|shorts\/|embed\/|live\/)[\w-]{11}/g)) || [])).size;
    const picked = $$(".vdoc:checked").length, files = $("#vfiles").files.length;
    if ($("#vdn")) $("#vdn").textContent = picked;
    $("#vmode").classList.toggle("hidden", links + picked + files < 2);
    return links + picked + files;
  };
  main().oninput = main().onchange = (e) => { if (e.target.closest("#vlinks,#vfiles,.vdoc")) count(); if (e.target.id === "vframes") $("#veveryw").classList.toggle("hidden", e.target.value !== "interval"); };
  $$("[data-vdel]").forEach((b) => b.onclick = async () => { if (!confirm("Delete this draft and its screenshots?")) return; await api("/api/video/" + b.dataset.vdel, { method: "DELETE" }); vdStart(); });
  $("#vgo").onclick = (e) => busy(e.currentTarget, async () => {
    if (!count()) return toast("Paste a YouTube link or add a document first");
    const data = { project_id: S.project.id, url_text: $("#vlinks").value, doc_ids: $$(".vdoc:checked").map((c) => c.value), mode: (document.querySelector("input[name=vmode]:checked") || {}).value || "combined",
      options: { detail: $("#vdetail").value, language: $("#vlang").value, frames: $("#vframes").value, max_frames: +$("#vmax").value || 12, every: +$("#vevery").value || 2, links: $("#vlinksopt").checked, ask: $("#vask").checked } };
    const files = [...$("#vfiles").files];
    let r;
    if (files.length) { const fd = new FormData(); fd.append("data", JSON.stringify(data)); files.forEach((f) => fd.append("files", f)); r = await api("/api/video", { body: fd }); }
    else r = await api("/api/video", { body: data });
    const ids = r.drafts.map((d) => d.id);
    VD.batch = ids;
    await vdRun(ids[0], "", ids.slice(1));
    location.hash = "#video/" + ids[0];
  });
}

// Starts (or resumes) the job. In Claude mode the writing steps are handed to the Claude app: the page keeps a visible "Claude is on it" box.
async function vdRun(id, redo = "", batch = []) {
  const headers = { "content-type": "application/json" };
  let msg = "";
  if (claudeMode()) {
    const hid = Math.random().toString(36).slice(2, 10);
    headers["x-afra-handoff"] = hid;
    msg = `A.F.R.A task ${hid}: Video summary. Please do it (get_task, then finish_task). If finish_task says there is a next task, do that one too.`;
    VD.claude = { msg, url: claudeLink(msg) };
    copyText(msg);
  } else VD.claude = null;
  const r = await fetch(`/api/video/${id}/start`, { method: "POST", headers, body: JSON.stringify({ redo, batch }) });
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail; } catch { } throw new Error(m); }
  const j = await r.json();
  Object.assign(VD.jobs, j.jobs);
  if (VD.claude) openClaude(VD.claude.url);
}

// ---- one draft: progress, questions, review
async function vdOpen(id) {
  try { VD.st = await api("/api/video/" + id); } catch (e) { toast(e.message, "bad"); location.hash = "#video"; return; }
  VD.id = id;
  const st = VD.st, working = VD.jobs[id] && !["done", "error", "questions"].includes((VD.last || {}).state);
  if (st.summary && st.step !== "working" && !working) return vdReview();
  if (st.step === "questions" && !working) return vdQuestions();
  return vdProgress();
}

function vdHead(st, sub) {
  return `<div class="page-head"><div><h1>${esc(st.meta ? st.meta.title : "Video summary")}</h1><p>${sub || ""}</p></div><div class="row"><a class="btn small ghost" href="#video">All video summaries</a></div></div>`;
}

// A video no server or PC could fetch: open it on YouTube in your own browser, copy its transcript, paste it here.
function vdManual(m) {
  return `<details class="vd-manual" data-m="${m.id}" style="margin-top:6px"><summary><b>Do it by hand</b>: open the video yourself and paste its transcript</summary>
    <div class="small" style="margin:6px 0"><a class="btn small primary" target="_blank" rel="noopener" href="${esc(m.url)}">Open the video on YouTube</a>
      Under the video press <b>…more</b>, then <b>Show transcript</b>, select all the lines, copy, and paste them below. (You'll get the notes without screenshots.)</div>
    <textarea class="vman" rows="5" placeholder="0:00&#10;Welcome to the lecture…&#10;0:12&#10;Today we cover…"></textarea>
    <div class="row" style="margin-top:6px"><button class="btn small primary" data-vman="${m.id}">Use this transcript</button></div></details>`;
}
function vdBindManual(id) {
  $$("[data-vman]").forEach((b) => b.onclick = (e) => busy(e.currentTarget, async () => {
    const text = $(".vman", b.closest(".vd-manual")).value;
    await api(`/api/video/${id}/transcript`, { body: { m: b.dataset.vman, text } });
    await vdRun(id); vdProgress();
  }));
}

function vdRows(st) {
  return `<div class="card"><h3>Sources</h3>${st.materials.map((m) => `<div class="row small" style="align-items:flex-start;gap:8px;margin:5px 0">
    <span class="pill ${m.status === "ok" ? "ok" : m.status === "failed" ? "bad" : "gray"}">${m.status === "ok" ? "Read" : m.status === "failed" ? "Failed" : "Waiting"}</span>
    <div class="grow"><b>${esc(m.meta && m.meta.title || m.label)}</b>${m.kind === "doc" ? " (document)" : m.meta && m.meta.channel ? ` · ${esc(m.meta.channel)}` : ""}
      <div class="muted">${esc(m.note || "")}${m.frames_note ? " · " + esc(m.frames_note) : ""}</div>${m.status === "failed" && m.kind === "video" ? vdManual(m) : ""}</div></div>`).join("")}
    ${(st.problems || []).length ? `<div class="small muted" style="margin-top:6px">Skipped: ${st.problems.map(esc).join("; ")}</div>` : ""}</div>`;
}

async function vdProgress() {
  const id = VD.id, my = ++VD.poll;
  const draw = (st, j) => {
    const sibs = VD.batch.length > 1 ? `<div class="card"><h3>This run</h3>${VD.batch.map((b) => `<div class="small" style="margin:3px 0"><a href="#video/${b}">${b === id ? "<b>this one</b>" : "Open"}</a> ${VD.jobs[b] && (VD.sib || {})[b] ? esc(VD.sib[b]) : ""}</div>`).join("")}</div>` : "";
    main().innerHTML = vdHead(st, "Working on it. You can leave this page: the draft is saved and you can come back to it.") + `<div class="notice info small">${VD_NOTICE}</div>
      <div class="card">${j && j.state === "error" ? `<div class="notice bad"><b>It stopped.</b> ${esc(j.error || st.error)}</div><div class="row"><button class="btn primary" id="vresume">Try again</button><a class="btn" href="#video">Back</a></div>`
        : j ? `<div class="statusbar"><span class="spin"></span> ${esc(j.step)}</div>` : `<div class="row"><div class="grow small muted">${st.error ? esc(st.error) : "This summary isn't finished."}</div><button class="btn primary" id="vresume">Resume</button></div>`}
        ${j && j.state === "working" && VD.claude ? `<div class="small muted" style="margin-top:8px">Claude is doing the writing steps. Claude opened with the request filled in: press <b>Enter</b> there. <a class="btn small" href="${esc(VD.claude.url)}">Open Claude</a></div>` : ""}</div>` + vdRows(st) + sibs;
    const r = $("#vresume"); if (r) r.onclick = (e) => busy(e.currentTarget, async () => { await vdRun(id); vdProgress(); });
    vdBindManual(id);
  };
  for (; ;) {
    let st, j = null;
    try { st = await api("/api/video/" + id); const jid = VD.jobs[id]; if (jid) j = await api("/api/video/jobs/" + jid).catch(() => null); } catch (e) { main().innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; return; }
    if (my !== VD.poll || !main()) return;
    VD.last = j;
    for (const b of VD.batch) { if (VD.jobs[b]) { try { const bj = await api("/api/video/jobs/" + VD.jobs[b]); (VD.sib = VD.sib || {})[b] = bj.state === "done" ? "done" : bj.state === "error" ? "failed: " + bj.error : bj.state === "queued" ? "waiting" : "working"; } catch { } } }
    if (j && j.state === "questions") { delete VD.jobs[id]; return vdOpen(id); }
    if (j && j.state === "done") { delete VD.jobs[id]; VD.last = null; return vdOpen(id); }
    draw(st, j);
    if (j && j.state === "error") return;
    if (!j) return;
    await sleep(1200);
  }
}

// ---- the AI's questions (at most one round): chips, a free-text field, "You decide"
function vdQuestions() {
  const st = VD.st, id = VD.id;
  main().innerHTML = vdHead(st, "A few quick questions so the notes fit what you need. Skip any you like.") + `<div class="card"><div class="notice info small" style="margin-bottom:12px">The work is paused until you answer or skip.</div>
    ${st.questions.map((q) => `<div style="margin-bottom:16px" data-q="${q.id}"><b>${esc(q.question)}</b>
      <div class="row" style="gap:6px;margin:6px 0">${q.choices.map((c) => `<button type="button" class="btn small" data-chip="${esc(c)}">${esc(c)}</button>`).join("")}<button type="button" class="btn small ghost" data-chip="You decide">You decide</button></div>
      <input type="text" class="vqa" placeholder="Or write your own answer"></div>`).join("")}
    <div class="row"><button class="btn primary" id="vqgo">Continue</button><button class="btn ghost" id="vqskip">Skip the questions</button></div></div>` + vdRows(st);
  $$("[data-chip]").forEach((b) => b.onclick = () => { const box = b.closest("[data-q]"); $(".vqa", box).value = b.dataset.chip; $$("[data-chip]", box).forEach((x) => x.classList.toggle("primary", x === b)); });
  const go = (skip) => async (e) => busy(e.currentTarget, async () => {
    const answers = {}; if (!skip) $$("[data-q]").forEach((q) => { answers[q.dataset.q] = $(".vqa", q).value; });
    await api(`/api/video/${id}/answers`, { body: { answers } });
    await vdRun(id); vdProgress();
  });
  $("#vqgo").onclick = go(false); $("#vqskip").onclick = go(true);
}

// ---- review
function vdSecHtml(st, s, i, n) {
  const refs = (s.refs || []).map((r) => { const m = vdMat(st, r.m); if (!m) return ""; if (m.kind === "doc") return `<span class="pill gray">${esc(m.label)}</span>`; return st.options.links ? `<a class="pill" target="_blank" href="${vdLink(m, r.t)}">${vdMany(st) ? esc(vdTitle(st, r.m).slice(0, 30)) + " " : ""}${vdMin(r.t)}</a>` : ""; }).join(" ");
  const shots = s.frames.map((f) => `<div class="vd-shot${f.keep ? "" : " off"}" data-f="${f.id}"><img src="${vdFrame(st, f)}" alt="Frame at ${vdMin(f.t)}" loading="lazy">
    <div class="row small" style="gap:6px;margin-top:4px"><span class="muted">${vdMany(st) ? esc(vdTitle(st, f.m).slice(0, 24)) + " " : ""}${vdMin(f.t)}</span><button class="btn small ${f.keep ? "ghost" : "primary"}" data-keep="${f.id}">${f.keep ? "Remove" : "Keep"}</button>
    <button class="btn small" data-pick="${s.id}" data-rep="${f.id}" data-m="${f.m || s.m}" data-t="${f.t}">Pick another moment</button></div></div>`).join("");
  const vm = vdMat(st, s.m), canShot = vm && vm.kind === "video";
  return `<div class="card vd-sec" data-s="${s.id}"><div class="row" style="gap:6px"><input type="text" class="vtitle grow" value="${esc(s.title)}" style="font-weight:600">
    <button class="btn small ghost" data-up="${s.id}" ${i === 0 ? "disabled" : ""} title="Move up">↑</button><button class="btn small ghost" data-down="${s.id}" ${i === n - 1 ? "disabled" : ""} title="Move down">↓</button></div>
    <div class="row small" style="gap:6px;margin:6px 0">${refs}</div>
    <textarea class="vnotes" rows="${Math.min(12, Math.max(3, s.notes.split("\n").length + 1))}">${esc(s.notes)}</textarea>
    <div class="vd-shots">${shots}</div>
    <div class="row" style="margin-top:8px;gap:6px">${canShot ? `<button class="btn small" data-pick="${s.id}" data-rep="" data-m="${s.m}" data-t="${s.frame_at ?? s.start}">Add a screenshot</button>` : ""}<button class="btn small" data-rw="${s.id}">Rewrite with AI</button></div>
    <div class="vd-scrub hidden"></div></div>`;
}

function vdCollect() {
  const st = VD.st;
  return { title: $("#vdtitle").value, tldr: $("#vtldr").value, takeaways: $$(".vtk").map((r) => ({ text: $("input", r).value, t: +r.dataset.t, m: r.dataset.m })),
    sections: $$(".vd-sec").map((c) => ({ id: c.dataset.s, title: $(".vtitle", c).value, notes: $(".vnotes", c).value,
      frames: $$(".vd-shot", c).map((f) => ({ id: f.dataset.f, keep: !f.classList.contains("off") })) })) };
}
const vdSave = debounce(async () => { try { VD.st = await api("/api/video/" + VD.id, { method: "PUT", body: vdCollect() }); const s = $("#vdsaved"); if (s) s.textContent = "Saved"; } catch (e) { toast(e.message, "bad"); } }, 900);
const vdSaveNow = async () => { VD.st = await api("/api/video/" + VD.id, { method: "PUT", body: vdCollect() }); };

function vdReview() {
  const st = VD.st, id = VD.id, sm = st.summary, many = vdMany(st), video1 = st.materials.find((m) => m.kind === "video" && m.status === "ok");
  const tk = sm.takeaways.map((k) => { const m = vdMat(st, k.m); const tag = m && m.kind === "video" && st.options.links ? `<a class="pill" target="_blank" href="${vdLink(m, k.t)}">${many ? esc(vdTitle(st, k.m).slice(0, 24)) + " " : ""}${vdMin(k.t)}</a>` : m && m.kind === "doc" ? `<span class="pill gray">${esc(m.label.slice(0, 24))}</span>` : "";
    return `<div class="row vtk" data-t="${k.t}" data-m="${k.m || ""}" style="gap:6px;margin:4px 0">${tag}<input type="text" class="grow" value="${esc(k.text)}"></div>`; }).join("");
  main().innerHTML = vdHead(st, `${st.step === "done" ? "Finished. " : ""}Edit anything, then export. ${many ? "This is one combined resume of " + st.materials.filter((m) => m.status === "ok").length + " sources." : ""}`) + `
    <div class="notice info small">${VD_NOTICE}</div>
    ${st.frames_error ? `<div class="notice small">${esc(st.frames_error)} <button class="btn small" id="vredoframes">Get the screenshots again</button></div>` : ""}
    <div class="split-wide" style="grid-template-columns:minmax(0,1fr) minmax(0,340px)"><div>
      <div class="card"><label class="f">Title</label><input type="text" id="vdtitle" value="${esc(st.meta.title)}">
        <label class="f">Summary</label><textarea id="vtldr" rows="4">${esc(sm.tldr)}</textarea>
        <label class="f">Key takeaways</label>${tk}</div>
      ${sm.sections.map((s, i) => vdSecHtml(st, s, i, sm.sections.length)).join("")}
    </div><div>
      <div class="card" style="position:sticky;top:12px"><h3>Export</h3><div class="small muted" id="vdsaved" style="margin-bottom:6px"></div>
        <div class="row" style="flex-direction:column;align-items:stretch;gap:6px">
          <button class="btn primary" id="vxdocx">Download Word (.docx)</button>
          <button class="btn" id="vxzip">Download Markdown folder (.zip)</button>
          <button class="btn" id="vxtugas">Save to a Tugas folder…</button>
          <button class="btn" id="vxgoogle">Make a Google Doc</button>
          <button class="btn" id="vxdoc">Save as an A.F.R.A document</button>
          <button class="btn" id="vxppt">Use in Create into PPT</button>
        </div>
        <div class="small muted" style="margin-top:6px">Create into PPT gets the text of the notes (as an A.F.R.A document); the screenshots stay here and in the Word file.</div>
        <hr style="border:0;border-top:1px solid var(--line);margin:12px 0">
        ${st.options.frames !== "none" && video1 ? (st.video_cached ? `<div class="small muted">The video is kept for about ${Math.ceil(st.cache_left / 60)} more minutes so you can pick other moments, then it is deleted.</div>` : `<div class="small muted">The video has been deleted. To pick other moments, download it again.</div><button class="btn small" id="vredovideo" style="margin-top:6px">Get the video again</button>`) : ""}
        <div class="row" style="margin-top:10px"><button class="btn small" id="vredo">Write the notes again</button><button class="btn small ghost" id="vfinish">I'm done: delete the video</button></div>
      </div></div></div>`;
  const dirty = () => { const s = $("#vdsaved"); if (s) s.textContent = "Saving…"; vdSave(); };
  main().oninput = (e) => { if (e.target.closest("#vdtitle,#vtldr,.vtk,.vd-sec")) dirty(); };
  const redraw = async () => { VD.st = await api("/api/video/" + id); vdReview(); };
  // keep / remove a screenshot
  $$("[data-keep]").forEach((b) => b.onclick = async () => { const box = b.closest(".vd-shot"); const off = !box.classList.contains("off"); box.classList.toggle("off", off); await vdSaveNow(); await redraw(); });
  // reorder
  const move = (sid, d) => async () => { await vdSaveNow(); const ids = VD.st.summary.sections.map((s) => s.id); const i = ids.indexOf(sid); [ids[i], ids[i + d]] = [ids[i + d], ids[i]];
    VD.st = await api("/api/video/" + id, { method: "PUT", body: { sections: ids.map((x) => ({ id: x })) } }); vdReview(); };
  $$("[data-up]").forEach((b) => b.onclick = move(b.dataset.up, -1)); $$("[data-down]").forEach((b) => b.onclick = move(b.dataset.down, 1));
  // pick another moment: a scrubber that cuts a new frame out of the cached video
  $$("[data-pick]").forEach((b) => b.onclick = () => {
    const sec = b.closest(".vd-sec"), box = $(".vd-scrub", sec), m = vdMat(st, b.dataset.m), dur = (m.meta && m.meta.duration) || 3600;
    if (!st.video_cached) return toast("The video was deleted. Press “Get the video again” first.", "bad");
    box.classList.remove("hidden");
    box.innerHTML = `<div class="small muted">Slide to the moment, then take the screenshot.${st.options.links ? ` <a target="_blank" id="vpl" href="${vdLink(m, b.dataset.t)}">Watch it on YouTube</a>` : ""}</div>
      <div class="row" style="gap:8px"><input type="range" class="grow" min="0" max="${Math.max(1, dur - 1)}" value="${Math.floor(+b.dataset.t)}" id="vrng" style="min-width:140px"><b id="vtime">${vdMin(+b.dataset.t)}</b></div>
      <div class="row"><button class="btn small primary" id="vtake">Take the screenshot here</button><button class="btn small ghost" id="vcancel">Cancel</button></div>`;
    const rng = $("#vrng", box); rng.oninput = () => { $("#vtime", box).textContent = vdMin(+rng.value); const l = $("#vpl", box); if (l) l.href = vdLink(m, +rng.value); };
    $("#vcancel", box).onclick = () => { box.classList.add("hidden"); box.innerHTML = ""; };
    $("#vtake", box).onclick = (e) => busy(e.currentTarget, async () => { await vdSaveNow(); await api(`/api/video/${id}/frame`, { body: { section: b.dataset.pick, t: +rng.value, replace: b.dataset.rep, m: b.dataset.m } }); await redraw(); });
  });
  $$("[data-rw]").forEach((b) => b.onclick = async () => {
    const ins = prompt("How should this section change? (empty = clearer and more precise)", ""); if (ins === null) return;
    await busy(b, async () => { await vdSaveNow(); await api(`/api/video/${id}/rewrite`, { body: { section: b.dataset.rw, instruction: ins } }); await redraw(); });
  });
  const dl = (fmt, name) => (e) => busy(e.currentTarget, async () => { await vdSaveNow(); const r = await api(`/api/video/${id}/export?fmt=${fmt}`); download(await r.blob(), name); });
  const base = (VD.st.meta.title || "Video summary").replace(/[\\/:*?"<>|]+/g, "").slice(0, 80);
  $("#vxdocx").onclick = dl("docx", base + ".docx"); $("#vxzip").onclick = dl("zip", base + ".zip");
  $("#vxtugas").onclick = async (e) => {
    const folder = await cwPickFolder(null); if (folder == null) return;
    const fmt = confirm("OK = a Word file (.docx)\nCancel = a Markdown folder (note.md + frames/)") ? "docx" : "md";
    await busy(e.currentTarget, async () => { await vdSaveNow(); const r = await api(`/api/video/${id}/save`, { body: { folder, format: fmt } }); toast("Saved as " + r.path, "ok"); });
  };
  $("#vxgoogle").onclick = (e) => busy(e.currentTarget, async () => { await vdSaveNow(); const r = await api(`/api/video/${id}/google-doc`, { body: {} }); window.open(r.url, "_blank"); toast("Google Doc made", "ok"); });
  $("#vxdoc").onclick = (e) => busy(e.currentTarget, async () => { await vdSaveNow(); const r = await api(`/api/video/${id}/document`, { body: {} }); linkToast("Saved as an A.F.R.A document.", "#docs"); });
  $("#vxppt").onclick = (e) => busy(e.currentTarget, async () => { await vdSaveNow(); const r = await api(`/api/video/${id}/document`, { body: {} }); await startWizard({ mode: "doc", doc_id: r.doc_id }); });
  $("#vredo").onclick = async () => { if (!confirm("Write the notes again? Your edits and screenshots are replaced.")) return; await vdRun(id, "summary"); vdProgress(); };
  $("#vfinish").onclick = (e) => busy(e.currentTarget, async () => { await vdSaveNow(); await api(`/api/video/${id}/finish`, { body: {} }); toast("The video is deleted. The screenshots stay in your notes.", "ok"); await redraw(); });
  const rf = $("#vredoframes"); if (rf) rf.onclick = async () => { await vdRun(id, "frames"); vdProgress(); };
  const rv = $("#vredovideo"); if (rv) rv.onclick = async () => { await vdRun(id, "video"); vdProgress(); };
}
