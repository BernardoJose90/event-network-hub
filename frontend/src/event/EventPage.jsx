import { useCallback, useEffect, useMemo, useState } from 'react'
import { loadIdentity } from '../identity.js'
import { eventApi } from './api.js'
import { TITLES } from './columns.js'
import { HistoryPanel, ListsPanel, Overview, VlanMatrix } from './panels.jsx'
import RecordTable from './RecordTable.jsx'

// Requirements page, /event/<documentId>: the event's network requirements as
// structured records. Viewers read; network admins edit and publish versions.

const VIEWS = [
  ['overview', 'Overview'],
  ['wifi', TITLES.wifi],
  ['ethernet', TITLES.ethernet],
  ['partner', TITLES.partner],
  ['vlan_extension', TITLES.vlan_extension],
  ['lists', 'Lists'],
  ['history', 'History'],
]

export default function EventPage({ documentId }) {
  const [identity] = useState(loadIdentity) // TEST ONLY role/name chosen on the main page
  const isAdmin = identity.role === 'network-admin'
  const api = useMemo(() => eventApi(documentId, identity), [documentId, identity])
  const [overview, setOverview] = useState(null)
  const [error, setError] = useState(null)
  const [view, setView] = useState(() => {
    const v = new URLSearchParams(window.location.search).get('view')
    return VIEWS.some(([k]) => k === v) ? v : 'overview'
  })
  const [notice, setNotice] = useState(null) // { message, tone, retry }
  const [publishing, setPublishing] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const ov = await api.overview()
      setOverview(ov)
      document.title = `${ov.document.name} · requirements`
    } catch (err) {
      setError(err.status === 403 ? 'Choose a role on the documents page first.' : err.message)
    }
  }, [api])

  useEffect(() => {
    refresh()
  }, [refresh])

  const notify = useCallback((message, tone = 'info', retry) => {
    setNotice({ message, tone, retry })
    if (tone === 'ok') setTimeout(() => setNotice((n) => (n?.message === message ? null : n)), 4000)
  }, [])

  function goTo(next) {
    setView(next)
    const url = new URL(window.location.href)
    url.searchParams.set('view', next)
    window.history.replaceState(null, '', url)
  }

  async function publish() {
    let changes = []
    try {
      changes = await api.changes()
    } catch (err) {
      notify(err.message, 'error')
      return
    }
    const next = (overview.latest_version || 0) + 1
    const preview = changes.slice(-8).map((c) => `• ${c.summary}`).join('\n')
    const more = changes.length > 8 ? `\n…and ${changes.length - 8} more` : ''
    if (!window.confirm(`Publish V${next} with ${changes.length} change(s)?\n\n${preview}${more}\n\nEveryone will see V${next} as the latest version.`)) return
    setPublishing(true)
    try {
      const result = await api.publish()
      notify(`${result.message} It's now the latest version on the documents page.`, 'ok')
      refresh()
    } catch (err) {
      notify(err.message, 'error')
    } finally {
      setPublishing(false)
    }
  }

  const back = () => window.location.assign(`/?doc=${documentId}`)

  if (error || !overview) {
    return (
      <div className="event-page">
        <header className="sheet-bar">
          <button className="secondary" onClick={back}>← Documents</button>
        </header>
        <div className="sheet-message">{error ? <p className="error">{error}</p> : <p className="muted">Loading…</p>}</div>
      </div>
    )
  }

  const props = { api, overview, isAdmin, notify, onChanged: refresh }
  return (
    <div className="event-page">
      <header className="sheet-bar">
        <button className="secondary" onClick={back}>← Documents</button>
        <strong className="sheet-title">{overview.document.name}</strong>
        <span className="muted small">Requirements{isAdmin ? '' : ' · read only'}</span>
        <span className="sheet-status">
          {overview.unpublished_changes > 0 ? `${overview.unpublished_changes} unpublished change(s)` : overview.has_records ? 'All published' : ''}
        </span>
        {isAdmin && overview.has_records && (
          <button onClick={publish} disabled={publishing || overview.unpublished_changes === 0}>
            {publishing ? 'Publishing…' : `Publish V${(overview.latest_version || 0) + 1}`}
          </button>
        )}
      </header>
      {notice && (
        <div className={`notice ${notice.tone} event-notice`}>
          <span>{notice.message}</span>
          {notice.retry && (
            <button className="link" onClick={() => { notice.retry(); refresh(); setNotice(null) }}>Reload</button>
          )}
          <button className="link" onClick={() => setNotice(null)} aria-label="Dismiss">✕</button>
        </div>
      )}
      <div className="event-body">
        <nav className="event-nav" aria-label="Requirements">
          {VIEWS.map(([key, label]) => (
            <button key={key} className={view === key ? 'active' : ''} onClick={() => goTo(key)}
              disabled={!overview.has_records && key !== 'overview' && key !== 'history'}>
              {label}
              {key in (overview.counts || {}) && <span className="nav-count">{overview.counts[key]}</span>}
              {key === 'lists' && overview.flagged_list_values > 0 && <span className="nav-flag">{overview.flagged_list_values}</span>}
            </button>
          ))}
        </nav>
        <main className="event-main">
          {view === 'overview' && <Overview {...props} onPublish={publish} goTo={goTo} />}
          {['wifi', 'ethernet', 'partner'].includes(view) && <RecordTable key={view} type={view} {...props} />}
          {view === 'vlan_extension' && <VlanMatrix {...props} />}
          {view === 'lists' && <ListsPanel {...props} />}
          {view === 'history' && <HistoryPanel {...props} />}
        </main>
      </div>
    </div>
  )
}
