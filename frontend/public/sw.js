// Soniox Live Translate - Service Worker
// Caches static assets for offline use. The API (WebSocket) always needs
// a network connection, so we only cache the shell (HTML/CSS/JS).

const CACHE_VERSION = "v1";
const CACHE_NAME = `soniox-live-translate-${CACHE_VERSION}`;

const SHELL_ASSETS = [
  "/",
  "/index.html",
  "/setup.html",
  "/styles.css",
  "/manifest.json",
  "/icon-192.svg",
  "/icon-512.svg",
];

// Install: cache shell assets
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS))
  );
  self.skipWaiting();
});

// Activate: clean up old caches
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k !== CACHE_NAME)
          .map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

// Fetch: network-first for API, cache-first for assets
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Never intercept WebSocket, API, or cross-origin requests
  if (
    event.request.url.startsWith("ws") ||
    url.pathname.startsWith("/ws/") ||
    url.pathname.startsWith("/api/") ||
    url.pathname.startsWith("/health") ||
    url.pathname.startsWith("/config") ||
    url.pathname.startsWith("/setup") ||
    url.pathname.startsWith("/transcript") ||
    url.origin !== location.origin
  ) {
    return;
  }

  // Cache-first for hashed assets (immutable)
  if (url.pathname.startsWith("/assets/")) {
    event.respondWith(
      caches.match(event.request).then(
        (cached) => cached || fetch(event.request).then((resp) => {
          const clone = resp.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          return resp;
        })
      )
    );
    return;
  }

  // Network-first for shell (always fresh when online)
  event.respondWith(
    fetch(event.request)
      .then((resp) => {
        const clone = resp.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        return resp;
      })
      .catch(() => caches.match(event.request))
  );
});
