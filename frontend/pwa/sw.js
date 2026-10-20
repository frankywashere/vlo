/* Cache only a public offline page. Account data, signed media and APIs stay network-only. */
const CACHE = "recastfx-offline-v1";
self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.add("/offline.html")));
  self.skipWaiting();
});
self.addEventListener("activate", event => {
  event.waitUntil(Promise.all([caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith("recastfx-offline-") && key !== CACHE).map(key => caches.delete(key)))), self.clients.claim()]));
});
self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/") || url.pathname.startsWith("/__/")) return;
  if (event.request.mode === "navigate") event.respondWith(fetch(event.request).catch(() => caches.match("/offline.html")));
});
self.addEventListener("push", event => {
  // Pushes deliberately contain no names, thumbnails, signed URLs or uploaded content.
  event.waitUntil(self.registration.showNotification("Your recast is ready", {
    body: "Open your library to watch your new video.", icon: "/icon-192.png", badge: "/icon-192.png", tag: "recastfx-ready",
    data: {url: "/?page=library"},
  }));
});
self.addEventListener("notificationclick", event => {
  event.notification.close();
  const url = new URL("/?page=library", self.location.origin).href;
  event.waitUntil(self.clients.matchAll({type: "window", includeUncontrolled: true}).then(async clients => {
    const client = clients.find(item => new URL(item.url).origin === self.location.origin);
    if (client) {await client.navigate(url); return client.focus();}
    return self.clients.openWindow(url);
  }));
});
