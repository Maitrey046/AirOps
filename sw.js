const CACHE_NAME = 'airops-shell-v1';
const SHELL = ['./', './index.html'];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('airops-shell-') && key !== CACHE_NAME).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});

self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin || url.pathname.includes('/api/')) return;
  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).then(response => {
      const copy = response.clone();
      caches.open(CACHE_NAME).then(cache => cache.put('./index.html', copy));
      return response;
    }).catch(async () => (await caches.match('./index.html')) || Response.error()));
  }
});

self.addEventListener('message', event => {
  const data = event.data || {};
  if (data.type === 'AIR_OPS_NOTIFY' && self.registration.showNotification) {
    event.waitUntil(self.registration.showNotification(data.title || 'AirOps alert', { body: data.body || 'An aircraft alert matched.', tag: 'airops-aircraft-alert' }));
  }
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clients => {
    const client = clients.find(item => 'focus' in item);
    return client ? client.focus() : self.clients.openWindow(self.registration.scope);
  }));
});

self.addEventListener('periodicsync', event => {
  if (event.tag !== 'airops-background-refresh') return;
  event.waitUntil((async () => {
    try {
      const response = await fetch(new URL('api/status', self.registration.scope));
      if (response.ok) {
        const cache = await caches.open(CACHE_NAME);
        await cache.put('airops-status', response.clone());
      }
    } catch { /* Background status refresh is best-effort. */ }
    const clients = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const client of clients) client.postMessage({ type: 'AIR_OPS_REFRESH' });
  })());
});
