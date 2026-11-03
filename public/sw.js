const CACHE = "studio-v3";
self.addEventListener("install", event => { self.skipWaiting(); event.waitUntil(caches.open(CACHE)); });
self.addEventListener("activate", event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", event => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/") || url.pathname.startsWith("/auth/") || url.pathname.startsWith("/_next/")) return;
  if (request.mode === "navigate" || request.destination === "document" || request.destination === "script") {
    event.respondWith(fetch(request).catch(() => new Response("Network unavailable. Reconnect and refresh.", { status: 503 })));
    return;
  }
  if (request.destination === "image" || request.destination === "style") {
    event.respondWith(fetch(request).then(response => {
      if (response.ok) { const copy = response.clone(); event.waitUntil(caches.open(CACHE).then(c => c.put(request, copy))); }
      return response;
    }).catch(() => caches.match(request)));
  }
});
