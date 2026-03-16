// service-worker.js
// TV Intercom — Service Worker
// Mantiene la connessione attiva in background e gestisce la cache

const CACHE_NAME = 'tv-intercom-v1';
const URLS_TO_CACHE = ['/operator/', '/operator/index.html'];

// ---------------------------------------------------------------------------
// Install — precache risorse
// ---------------------------------------------------------------------------
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(URLS_TO_CACHE))
  );
  self.skipWaiting();
});

// ---------------------------------------------------------------------------
// Activate — pulizia cache vecchie
// ---------------------------------------------------------------------------
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// ---------------------------------------------------------------------------
// Fetch — serve dalla cache se offline
// ---------------------------------------------------------------------------
self.addEventListener('fetch', event => {
  event.respondWith(
    fetch(event.request).catch(() =>
      caches.match(event.request)
    )
  );
});

// ---------------------------------------------------------------------------
// Push notifications — per istruzioni camera in background
// ---------------------------------------------------------------------------
self.addEventListener('push', event => {
  if (!event.data) return;
  const data = event.data.json();
  event.waitUntil(
    self.registration.showNotification(`CAM ${data.camera} — Istruzione`, {
      body: data.text,
      icon: '/operator/icon-192.png',
      badge: '/operator/icon-192.png',
      tag: `cam-${data.camera}`,
      renotify: true,
      requireInteraction: false,
      silent: false,
      vibrate: [200, 100, 200],
    })
  );
});

// ---------------------------------------------------------------------------
// Message — comunicazione con la pagina
// ---------------------------------------------------------------------------
self.addEventListener('message', event => {
  if (event.data?.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
