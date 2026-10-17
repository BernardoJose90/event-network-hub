import { useCallback, useEffect, useId, useRef, useState } from 'react'

// Notification centre: a bell with the unread count, a panel with the latest notifications,
// and the alert settings for this device (browser push). Live updates come over server-sent
// events; if the stream drops, it falls back to polling every 30 s.
// Push permission is only asked when the person clicks "Turn on alerts" (web.dev, WebKit).

function headers(identity, json = false) {
  const h = {}
  if (identity.role) h['X-Role'] = identity.role
  if (identity.user) h['X-User'] = identity.user
  if (json) h['Content-Type'] = 'application/json'
  return h
}

async function call(identity, method, path, body) {
  const res = await fetch(path, { method, headers: headers(identity, body !== undefined), body: body === undefined ? undefined : JSON.stringify(body) })
  const data = await res.json().catch(() => null)
  if (!res.ok) throw Object.assign(new Error(data?.detail || `Request failed (HTTP ${res.status})`), { status: res.status })
  return data
}

function urlBase64ToUint8Array(base64) {
  const padding = '='.repeat((4 - (base64.length % 4)) % 4)
  const raw = atob((base64 + padding).replace(/-/g, '+').replace(/_/g, '/'))
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)))
}

const pushSupported = () => 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
const isIos = () => /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
const isStandalone = () => window.matchMedia?.('(display-mode: standalone)').matches || navigator.standalone

function ago(iso) {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  return new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })
}

function useNotificationFeed(identity) {
  const [feed, setFeed] = useState({ items: [], unread: 0 })
  const load = useCallback(async () => {
    if (!identity.role) return
    try {
      setFeed(await call(identity, 'GET', '/api/notifications?limit=30'))
    } catch {
      // the bell just shows nothing until the next update
    }
  }, [identity])

  useEffect(() => {
    if (!identity.role) return undefined
    load()
    let poll = null
    const params = new URLSearchParams({ user: identity.user || '', role: identity.role })
    const source = new EventSource(`/api/notifications/stream?${params}`)
    source.addEventListener('update', () => load())
    source.onerror = () => {
      if (!poll) poll = setInterval(load, 30000) // keep working if the stream is blocked
    }
    return () => {
      source.close()
      if (poll) clearInterval(poll)
    }
  }, [identity, load])
  return [feed, load]
}

function DeviceAlerts({ identity }) {
  const [settings, setSettings] = useState(null)
  const [state, setState] = useState('checking') // checking | unsupported | off | on | blocked | ios-install
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState(null)

  const refresh = useCallback(async () => {
    try {
      setSettings(await call(identity, 'GET', '/api/notifications/settings'))
    } catch (err) {
      setMessage(err.message)
    }
    if (!pushSupported()) {
      setState(isIos() && !isStandalone() ? 'ios-install' : 'unsupported')
      return
    }
    if (Notification.permission === 'denied') {
      setState('blocked')
      return
    }
    const reg = await navigator.serviceWorker.getRegistration('/sw.js')
    const sub = reg && (await reg.pushManager.getSubscription())
    setState(sub ? 'on' : 'off')
  }, [identity])
  useEffect(() => { refresh() }, [refresh])

  async function turnOn() {
    setBusy(true)
    setMessage(null)
    try {
      const reg = await navigator.serviceWorker.register('/sw.js')
      const permission = await Notification.requestPermission() // asked only after this click
      if (permission !== 'granted') {
        setState(permission === 'denied' ? 'blocked' : 'off')
        return
      }
      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(settings.vapid_public_key),
      })
      await call(identity, 'POST', '/api/notifications/push/subscribe', sub.toJSON())
      setState('on')
      setMessage('Alerts are on for this device.')
      refresh()
    } catch (err) {
      setMessage(`Couldn't turn on alerts: ${err.message}`)
    } finally {
      setBusy(false)
    }
  }

  async function turnOff() {
    setBusy(true)
    try {
      const reg = await navigator.serviceWorker.getRegistration('/sw.js')
      const sub = reg && (await reg.pushManager.getSubscription())
      if (sub) {
        await call(identity, 'POST', '/api/notifications/push/unsubscribe', { endpoint: sub.endpoint })
        await sub.unsubscribe()
      }
      setState('off')
      setMessage('Alerts are off for this device.')
      refresh()
    } finally {
      setBusy(false)
    }
  }

  async function test() {
    await call(identity, 'POST', '/api/notifications/test')
    setMessage('Test alert sent. It should arrive within a few seconds.')
  }

  async function toggle(key, on) {
    const chosen = settings.push_types.filter((p) => (p.key === key ? on : p.on)).map((p) => p.key)
    await call(identity, 'PUT', '/api/notifications/settings', { push_types: chosen })
    refresh()
  }

  if (!settings) return <p className="muted small">Loading…</p>
  return (
    <div className="device-alerts">
      <h3 className="notif-subtitle">Alerts on this device</h3>
      {!settings.push_available && <p className="muted small">Browser alerts aren&apos;t set up on this server.</p>}
      {settings.push_available && state === 'ios-install' && (
        <p className="small">On iPhone or iPad, first add this app to the Home Screen (Share → Add to Home Screen), then open it from there to turn on alerts.</p>
      )}
      {settings.push_available && state === 'unsupported' && <p className="muted small">This browser doesn&apos;t support alerts.</p>}
      {settings.push_available && state === 'blocked' && (
        <p className="small">Alerts are blocked for this site. Allow notifications in the browser&apos;s site settings, then try again.</p>
      )}
      {settings.push_available && (state === 'off' || state === 'on') && (
        <div className="row">
          {state === 'off'
            ? <button type="button" onClick={turnOn} disabled={busy}>{busy ? 'Turning on…' : 'Turn on alerts'}</button>
            : <button type="button" className="secondary" onClick={turnOff} disabled={busy}>Turn off on this device</button>}
          {state === 'on' && <button type="button" className="secondary" onClick={test}>Send a test alert</button>}
        </div>
      )}
      {message && <p className="small" role="status">{message}</p>}
      <fieldset className="checkbox-field">
        <legend className="field-label">Alert me about</legend>
        <div className="checkbox-list stacked">
          {settings.push_types.map((p) => (
            <label key={p.key} className="checkbox-option">
              <input type="checkbox" checked={p.on} onChange={(e) => toggle(p.key, e.target.checked)} /> {p.label}
            </label>
          ))}
        </div>
      </fieldset>
      <p className="muted small">
        Everything also appears here in the notification list.
        {settings.devices ? ` Alerts are on for ${settings.devices} device${settings.devices === 1 ? '' : 's'} under your name.` : ''}
      </p>
    </div>
  )
}

