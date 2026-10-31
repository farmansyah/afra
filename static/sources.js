// ================================================================== SOURCE PICKER
// One searchable checkbox list for "several sources at once": A.F.R.A documents, library papers, uploaded files and files or whole folders
// from Tugas, mixed. Used by Create into PPT (start page and Content step) and by Cheatsheet. Loaded after app.js, before create.js.
// srcPicker(host, {refs, files, extra, onChange}) -> {refs(), files(), set(refs)}. refs look like {type: "doc"|"paper"|"file"|"folder", id|path};
// files are File objects the caller uploads once it has a draft; extra are already-uploaded files [{id, name, remove()}] shown as chips.
const SRC = { cache: null, at: 0, pid: "" };
async function srcLoad(force) {
  if (!force && SRC.cache && SRC.pid === S.project.id && Date.now() - SRC.at < 15000) return SRC.cache;
  const [docs, papers, tg] = await Promise.all([api(`/api/projects/${S.project.id}/documents?kind=doc`).catch(() => []), api(`/api/projects/${S.project.id}/papers`).catch(() => []),
    api("/api/sources/tugas").catch(() => ({ files: [], folders: [] }))]);
  SRC.cache = { docs, papers, files: tg.files || [], folders: tg.folders || [] }; SRC.at = Date.now(); SRC.pid = S.project.id;
  return SRC.cache;
}
const srcKey = (r) => r.type + ":" + (r.type === "file" || r.type === "folder" ? r.path : r.id);
const SRC_GROUPS = [["doc", "A.F.R.A documents"], ["paper", "Library papers"], ["folder", "Tugas folders (every file inside)"], ["file", "Tugas files"]];
function srcRows(c) {
  return [...c.docs.map((d) => ({ ref: { type: "doc", id: d.id }, name: d.title || "Untitled", sub: "" })),
    ...c.papers.map((p) => ({ ref: { type: "paper", id: p.id }, name: p.title || "Untitled", sub: [authorsShort(p.authors), p.year].filter(Boolean).join(" ") })),
    ...c.folders.map((f) => ({ ref: { type: "folder", path: f.path }, name: f.path, sub: `${f.count} file${f.count === 1 ? "" : "s"}` })),
    ...c.files.map((f) => ({ ref: { type: "file", path: f.path }, name: f.name, sub: f.folder || "" }))];
}
async function srcPicker(host, opts = {}) {
  const c = await srcLoad(), rows = srcRows(c), byKey = new Map(rows.map((r) => [srcKey(r.ref), r]));
  const st = { refs: (opts.refs || []).slice(), files: (opts.files || []).slice(), q: "" };
  host.innerHTML = `<div class="sp"><div class="sp-chips"></div>
    <div class="row" style="margin:8px 0"><input type="text" class="sp-q grow" placeholder="Search your documents, papers and Tugas files…"><label class="btn small" style="cursor:pointer">Upload files<input type="file" class="sp-up" multiple hidden accept=".pptx,.pdf,.docx,.md,.markdown,.txt"></label></div>
    <div class="sp-list scroll"></div><div class="small muted" style="margin-top:4px">Lecture slides (.pptx), PDF, Word, notes (.md, .txt), A.F.R.A documents and papers. Picking a folder takes every supported file inside it (up to 40).</div></div>`;
  const chips = $(".sp-chips", host), list = $(".sp-list", host);
  const changed = () => { drawChips(); opts.onChange && opts.onChange(); };
  function drawChips() {
    const label = (r) => { const row = byKey.get(srcKey(r)); return row ? (r.type === "folder" ? row.name + "/" : row.name) : (r.type === "folder" ? (r.path || "All Tugas") + "/" : "(not found)"); };
    const all = [...st.refs.map((r, i) => `<span class="sp-chip" data-k="${i}" title="${esc(label(r))}"><i>${{ doc: "Doc", paper: "Paper", file: "File", folder: "Folder" }[r.type]}</i> ${esc(label(r).slice(0, 48))} <button data-rm="r${i}" title="Remove">×</button></span>`),
      ...(opts.extra || []).map((m, i) => `<span class="sp-chip"><i>Upload</i> ${esc(m.name.slice(0, 48))} <button data-rm="e${i}" title="Remove">×</button></span>`),
      ...st.files.map((f, i) => `<span class="sp-chip"><i>Upload</i> ${esc(f.name.slice(0, 48))} <button data-rm="f${i}" title="Remove">×</button></span>`)];
    chips.innerHTML = all.length ? all.join("") : `<span class="small muted">Nothing picked yet.</span>`;
    $$("[data-rm]", chips).forEach((b) => b.onclick = async () => {
      const k = b.dataset.rm, i = +k.slice(1);
      if (k[0] === "r") st.refs.splice(i, 1); else if (k[0] === "f") st.files.splice(i, 1); else if (opts.extra[i].remove) await opts.extra[i].remove();
      drawList(); changed();
    });
  }
  function drawList() {
    const q = st.q.trim().toLowerCase(), on = new Set(st.refs.map(srcKey));
    const html = SRC_GROUPS.map(([t, title]) => {
      const hit = rows.filter((r) => r.ref.type === t && (!q || (r.name + " " + r.sub).toLowerCase().includes(q)));
      if (!hit.length) return "";
      return `<div class="small muted sp-h">${title} <span>${hit.length}</span></div>` + hit.slice(0, 60).map((r) => `<label class="sp-row"><input type="checkbox" data-k="${esc(srcKey(r.ref))}" ${on.has(srcKey(r.ref)) ? "checked" : ""}>
        <span class="grow">${esc(r.name)}</span><span class="small muted">${esc(r.sub)}</span></label>`).join("") + (hit.length > 60 ? `<div class="small muted" style="padding:2px 8px">…${hit.length - 60} more: type to narrow it down.</div>` : "");
    }).join("");
    list.innerHTML = html || `<div class="small muted" style="padding:10px">${rows.length ? "Nothing matches." : "No documents, papers or Tugas files yet. Upload files instead."}</div>`;
    $$("input[data-k]", list).forEach((x) => x.onchange = () => {
      const row = byKey.get(x.dataset.k);
      if (x.checked) st.refs.push(row.ref); else st.refs = st.refs.filter((r) => srcKey(r) !== x.dataset.k);
      changed();
    });
  }
  $(".sp-q", host).oninput = debounce((e) => { st.q = e.target.value; drawList(); }, 150);
  $(".sp-up", host).onchange = (e) => { st.files.push(...e.target.files); e.target.value = ""; changed(); };
  drawChips(); drawList();
  return { refs: () => st.refs.slice(), files: () => st.files.slice(), clearFiles: () => { st.files = []; drawChips(); }, redraw: () => { drawChips(); drawList(); } };
}
// upload the picked File objects into a draft (kind: "wizard" | "cheatsheet"); returns the names that failed
async function srcUpload(kind, id, files) {
  const bad = [];
  for (const f of files) { const fd = new FormData(); fd.append("file", f); try { await api(`/api/${kind}/${id}/materials`, { body: fd }); } catch (e) { bad.push(`${f.name}: ${e.message}`); } }
  if (bad.length) toast(bad.join("\n"), "bad");
  return bad;
}
