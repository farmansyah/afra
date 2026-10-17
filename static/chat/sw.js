/* A.F.R.A chat: works offline (app shell) and shows push notifications. The push carries no message text:
   messages are end-to-end encrypted, so the notification just says a new one arrived. */
const CACHE = "afra-chat-v2";
const SHELL = ["./", "index.html", "chat.css?v=2", "chat.js?v=2", "templates.json", "manifest.webmanifest", "icon-512.png"];
self.addEventListener("install", (e) => { e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener("activate", (e) => { e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim())); });
self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin || u.pathname.startsWith("/v1/")) return;
  e.respondWith(fetch(e.request).then((r) => { const copy = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); return r; })
    .catch(() => caches.match(e.request).then((r) => r || caches.match("index.html"))));
});
self.addEventListener("push", (e) => {
  e.waitUntil((async () => {
    // always show one: iOS withdraws push from apps that receive a push silently (the relay skips pushes while the app is open)
    await self.registration.showNotification("A.F.R.A ♥", { body: "💌 You have a new message", icon: "icon-512.png", badge: "icon-512.png", tag: "afra-chat", renotify: true });
    if (self.navigator && self.navigator.setAppBadge) { try { await self.navigator.setAppBadge(); } catch (err) { } }
  })());
});
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((cs) => cs.length ? cs[0].focus() : self.clients.openWindow("./")));
});
