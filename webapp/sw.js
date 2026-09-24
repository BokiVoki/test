// 하루앱 서비스워커 — 웹 푸시 수신 + 알림 클릭 시 앱 열기 전용.
// 오프라인 캐싱은 하지 않음(앱이 항상 최신 Supabase 데이터를 봐야 하므로).

self.addEventListener('install', function (e) {
  self.skipWaiting();
});
self.addEventListener('activate', function (e) {
  e.waitUntil(self.clients.claim());
});

self.addEventListener('push', function (e) {
  var payload = { title: '하루', body: '' };
  if (e.data) {
    try { payload = Object.assign(payload, e.data.json()); }
    catch (err) { payload.body = e.data.text(); }
  }
  var url = payload.url || './';
  e.waitUntil(self.registration.showNotification(payload.title || '하루', {
    body: payload.body || '',
    icon: 'apple-touch-icon.png',
    badge: 'apple-touch-icon.png',
    data: { url: url },
  }));
});

self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  var url = (e.notification.data && e.notification.data.url) || './';
  e.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
      for (var i = 0; i < list.length; i++) {
        var c = list[i];
        if ('focus' in c) { c.navigate(url); return c.focus(); }
      }
      if (self.clients.openWindow) return self.clients.openWindow(url);
    })
  );
});
