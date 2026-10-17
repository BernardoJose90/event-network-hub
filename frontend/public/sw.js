// Service worker for browser alerts (Push API + Notifications API).
// Shows each push as a system notification; clicking it focuses an open app tab on that page,
// or opens one.

self.addEventListener('push', (event) => {
  let data = {}
  try {
    data = event.data ? event.data.json() : {}
  } catch {
    data = { title: 'Event Network Hub', body: event.data ? event.data.text() : '' }
  }
  const title = data.title || 'Event Network Hub'
  event.waitUntil(self.registration.showNotification(title, {
    body: data.body || '',
    tag: data.tag, // the same notification replaces itself instead of stacking
    renotify: Boolean(data.urgent),
    requireInteraction: Boolean(data.urgent), // urgent alerts stay until dealt with (where supported)
    icon: '/websummit-mark.png',
    badge: '/websummit-mark.png',
    data: { url: data.url || '/' },
  }))
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const url = new URL(event.notification.data?.url || '/', self.location.origin).href
  event.waitUntil((async () => {
    const tabs = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
    for (const tab of tabs) {
      if (new URL(tab.url).origin === self.location.origin && 'focus' in tab) {
        await tab.focus()
        if ('navigate' in tab) await tab.navigate(url)
        return
      }
    }
    await self.clients.openWindow(url)
  })())
})
