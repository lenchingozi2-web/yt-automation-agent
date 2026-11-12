const CACHE = "studio-v5";
const ASSETS = new Set(["/icon-192.png", "/icon-512.png", "/apple-touch-icon.png", "/lenxiq.jpg"]);

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});
self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(
    keys.filter((key) => key.startsWith("studio-") && key !== CACHE).map((key) => caches.delete(key)),
  )).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin || !ASSETS.has(url.pathname)) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const existing = await cache.match(request);
    if (existing) return existing;
    const response = await fetch(request);
    if (response.ok) await cache.put(request, response.clone());
    return response;
  })());
});
