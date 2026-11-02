const CACHE = "yt-studio-v1";
self.addEventListener("install", (event) => { event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(["/", "/icon.svg"]))); self.skipWaiting(); });
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (event) => { if (event.request.method === "GET") event.respondWith(fetch(event.request).catch(() => caches.match(event.request))); });