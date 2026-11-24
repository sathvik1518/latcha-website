/* LATCHA service worker: makes the site installable and shows a friendly page if the phone is offline.
   It never touches /api, so orders, points and the owner desk are always live. */
var V = "latcha-v1", SHELL = ["/", "/favicon-192.png", "/icons/icon-192.png"];
self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(V).then(function (c) { return c.addAll(SHELL); }).then(function () { return self.skipWaiting(); }));
});
self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (ks) { return Promise.all(ks.filter(function (k) { return k !== V; }).map(function (k) { return caches.delete(k); })); }).then(function () { return self.clients.claim(); }));
});
self.addEventListener("fetch", function (e) {
  var r = e.request, u = new URL(r.url);
  if (r.method !== "GET" || u.origin !== location.origin || u.pathname.indexOf("/api/") === 0 || u.pathname === "/admin.html") return;
  if (r.mode === "navigate") {   // pages: network first, saved copy if offline
    e.respondWith(fetch(r).then(function (res) { var cp = res.clone(); caches.open(V).then(function (c) { c.put("/", cp); }); return res; })
      .catch(function () { return caches.match("/").then(function (m) { return m || new Response("You are offline. Please reconnect to order.", { status: 503, headers: { "Content-Type": "text/plain" } }); }); }));
    return;
  }
  if (u.pathname.indexOf("/assets/") === 0 || u.pathname.indexOf("/icons/") === 0) {   // pictures: saved copy first, refreshed in the background
    e.respondWith(caches.match(r).then(function (hit) {
      var net = fetch(r).then(function (res) { if (res && res.ok) { var cp = res.clone(); caches.open(V).then(function (c) { c.put(r, cp); }); } return res; }).catch(function () { return hit; });
      return hit || net;
    }));
  }
});
