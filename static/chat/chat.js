/* A.F.R.A ♥ chat: a private chat for two. Runs as the iPhone app (served by the relay on your VPS, installed to the
   Home Screen) and inside A.F.R.A on the computers. Messages are end-to-end encrypted with a key made from the pairing
   code (same scheme as app/chat.py): the relay only ever sees scrambled data.
   (c) 2026 danafarmansyah. All rights reserved. Crafted with love. */
(() => {
  "use strict";
  const LOCAL = location.pathname.startsWith("/static/chat");  // inside A.F.R.A: the local server does relay + backup
  const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const now = () => Date.now() / 1000;
  const te = new TextEncoder(), td = new TextDecoder();
  const toHex = (u8) => [...u8].map((b) => b.toString(16).padStart(2, "0")).join("");
  const randHex = (n) => toHex(crypto.getRandomValues(new Uint8Array(n)));
  const concat = (a, b) => { const o = new Uint8Array(a.length + b.length); o.set(a); o.set(b, a.length); return o; };
  const b64 = (u8) => { let s = ""; for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode(...u8.subarray(i, i + 0x8000)); return btoa(s); };
  const unb64 = (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)), (c) => c.charCodeAt(0));
  const ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const newCode = () => { const r = crypto.getRandomValues(new Uint8Array(16)); const c = [...r].map((x) => ALPHABET[x % 32]).join(""); return c.match(/.{4}/g).join("-"); };
  const normCode = (c) => (c || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  const isIOS = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  function toast(msg) { const t = document.createElement("div"); t.className = "toast"; t.textContent = msg; document.body.appendChild(t); setTimeout(() => t.remove(), 2600); }

  // ------------------------------------------------------------------ encryption (same as app/chat.py)
  async function deriveKeys(code) {
    const base = await crypto.subtle.importKey("raw", te.encode(normCode(code)), "PBKDF2", false, ["deriveBits"]);
    const master = new Uint8Array(await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt: te.encode("afra-chat-v1"), iterations: 200000 }, base, 256));
    const room = toHex(new Uint8Array(await crypto.subtle.digest("SHA-256", concat(master, te.encode("room"))))).slice(0, 32);
    const hk = await crypto.subtle.importKey("raw", master, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    const token = toHex(new Uint8Array(await crypto.subtle.sign("HMAC", hk, te.encode("token"))));
    const key = await crypto.subtle.importKey("raw", await crypto.subtle.sign("HMAC", hk, te.encode("enc")), "AES-GCM", false, ["encrypt", "decrypt"]);
    return { room, token, key };
  }
  async function seal(k, bytes) {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    return concat(iv, new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: te.encode(k.room) }, k.key, bytes)));
  }
  async function unseal(k, bytes) {
    return new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv: bytes.slice(0, 12), additionalData: te.encode(k.room) }, k.key, bytes.slice(12)));
  }

  // ------------------------------------------------------------------ phone storage (IndexedDB)
  const IDB = {
    db: null,
    open() {
      return this.db ? Promise.resolve(this.db) : new Promise((res, rej) => {
        const r = indexedDB.open("afra-chat", 1);
        r.onupgradeneeded = () => { const d = r.result; d.createObjectStore("kv"); d.createObjectStore("msgs", { keyPath: "id" }); d.createObjectStore("blobs"); d.createObjectStore("outbox", { autoIncrement: true }); };
        r.onsuccess = () => res(this.db = r.result); r.onerror = () => rej(r.error);
      });
    },
    async tx(store, mode, fn) { const d = await this.open(); return new Promise((res, rej) => { const t = d.transaction(store, mode); const s = t.objectStore(store); const out = fn(s); t.oncomplete = () => res(out && "result" in out ? out.result : out); t.onerror = () => rej(t.error); }); },
    get(store, key) { return this.tx(store, "readonly", (s) => s.get(key)); },
    put(store, val, key) { return this.tx(store, "readwrite", (s) => key === undefined ? s.put(val) : s.put(val, key)); },
    add(store, val) { return this.tx(store, "readwrite", (s) => s.add(val)); },
    del(store, key) { return this.tx(store, "readwrite", (s) => s.delete(key)); },
    async entries(store) {
      const d = await this.open();
      return new Promise((res) => { const out = []; const c = d.transaction(store).objectStore(store).openCursor(); c.onsuccess = () => { const cur = c.result; if (cur) { out.push([cur.key, cur.value]); cur.continue(); } else res(out); }; });
    },
  };

  // ------------------------------------------------------------------ transport 1: the phone app talks to the relay itself
  class RelayTransport {
    async init() {
      this.cfg = await IDB.get("kv", "cfg");
      if (!this.cfg) return false;
      this.keys = await deriveKeys(this.cfg.code);
      this.me = this.cfg.name; this.status = { relay: "connecting", partner_online: false, typing: [] };
      this.typingAt = {}; this.inflight = new Set(); this.backoff = 1500;
      this.connect();
      return true;
    }
    connect() {
      const k = this.keys, url = `${location.origin.replace(/^http/, "ws")}/v1/ws?room=${k.room}&token=${k.token}&device=${this.cfg.device}`;
      const ws = this.ws = new WebSocket(url);
      ws.onopen = async () => {
        this.backoff = 1500; this.status.relay = "live"; this.emit("status");
        ws.send(JSON.stringify({ t: "since", seq: (await IDB.get("kv", "seq")) || 0 }));
        this.inflight.clear(); this.flush(); this.registerPush(false);
      };
      ws.onmessage = (e) => this.receive(JSON.parse(e.data));
      ws.onclose = (e) => {
        this.status.relay = e.code === 4401 ? "denied" : "down"; this.status.partner_online = false; this.emit("status");
        if (e.code !== 4401) setTimeout(() => this.connect(), this.backoff); this.backoff = Math.min(this.backoff * 2, 30000);
      };
      clearInterval(this.pinger); this.pinger = setInterval(() => { if (ws.readyState === 1) ws.send('{"t":"ping"}'); }, 25000);
    }
    async receive(m) {
      if (m.t === "env") {
        try {
          const body = JSON.parse(td.decode(await unseal(this.keys, unb64(m.data))));
          if (body.row && await this.upsert(body.row)) this.emit("change");
        } catch { }
        if (m.seq) await IDB.put("kv", Math.max(m.seq, (await IDB.get("kv", "seq")) || 0), "seq");
      } else if (m.t === "stored") { await IDB.del("outbox", m.ref); this.inflight.delete(m.ref); this.emit("change"); }
      else if (m.t === "presence") { this.status.partner_online = (m.online || []).some((d) => d !== this.cfg.device); this.emit("status"); }
      else if (m.t === "eph") {
        try { const b = JSON.parse(td.decode(await unseal(this.keys, unb64(m.data)))); if (b.typing && b.typing !== this.me) { this.typingAt[b.typing] = now(); this.emit("status"); } } catch { }
      }
    }
    async upsert(row) {
      if (!row || !row.id) return false;
      const cur = await IDB.get("msgs", row.id);
      if (cur && (cur.updated || 0) >= (row.updated || 0)) return false;
      await IDB.put("msgs", row); return true;
    }
    getStatus() { const t = now(); this.status.typing = Object.entries(this.typingAt).filter(([, at]) => t - at < 6).map(([n]) => n); return this.status; }
    async list() { const rows = (await IDB.entries("msgs")).map(([, v]) => v); const out = new Set((await IDB.entries("outbox")).map(([, v]) => v.row.id)); rows.forEach((r) => r.pending = out.has(r.id)); return rows.sort((a, b) => a.created - b.created); }
    async saveSend(row, notify) { await IDB.put("msgs", row); await IDB.add("outbox", { row, notify }); this.emit("change"); this.flush(); return row; }
    async flush() {
      if (!this.ws || this.ws.readyState !== 1) return;
      for (const [ref, v] of await IDB.entries("outbox")) {
        if (this.inflight.has(ref)) continue; this.inflight.add(ref);
        const data = b64(await seal(this.keys, te.encode(JSON.stringify({ row: v.row }))));
        this.ws.send(JSON.stringify({ t: "env", ref, data, notify: !!v.notify }));
      }
    }
    send({ text = "", kind = "text", reply_to = "", attachment = null }) {
      const t = now();
      return this.saveSend({ id: randHex(8), author: this.me, device: this.cfg.device, kind, text, reply_to, reactions: {}, edited: 0, deleted: false, attachment, created: t, updated: t, read: 0 }, true);
    }
    async change(id, action, value) { const row = await IDB.get("msgs", id); if (!row) return; applyChange(row, action, value, this.me); row.updated = now(); return this.saveSend(row, false); }
    async markRead() {
      const t = now();
      for (const [, r] of await IDB.entries("msgs")) if (r.author !== this.me && !r.read && !r.deleted) { r.read = t; r.updated = t; await this.saveSend(r, false); }
    }
    async typing() {
      if (!this.ws || this.ws.readyState !== 1) return;
      this.ws.send(JSON.stringify({ t: "eph", data: b64(await seal(this.keys, te.encode(JSON.stringify({ typing: this.me })))) }));
    }
    async attach(file, bytes) {
      const blob = randHex(12);
      await IDB.put("blobs", bytes, blob);
      fetch(`/v1/blob/${this.keys.room}/${blob}`, { method: "PUT", headers: { "x-token": this.keys.token }, body: await seal(this.keys, bytes) }).catch(() => { });
      return { name: file.name || "file", mime: file.type || "application/octet-stream", size: bytes.length, blob };
    }
    async fileUrl(att) {
      let bytes = await IDB.get("blobs", att.blob);
      if (!bytes) {
        const r = await fetch(`/v1/blob/${this.keys.room}/${att.blob}`, { headers: { "x-token": this.keys.token } });
        if (!r.ok) return "";
        bytes = await unseal(this.keys, new Uint8Array(await r.arrayBuffer())); await IDB.put("blobs", bytes, att.blob);
      }
      return URL.createObjectURL(new Blob([bytes], { type: att.mime }));
    }
    async registerPush(ask) {
      if (!("serviceWorker" in navigator) || !("PushManager" in window)) return "unsupported";
      if (Notification.permission === "denied") return "denied";
      if (Notification.permission !== "granted") { if (!ask) return "off"; if (await Notification.requestPermission() !== "granted") return "denied"; }
      const reg = await navigator.serviceWorker.ready;
      let sub = await reg.pushManager.getSubscription();
      if (!sub) { const { key } = await (await fetch("/v1/vapid")).json(); sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: unb64(key) }); }
      if (this.ws && this.ws.readyState === 1) this.ws.send(JSON.stringify({ t: "webpush", sub: sub.toJSON() }));
      return "on";
    }
    async setName(n) { this.cfg.name = this.me = n; await IDB.put("kv", this.cfg, "cfg"); }
    async leave() {
      try { const sub = await (await navigator.serviceWorker.ready).pushManager.getSubscription(); if (sub) { if (this.ws && this.ws.readyState === 1) this.ws.send('{"t":"webpush"}'); await sub.unsubscribe(); } } catch { }
      if (this.ws) { this.ws.onclose = null; this.ws.close(); }
      if (IDB.db) IDB.db.close(); IDB.db = null;
      await new Promise((r) => { const q = indexedDB.deleteDatabase("afra-chat"); q.onsuccess = q.onerror = q.onblocked = r; });
    }
    emit(kind) { this.onChange && this.onChange(kind); }
  }

  // ------------------------------------------------------------------ transport 2: inside A.F.R.A, the local server does it all
  class LocalTransport {
    async api(path, body, method) {
      const r = await fetch(path, body instanceof FormData ? { method: "POST", body } : body !== undefined ? { method: method || "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) } : {});
      if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail; } catch { } throw new Error(m); }
      return r.json();
    }
    async init() {
      this.status = await this.api("/api/chat/status"); this.me = this.status.me;
      if (!this.status.paired) return false;
      const es = new EventSource("/api/chat/stream");
      es.onmessage = async (e) => { const ev = JSON.parse(e.data); if (ev.type === "status") { this.status = await this.api("/api/chat/status"); } this.emit(ev.type); };
      return true;
    }
    getStatus() { return { ...this.status, relay: this.status.relay === "live" ? "live" : this.status.relay_url ? "down" : "off" }; }
    async list() { const r = await this.api("/api/chat"); this.status = r; this.me = r.me; return r.messages; }
    send(m) { return this.api("/api/chat/send", m); }
    change(id, action, value) { return this.api(`/api/chat/${id}/${action}`, { value }); }
    markRead() { return this.api("/api/chat/read", {}); }
    typing() { return this.api("/api/chat/typing", {}).catch(() => { }); }
    async attach(file, bytes) { const fd = new FormData(); fd.append("file", new Blob([bytes], { type: file.type }), file.name || "file"); return this.api("/api/chat/attach", fd); }
    async fileUrl(att) { return `/api/chat/file/${att.blob}?mime=${encodeURIComponent(att.mime)}&name=${encodeURIComponent(att.name)}`; }
    async registerPush() { return "desktop"; }
    async setName(n) { await this.api("/api/chat/pair", { name: n }); this.me = n; }
    emit(kind) { this.onChange && this.onChange(kind); }
  }

  function applyChange(row, action, value, me) {
    if (action === "react") { const r = { ...(row.reactions || {}) }; if (!value || r[me] === value) delete r[me]; else r[me] = value; row.reactions = r; }
    else if (action === "edit") { row.text = value; row.edited = now(); }
    else if (action === "delete") { row.text = ""; row.attachment = null; row.deleted = true; row.reactions = {}; }
  }

  // ------------------------------------------------------------------ UI
  const T = LOCAL ? new LocalTransport() : new RelayTransport();
  const app = $("#app");
  let MSGS = [], replyTo = null, editing = null, search = "", TEMPLATES = null, lastTyping = 0, firstPaint = true;
  const REACTIONS = ["❤️", "🥰", "😂", "😮", "😢", "🙏", "👍", "🔥"];
  const EMOJIS = "❤️ 🥰 😘 😍 🤗 😊 😁 😂 🥹 😭 😢 🥺 😴 🤔 😮 😎 🙈 🙏 👍 👏 💪 🎉 ✨ 🔥 💯 🌹 🌸 🌙 ☀️ ☕ 🍜 🍦 📚 ✍️ 🎓 💌 💖 💕 💞 💗 🤍 💛 💙 🫶 👀 😅 🙃 😌".split(" ");

  const fmtTime = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  function dayLabel(t) {
    const d = new Date(t * 1000), today = new Date(), y = new Date(Date.now() - 864e5);
    if (d.toDateString() === today.toDateString()) return "Today";
    if (d.toDateString() === y.toDateString()) return "Yesterday";
    return d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short", year: d.getFullYear() === today.getFullYear() ? undefined : "numeric" });
  }
  const partnerName = () => (MSGS.slice().reverse().find((m) => m.author && m.author !== T.me) || {}).author || "Your love";
  const onlyEmoji = (s) => s && s.length <= 12 && /^(\p{Extended_Pictographic}|\p{Emoji_Component}|‍|️|\s)+$/u.test(s);
  const sizeTxt = (n) => n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB";

  function shell() {
    app.innerHTML = `
      <div class="top"><img src="icon-512.png" alt=""><div class="who"><b id="pname">A.F.R.A ♥</b><small id="pstat"></small></div>
        <button class="icon" id="bsearch" title="Search">🔍</button><button class="icon" id="bmenu" title="Menu">⋯</button></div>
      <div id="banner"></div>
      <div class="searchbar" id="sbar" hidden><input type="search" id="sq" placeholder="Search messages"><button class="cbtn" id="sclose">✕</button></div>
      <div class="log" id="log"></div>
      <div class="typing" id="typing"></div>
      <div id="replybar"></div>
      <div class="compose">
        <button class="cbtn" id="btpl" title="Love notes">💌</button>
        <button class="cbtn" id="bemoji" title="Emoji">😊</button>
        <textarea id="txt" rows="1" placeholder="Message"></textarea>
        <button class="cbtn" id="battach" title="Photo or file">📎</button>
        <button class="cbtn send" id="bsend" title="Send">🎤</button>
        <input type="file" id="file" hidden>
      </div>`;
    const txt = $("#txt");
    const grow = () => { txt.style.height = "auto"; txt.style.height = Math.min(txt.scrollHeight, 140) + "px"; $("#bsend").textContent = txt.value.trim() || editing ? "➤" : "🎤"; };
    txt.oninput = () => { grow(); if (txt.value && now() - lastTyping > 3) { lastTyping = now(); T.typing(); } };
    txt.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !matchMedia("(pointer: coarse)").matches) { e.preventDefault(); sendText(); } if (e.key === "Escape") { replyTo = editing = null; drawReply(); } };
    $("#bsend").onclick = () => (txt.value.trim() || editing) ? sendText() : toggleVoice();
    $("#battach").onclick = () => $("#file").click();
    $("#file").onchange = (e) => { const f = e.target.files[0]; e.target.value = ""; if (f) sendFile(f); };
    $("#btpl").onclick = openTemplates;
    $("#bemoji").onclick = openEmoji;
    $("#bmenu").onclick = openMainMenu;
    $("#bsearch").onclick = () => { $("#sbar").hidden = false; $("#sq").focus(); };
    $("#sclose").onclick = () => { $("#sbar").hidden = true; search = ""; $("#sq").value = ""; draw(); };
    $("#sq").oninput = (e) => { search = e.target.value.trim().toLowerCase(); draw(); };
    grow.call(); txt.addEventListener("focus", () => setTimeout(() => scrollEnd(true), 300));
    window.growComposer = grow;
  }

  function scrollEnd(force) { const log = $("#log"); if (force || log.scrollHeight - log.scrollTop - log.clientHeight < 140) log.scrollTop = log.scrollHeight; }

  async function refresh() {
    MSGS = await T.list();
    draw();
    if (document.visibilityState === "visible" && MSGS.some((m) => m.author !== T.me && !m.read && !m.deleted)) {
      await T.markRead();
      if (navigator.clearAppBadge) navigator.clearAppBadge().catch(() => { });
    }
  }

  function drawStatus() {
    const st = T.getStatus(), el = $("#pstat"); if (!el) return;
    $("#pname").textContent = partnerName();
    let line = "", live = false;
    if (st.typing && st.typing.length) line = "typing…";
    else if (st.relay === "live") { line = st.partner_online ? "online" : "connected"; live = true; }
    else if (st.relay === "connecting") line = "connecting…";
    else if (st.relay === "denied") line = "this pairing code isn't accepted by the relay";
    else if (LOCAL) line = st.backup ? "relay offline · sending through the shared folder" : (st.relay_url ? "relay offline · will send later" : "no relay set");
    else line = "offline · messages will send when back online";
    el.textContent = line; el.className = live ? "live" : "";
    $("#typing").innerHTML = st.typing && st.typing.length ? `<span>${esc(st.typing[0])} is typing…</span>` : "";
  }

  async function drawBanner() {
    const el = $("#banner"); if (!el) return;
    if (LOCAL) { el.innerHTML = ""; return; }
    if (isIOS && !standalone) { el.innerHTML = `<div class="banner">📲 For notifications: tap <b>Share</b> ⬆ then <b>Add to Home Screen</b>, and open A.F.R.A ♥ from there.</div>`; return; }
    if ("Notification" in window && Notification.permission === "default") {
      el.innerHTML = `<div class="banner">🔔 Get a notification when a message arrives <button id="bnotif">Turn on</button></div>`;
      $("#bnotif").onclick = async () => { const r = await T.registerPush(true); toast(r === "on" ? "Notifications are on ♥" : "Notifications are blocked: turn them on in Settings › Notifications"); drawBanner(); };
    } else el.innerHTML = "";
  }

  function bubbleBody(m) {
    if (m.deleted) return `<div class="txt">This message was deleted</div>`;
    const a = m.attachment;
    let html = "";
    if (m.reply_to) {
      const q = MSGS.find((x) => x.id === m.reply_to);
      html += `<div class="quote" data-goto="${esc(m.reply_to)}"><b>${esc(q ? (q.author === T.me ? "You" : q.author) : "")}</b> ${esc(q ? (q.deleted ? "deleted message" : q.text || (q.attachment ? q.attachment.name : "")) : "message").slice(0, 120)}</div>`;
    }
    if (a && a.blob) {
      if (m.kind === "image") html += `<img class="photo" data-blob="${esc(a.blob)}" alt="${esc(a.name)}">`;
      else if (m.kind === "voice") html += `<audio controls preload="none" data-blob="${esc(a.blob)}"></audio>`;
      else html += `<a class="file" href="#" data-file="${esc(a.blob)}"><span style="font-size:28px">📄</span><span><b>${esc(a.name)}</b><small>${sizeTxt(a.size || 0)}</small></span></a>`;
    }
    if (m.text) html += `<div class="txt ${onlyEmoji(m.text) ? "big" : ""}">${esc(m.text)}</div>`;
    return html;
  }

  function draw() {
    const log = $("#log"); if (!log) return;
    const atBottom = firstPaint || log.scrollHeight - log.scrollTop - log.clientHeight < 140;
    const shown = search ? MSGS.filter((m) => (m.text || "").toLowerCase().includes(search) || (m.attachment && m.attachment.name.toLowerCase().includes(search))) : MSGS;
    let html = "", lastDay = "";
    for (const m of shown) {
      const day = dayLabel(m.created);
      if (day !== lastDay) { html += `<div class="day">${day}</div>`; lastDay = day; }
      const mine = m.author === T.me;
      const rx = Object.entries(m.reactions || {});
      const ticks = mine && !m.deleted ? `<span class="ticks ${m.read ? "read" : ""}">${m.pending ? "🕓" : m.read ? "✓✓" : "✓"}</span>` : "";
      html += `<div class="msg ${mine ? "mine" : ""}" data-id="${esc(m.id)}">
        <div class="bub ${m.deleted ? "deleted" : ""}">${bubbleBody(m)}</div>
        ${rx.length ? `<div class="reacts">${rx.map(([who, e]) => `<span title="${esc(who)}">${esc(e)}</span>`).join("")}</div>` : ""}
        <div class="meta">${m.edited && !m.deleted ? "edited · " : ""}${fmtTime(m.created)} ${ticks}</div></div>`;
    }
    log.innerHTML = html || `<div class="empty">${search ? "Nothing found." : "No messages yet.<br>Say something sweet ♥<br><small>Tip: 💌 has ready-made notes.</small>"}</div>`;
    hydrate(log);
    if (atBottom) scrollEnd(true);
    firstPaint = false;
    drawStatus();
  }

  const URLS = {};
  async function hydrate(root) {
    for (const el of $$("[data-blob]", root)) {
      const m = MSGS.find((x) => x.attachment && x.attachment.blob === el.dataset.blob); if (!m) continue;
      const url = URLS[el.dataset.blob] || (URLS[el.dataset.blob] = await T.fileUrl(m.attachment).catch(() => ""));
      if (!url) { el.replaceWith(Object.assign(document.createElement("div"), { className: "txt", textContent: "⏳ downloading…" })); continue; }
      el.src = url;
      if (el.tagName === "IMG") el.onclick = (e) => { e.stopPropagation(); const v = document.createElement("div"); v.className = "viewer"; v.innerHTML = `<img src="${url}">`; v.onclick = () => v.remove(); document.body.appendChild(v); };
    }
    for (const a of $$("[data-file]", root)) a.onclick = async (e) => {
      e.preventDefault(); e.stopPropagation(); const m = MSGS.find((x) => x.attachment && x.attachment.blob === a.dataset.file);
      const url = await T.fileUrl(m.attachment); if (!url) return toast("Not downloaded yet");
      const link = document.createElement("a"); link.href = url; link.download = m.attachment.name; link.target = "_blank"; link.click();
    };
    for (const q of $$("[data-goto]", root)) q.onclick = (e) => {
      e.stopPropagation(); const t = $(`.msg[data-id="${CSS.escape(q.dataset.goto)}"]`); if (!t) return;
      t.scrollIntoView({ behavior: "smooth", block: "center" }); t.classList.add("flash"); setTimeout(() => t.classList.remove("flash"), 1400);
    };
    for (const el of $$(".msg", root)) bindPress(el);
  }

  function bindPress(el) {  // long-press (phone), right-click (computer): message menu; double-tap: ❤️
    let timer = null, lastTap = 0;
    const open = () => { timer = null; navigator.vibrate && navigator.vibrate(10); openMsgMenu(el.dataset.id); };
    el.addEventListener("touchstart", () => { timer = setTimeout(open, 450); }, { passive: true });
    el.addEventListener("touchmove", () => { clearTimeout(timer); timer = null; }, { passive: true });
    el.addEventListener("touchend", () => { if (timer) clearTimeout(timer); });
    el.addEventListener("contextmenu", (e) => { e.preventDefault(); openMsgMenu(el.dataset.id); });
    el.addEventListener("click", (e) => {
      if (e.target.closest("a,audio,img,.quote")) return;
      const t = Date.now(); if (t - lastTap < 320) { T.change(el.dataset.id, "react", "❤️").then(refresh); lastTap = 0; } else lastTap = t;
    });
  }

  function sheet(html) {
    const bg = document.createElement("div"); bg.className = "sheet-bg";
    bg.innerHTML = `<div class="sheet"><div class="grab"></div>${html}</div>`;
    bg.onclick = (e) => { if (e.target === bg) bg.remove(); };
    document.body.appendChild(bg); return bg;
  }

  function openMsgMenu(id) {
    const m = MSGS.find((x) => x.id === id); if (!m || m.deleted) return;
    const mine = m.author === T.me, myRx = (m.reactions || {})[T.me];
    const s = sheet(`<div class="menu"><div class="rx">${REACTIONS.map((r) => `<button data-rx="${r}" class="${myRx === r ? "on" : ""}">${r}</button>`).join("")}</div>
      <button data-a="reply">↩️ Reply</button>${m.text ? `<button data-a="copy">📋 Copy</button>` : ""}
      ${mine && m.text && !m.attachment ? `<button data-a="edit">✏️ Edit</button>` : ""}${mine ? `<button data-a="delete" class="danger">🗑 Delete for both of us</button>` : ""}</div>`);
    $$("[data-rx]", s).forEach((b) => b.onclick = async () => { s.remove(); await T.change(id, "react", b.dataset.rx); refresh(); });
    $$("[data-a]", s).forEach((b) => b.onclick = async () => {
      s.remove(); const a = b.dataset.a;
      if (a === "reply") { replyTo = m; editing = null; drawReply(); $("#txt").focus(); }
      if (a === "copy") { navigator.clipboard.writeText(m.text).then(() => toast("Copied")); }
      if (a === "edit") { editing = m; replyTo = null; $("#txt").value = m.text; window.growComposer(); drawReply(); $("#txt").focus(); }
      if (a === "delete" && confirm("Delete this message for both of you?")) { await T.change(id, "delete"); refresh(); }
    });
  }

  function drawReply() {
    const el = $("#replybar"); const m = replyTo || editing;
    el.innerHTML = m ? `<div class="reply-bar"><div><b>${editing ? "Editing" : "Replying to " + esc(m.author === T.me ? "yourself" : m.author)}</b> · ${esc((m.text || (m.attachment && m.attachment.name) || "").slice(0, 80))}</div><button class="cbtn" id="rclose">✕</button></div>` : "";
    if (m) $("#rclose").onclick = () => { if (editing) $("#txt").value = ""; replyTo = editing = null; drawReply(); window.growComposer(); };
  }

  async function sendText() {
    const txt = $("#txt"), text = txt.value.trim();
    if (editing) { if (text) await T.change(editing.id, "edit", text); editing = null; }
    else if (text) await T.send({ text, reply_to: replyTo ? replyTo.id : "" });
    txt.value = ""; replyTo = null; drawReply(); window.growComposer(); await refresh(); scrollEnd(true);
  }

  async function shrinkImage(file) {  // phone photos are huge: send a sharp 2048px JPEG instead
    if (!/^image\/(jpeg|png|heic|heif|webp)/.test(file.type) || file.size < 1.2e6) return new Uint8Array(await file.arrayBuffer());
    const img = await createImageBitmap(file).catch(() => null); if (!img) return new Uint8Array(await file.arrayBuffer());
    const s = Math.min(1, 2048 / Math.max(img.width, img.height)), c = document.createElement("canvas");
    c.width = Math.round(img.width * s); c.height = Math.round(img.height * s); c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
    const blob = await new Promise((r) => c.toBlob(r, "image/jpeg", 0.86));
    return new Uint8Array(await blob.arrayBuffer());
  }

  async function sendFile(file, kind) {
    try {
      if (file.size > 25 * 1048576) return toast("That file is larger than 25 MB");
      const image = !kind && /^image\//.test(file.type);
      const bytes = image ? await shrinkImage(file) : new Uint8Array(await file.arrayBuffer());
      const f = image && bytes.length !== file.size ? new File([bytes], (file.name || "photo").replace(/\.\w+$/, "") + ".jpg", { type: "image/jpeg" }) : file;
      const att = await T.attach(f, bytes);
      await T.send({ kind: kind || (image ? "image" : "file"), attachment: att, text: "", reply_to: replyTo ? replyTo.id : "" });
      replyTo = null; drawReply(); await refresh(); scrollEnd(true);
    } catch (e) { toast("Couldn't send: " + e.message); }
  }

  let rec = null;
  async function toggleVoice() {
    const btn = $("#bsend");
    if (rec) { rec.stop(); return; }
    if (!navigator.mediaDevices || !window.MediaRecorder) return toast("Voice notes need the app opened from the Home Screen (or a newer browser)");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const type = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm"].find((t) => MediaRecorder.isTypeSupported(t)) || "";
      const chunks = []; rec = new MediaRecorder(stream, type ? { mimeType: type } : {});
      const started = Date.now(); const tick = setInterval(() => { btn.textContent = "■ " + Math.floor((Date.now() - started) / 1000) + "s"; }, 500);
      btn.classList.add("rec"); btn.textContent = "■";
      rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      rec.onstop = async () => {
        clearInterval(tick); stream.getTracks().forEach((t) => t.stop()); btn.classList.remove("rec"); window.growComposer();
        const mime = (rec.mimeType || "audio/mp4").split(";")[0]; rec = null;
        if (Date.now() - started < 800) return toast("Hold on a little longer to record");
        const blob = new Blob(chunks, { type: mime });
        sendFile(new File([blob], `voice-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-")}.${mime.includes("mp4") ? "m4a" : "webm"}`, { type: mime }), "voice");
      };
      rec.start();
    } catch { rec = null; toast("Microphone not allowed"); }
  }

  async function openTemplates() {
    if (!TEMPLATES) { try { TEMPLATES = await (await fetch("templates.json", { cache: "no-cache" })).json(); } catch { TEMPLATES = { categories: [] }; } }
    const cats = TEMPLATES.categories || [], all = cats.flatMap((c) => c.items.map((t) => [c, t]));
    let cur = sessionStorage.getItem("afra_tpl_cat") || (cats[0] && cats[0].id), q = "";
    const s = sheet(`<h3>💌 Love notes</h3><input type="search" id="tq" placeholder="Search ${all.length} notes" style="margin-bottom:8px">
      <div class="chips" id="tchips"></div><div class="tlist" id="tlist"></div>`);
    const pick = (t) => { const txt = $("#txt"); txt.value = txt.value.trim() ? txt.value.trim() + " " + t : t; window.growComposer(); s.remove(); txt.focus(); };
    const drawList = () => {
      $("#tchips", s).innerHTML = `<button data-c="random">🎲 Surprise me</button>` + cats.map((c) => `<button data-c="${esc(c.id)}" class="${!q && c.id === cur ? "on" : ""}">${esc(c.emoji)} ${esc(c.name)}</button>`).join("");
      const items = q ? all.filter(([, t]) => t.toLowerCase().includes(q)).map(([, t]) => t) : ((cats.find((c) => c.id === cur) || { items: [] }).items);
      $("#tlist", s).innerHTML = items.map((t, i) => `<button data-i="${i}">${esc(t)}</button>`).join("") || `<div class="empty">Nothing found</div>`;
      $$("[data-i]", s).forEach((b) => b.onclick = () => pick(items[+b.dataset.i]));
      $$("[data-c]", s).forEach((b) => b.onclick = () => {
        if (b.dataset.c === "random") return pick(all[Math.floor(Math.random() * all.length)][1]);
        cur = b.dataset.c; q = ""; $("#tq", s).value = ""; sessionStorage.setItem("afra_tpl_cat", cur); drawList();
      });
    };
    $("#tq", s).oninput = (e) => { q = e.target.value.trim().toLowerCase(); drawList(); };
    drawList();
  }

  function openEmoji() {
    const s = sheet(`<div class="emojis">${EMOJIS.map((e) => `<button>${e}</button>`).join("")}</div>`);
    $$("button", s).forEach((b) => b.onclick = () => { const t = $("#txt"); t.value += b.textContent; window.growComposer(); s.remove(); t.focus(); });
  }

  async function openMainMenu() {
    const st = T.getStatus();
    const push = LOCAL ? "" : `<button data-a="push">🔔 Notifications${"Notification" in window && Notification.permission === "granted" ? ": on" : ""}</button>`;
    const s = sheet(`<div class="menu"><h3>A.F.R.A ♥</h3>
      <button data-a="name">🙂 Your name: <b>${esc(T.me)}</b></button>${push}
      ${LOCAL ? `<button data-a="phone">📲 Add your iPhone</button>` : ""}
      <div class="note" style="margin:6px 0">${st.relay === "live" ? "🔒 Connected. Messages are end-to-end encrypted: only your two apps can read them." : "Offline right now: messages are kept and sent when the connection is back."}${LOCAL && st.backup ? " A copy also travels through your shared library folder." : ""}</div>
      ${LOCAL ? "" : `<button data-a="leave" class="danger">Unpair this phone</button>`}</div>`);
    $$("[data-a]", s).forEach((b) => b.onclick = async () => {
      const a = b.dataset.a; s.remove();
      if (a === "name") { const n = prompt("Your name in the chat:", T.me); if (n && n.trim()) { await T.setName(n.trim()); refresh(); } }
      if (a === "push") { const r = await T.registerPush(true); toast(r === "on" ? "Notifications are on ♥" : r === "unsupported" ? (isIOS ? "Open the app from the Home Screen first" : "This browser can't do notifications") : "Blocked: allow notifications in Settings"); drawBanner(); }
      if (a === "phone") phoneHelp();
      if (a === "leave" && confirm("Unpair this phone? Messages on this phone are removed (they stay on your computers).")) { await T.leave(); location.reload(); }
    });
  }

  async function phoneHelp() {
    const s = await (await fetch("/api/settings")).json();
    const link = `${s.chat_relay}/#pair=${encodeURIComponent(s.chat_code)}`;
    const sh = sheet(`<h3>📲 Put the chat on an iPhone</h3><ol style="margin:0 0 10px;padding-left:20px;line-height:1.6">
      <li>Send yourself this link (AirDrop, WhatsApp or Notes) and open it in <b>Safari</b> on the iPhone.</li>
      <li>Tap <b>Share</b> ⬆ → <b>Add to Home Screen</b>, then open <b>A.F.R.A ♥</b> from its icon.</li>
      <li>Enter your name, tap <b>Join</b>, then <b>Turn on</b> notifications.</li></ol>
      <input type="text" readonly value="${esc(link)}" id="plink"><div class="row" style="margin-top:8px"><button class="big-btn" id="pcopy" style="flex:1">Copy link</button></div>
      <p style="font-size:13px;color:var(--muted)">The link contains your pairing code: only send it to yourself or your partner.</p>`);
    $("#pcopy", sh).onclick = () => navigator.clipboard.writeText(link).then(() => toast("Link copied"));
  }

  // ------------------------------------------------------------------ pairing
  function setupScreen() {
    const fromLink = new URLSearchParams(location.hash.slice(1)).get("pair") || "";
    app.innerHTML = `<div class="setup"><img class="logo" src="icon-512.png" alt=""><h1>A.F.R.A ♥</h1><p>A private chat for the two of you. End-to-end encrypted.</p>
      ${LOCAL ? `<label>Relay address (your VPS)</label><input type="url" id="srelay" placeholder="https://chat.example.com">` : ""}
      <label>Pairing code</label><input type="text" id="scode" class="code-input" placeholder="XXXX-XXXX-XXXX-XXXX" value="${esc(fromLink)}" autocapitalize="characters" autocomplete="off">
      <button class="big-btn ghost" id="snew">I'm the first one: make a new code</button>
      <label>Your name</label><input type="text" id="sname" placeholder="e.g. Raina" autocomplete="nickname">
      <button class="big-btn gold" id="sgo">Join ♥</button><div class="err" id="serr"></div>
      ${!LOCAL && isIOS && !standalone ? `<div class="note warn">📲 Tip: tap <b>Share</b> ⬆ → <b>Add to Home Screen</b> first, then open the app from its icon: that's where notifications work.</div>` : ""}
      <div class="note">Both of you use the <b>same pairing code</b> (on every phone and computer). It's the key to your chat: keep it between you two.</div></div>`;
    if (LOCAL) fetch("/api/settings").then((r) => r.json()).then((s) => { $("#srelay").value = s.chat_relay || ""; if (!$("#scode").value) $("#scode").value = s.chat_code || ""; $("#sname").value = s.chat_name || ""; });
    window.onhashchange = () => { const c = new URLSearchParams(location.hash.slice(1)).get("pair"); if (c && $("#scode")) $("#scode").value = c; };
    $("#snew").onclick = async () => { $("#scode").value = newCode(); $("#serr").textContent = "New code made: share it with your partner after joining (menu ⋯)."; };
    $("#sgo").onclick = async () => {
      const code = $("#scode").value, name = $("#sname").value.trim(), err = $("#serr");
      if (normCode(code).length !== 16) return err.textContent = "A pairing code has 16 letters and numbers.";
      if (!name) return err.textContent = "Please enter your name.";
      $("#sgo").disabled = true; err.textContent = "Setting up…";
      try {
        if (LOCAL) {
          const relay = $("#srelay").value.trim(); if (!relay) throw new Error("Enter the relay address of your VPS.");
          await new LocalTransport().api("/api/chat/pair", { code, relay, name });
        } else {
          await IDB.put("kv", { code: normCode(code).match(/.{4}/g).join("-"), name, device: "ph" + randHex(6) }, "cfg");
          history.replaceState(null, "", location.pathname);
        }
        start();
      } catch (e) { err.textContent = e.message; $("#sgo").disabled = false; }
    };
  }

  // ------------------------------------------------------------------ start
  async function start() {
    if (!LOCAL && "serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => { });
    if (!await T.init()) return setupScreen();
    shell(); drawBanner();
    T.onChange = (kind) => kind === "status" ? drawStatus() : refresh();
    await refresh(); scrollEnd(true);
    setInterval(drawStatus, 2000);
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") { refresh(); drawBanner(); if (!LOCAL && T.ws && T.ws.readyState > 1) T.connect(); } });
  }
  start().catch((e) => { app.innerHTML = `<div class="setup"><h1>A.F.R.A ♥</h1><p>Something went wrong: ${esc(e.message)}</p></div>`; });
})();
