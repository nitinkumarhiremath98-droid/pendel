/* PENDEL service worker: makes the app installable and keeps the app shell available offline.
   Deliberately small: API calls, audio and YouTube are never cached, so listeners always get fresh,
   licensed streams. Bump VERSION when the shell changes. */
const VERSION = "pendel-v4.8";
const SHELL = ["/", "/css/app.css", "/js/app.js", "/js/api.js", "/js/ui.js", "/js/player.js", "/js/lyrics.js", "/js/views.js",
  "/js/highlights.js", "/js/voice.js", "/js/admin.js", "/js/legal.js", "/img/logo.svg", "/img/icons.svg", "/img/icons/icon-192.png", "/manifest.webmanifest"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;            // YouTube, fonts, Apple: straight to network
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/media/")) return; // data and audio: never cached
  if (req.mode === "navigate") {
    // pages: network first (fresh SEO tags and deploys), cached shell when offline
    e.respondWith(fetch(req).catch(() => caches.match("/")));
    return;
  }
  // static files: network first, fall back to cache; update the cache in the background
  e.respondWith(fetch(req).then((res) => {
    if (res.ok) { const copy = res.clone(); caches.open(VERSION).then((c) => c.put(req, copy)); }
    return res;
  }).catch(() => caches.match(req)));
});