export default function NotificationBell({ identity }) {
  const [feed, reload] = useNotificationFeed(identity)
  const [open, setOpen] = useState(false)
  const [tab, setTab] = useState('list')
  const buttonRef = useRef(null)
  const panelRef = useRef(null)
  const headingRef = useRef(null)
  const panelId = useId()

  const close = useCallback((returnFocus = true) => {
    setOpen(false)
    if (returnFocus) buttonRef.current?.focus()
  }, [])

  useEffect(() => {
    if (!open) return undefined
    headingRef.current?.focus()
    const onKey = (e) => { if (e.key === 'Escape') close() }
    const onClick = (e) => {
      if (!panelRef.current?.contains(e.target) && !buttonRef.current?.contains(e.target)) close(false)
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onClick)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onClick)
    }
  }, [open, close])

  async function markRead(ids) {
    try {
      await call(identity, 'POST', '/api/notifications/read', ids ? { ids } : {})
      reload()
    } catch {
      // stays unread; harmless
    }
  }

  if (!identity.role) return null
  const label = `Notifications${feed.unread ? `, ${feed.unread} unread` : ''}`
  return (
    <div className="notif">
      <button ref={buttonRef} type="button" className="notif-button" aria-label={label} aria-expanded={open}
        aria-controls={panelId} onClick={() => setOpen((o) => !o)}>
        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">
          <path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0" fill="none" stroke="currentColor"
            strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        {feed.unread > 0 && <span className="notif-count" aria-hidden="true">{feed.unread > 99 ? '99+' : feed.unread}</span>}
      </button>
      {open && (
        <section ref={panelRef} id={panelId} className="notif-panel" aria-labelledby={`${panelId}-title`}>
          <div className="row space-between notif-head">
            <h2 id={`${panelId}-title`} tabIndex={-1} ref={headingRef}>{tab === 'list' ? 'Notifications' : 'Alert settings'}</h2>
            <div className="row nowrap">
              {tab === 'list' && feed.unread > 0 && (
                <button type="button" className="link" onClick={() => markRead(null)}>Mark all as read</button>
              )}
              <button type="button" className="link" onClick={() => setTab(tab === 'list' ? 'settings' : 'list')}>
                {tab === 'list' ? 'Settings' : 'Back to notifications'}
              </button>
            </div>
          </div>
          {tab === 'settings' ? <DeviceAlerts identity={identity} /> : (
            feed.items.length === 0 ? <p className="muted small">No notifications yet.</p> : (
              <ul className="notif-list">
                {feed.items.map((n) => (
                  <li key={n.id} className={`${n.read_at ? '' : 'unread'} sev-${n.severity}`}>
                    <a href={n.link || '#'} onClick={() => { if (!n.read_at) markRead([n.id]) }}>
                      <span className="notif-title">
                        {!n.read_at && <span className="visually-hidden">Unread: </span>}
                        {n.severity === 'urgent' && <span className="badge-pill sla-breached">Urgent</span>} {n.title}
                      </span>
                      {n.body && <span className="notif-body">{n.body}</span>}
                      <span className="notif-time"><time dateTime={n.created_at}>{ago(n.created_at)}</time></span>
                    </a>
                  </li>
                ))}
              </ul>
            )
          )}
        </section>
      )}
    </div>
  )
}
