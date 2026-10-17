import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { loadIdentity } from '../identity.js'
import { useDialog } from '../ui/Dialog.jsx'
import { StatusMessages } from '../ui/Notice.jsx'
import { eventApi } from './api.js'
import { TITLES } from './columns.js'
import { HistoryPanel, ListsPanel, Overview } from './panels.jsx'
import RecordTable from './RecordTable.jsx'

// Requirements page, /event/<documentId>: the event's network requirements as
// structured records. Viewers read; network admins edit and publish versions.

const VIEWS = [
  ['overview', 'Overview'],
  ['wifi', TITLES.wifi],
  ['ethernet', TITLES.ethernet],
  ['partner', TITLES.partner],
  ['lists', 'Setup'],
  ['history', 'History'],
]

export default function EventPage({ documentId }) {
  const [identity] = useState(loadIdentity) // TEST ONLY role/name chosen on the main page
  const isAdmin = identity.role === 'network-admin'
  const api = useMemo(() => eventApi(documentId, identity), [documentId, identity])
  const dialog = useDialog()
  const [overview, setOverview] = useState(null)
  const [error, setError] = useState(null)
  const [view, setView] = useState(() => {
    const v = new URLSearchParams(window.location.search).get('view')
    return VIEWS.some(([k]) => k === v) ? v : 'overview'
  })
  const [notice, setNotice] = useState(null) // { message, tone, actions }
  const [publishing, setPublishing] = useState(false)
  const viewHeading = useRef(null)
  const viewChanged = useRef(false)

  const refresh = useCallback(async () => {
    try {
      const ov = await api.overview()
      setOverview(ov)
    } catch (err) {
      setError(err.status === 403 ? 'Choose a role on the Events page first.' : err.message)
    }
  }, [api])

  useEffect(() => {
    refresh()
  }, [refresh])

  // Page title names the event and the view (WCAG 2.4.2).
  useEffect(() => {
    if (!overview) return
    const label = VIEWS.find(([k]) => k === view)?.[1]
    document.title = `${label} · ${overview.document.name} · Event Network Hub`
  }, [overview, view])

  // After switching views, move focus to the new view's heading so keyboard and
  // screen-reader users land at the start of the content.
  useEffect(() => {
    if (viewChanged.current) viewHeading.current?.focus()
    viewChanged.current = false
  }, [view])

  // retry: shows "Reload"; action { label, run }: e.g. Undo; timeout (ms) hides the message again.
  const notify = useCallback((message, tone = 'info', retry, action, timeout) => {
    const actions = []
    if (retry) actions.push({ label: 'Reload', run: () => { retry(); refresh() } })
    if (action) actions.push(action)
    setNotice({ message, tone, actions })
    const ms = timeout ?? (tone === 'ok' ? 5000 : null)
    if (ms) setTimeout(() => setNotice((n) => (n?.message === message ? null : n)), ms)
  }, [refresh])

  function goTo(next) {
    viewChanged.current = true
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
    const shown = changes.slice(-8).reverse()
    const ok = await dialog.confirm({
      title: `Publish V${next}?`,
      body: (
        <>
          <p>
            {changes.length} change{changes.length === 1 ? '' : 's'} since the previous version. Everyone will see V{next} as
            the latest version, as a PDF report and an Excel workbook.
          </p>
          <ul className="dialog-list">
            {shown.map((c) => <li key={c.id}>{c.summary}</li>)}
          </ul>
          {changes.length > shown.length && <p className="muted small">…and {changes.length - shown.length} more</p>}
        </>
      ),
      confirmLabel: `Publish V${next}`,
    })
    if (!ok) return
    setPublishing(true)
    try {
      const result = await api.publish()
      notify(`${result.message} It's now the latest version on the Events page.`, 'ok')
      refresh()
    } catch (err) {
      notify(err.message, 'error')
    } finally {
      setPublishing(false)
    }
  }

  async function saveAsTemplate() {
    const name = await dialog.prompt({
      title: 'Save as template',
      body: 'The template keeps the venues, sections, lists, Wi-Fi networks and Ethernet items. Ethernet points are set to 0 and notes left empty; partner sections, partner orders and Wi-Fi passwords are left out.',
      label: 'Template name',
      hint: '2 to 20 letters or digits, for example LIS',
      initial: overview.document.name.split(' | ')[0].replace(/\d+$/, ''),
      maxLength: 20,
      confirmLabel: 'Save template',
    })
    if (!name) return
    try {
      const tpl = await api.saveAsTemplate(name)
      notify(`Saved as template ${tpl.name}. It's now in "Start from" for new events.`, 'ok')
    } catch (err) {
      notify(err.message, 'error')
    }
  }

  const isTemplate = overview?.document.kind === 'template'
  const backHref = isTemplate ? '/' : `/?doc=${documentId}`

  if (error || !overview) {
    return (
      <div className="event-page">
        <header className="sheet-bar">
          <a className="button secondary" href={backHref}>← Events</a>
        </header>
        <main id="main" className="sheet-message" aria-busy={!error}>
          <h1 className="view-title">{error ? "Couldn't open this event" : 'Loading event…'}</h1>
          {error ? <p className="error" role="alert">{error}</p> : <div className="skeleton-block" aria-hidden="true" />}
        </main>
      </div>
    )
  }

  const archived = !!overview.document.archived_at
  const canEdit = isAdmin && !archived // archived events are read-only, even for admins
  const props = { api, overview, isAdmin: canEdit, notify, onChanged: refresh }
  const viewLabel = VIEWS.find(([k]) => k === view)?.[1]
  const points = overview.totals.ethernet.reduce((sum, t) => sum + t.total, 0)
  return (
    <div className="event-page">
      <a className="skip-link" href="#main">Skip to main content</a>
      <header className="sheet-bar">
        <img src="/websummit-mark.png" alt="Web Summit" className="brand-mark" />
        <a className="button secondary" href={backHref}>← Events</a>
        <h1 className="sheet-title">{overview.document.name}</h1>
        <span className="muted small">
          {isTemplate ? <span className="tag">Template</span> : 'Requirements'}
          {archived && <span className="tag"> Archived</span>}
          {canEdit ? '' : ' · read only'}
        </span>
        <span className="sheet-status">
          {isTemplate ? '' : overview.unpublished_changes > 0 ? `${overview.unpublished_changes} unpublished change(s)` : overview.has_records ? 'All published' : ''}
        </span>
        {isAdmin && !isTemplate && overview.has_records && (
          <button className="secondary" onClick={saveAsTemplate}>Save as template</button>
        )}
        {canEdit && !isTemplate && overview.has_records && (
          <button className="primary-cta" onClick={publish} disabled={publishing || overview.unpublished_changes === 0}
            aria-busy={publishing || undefined}>
            {publishing ? 'Publishing…' : `Publish V${(overview.latest_version || 0) + 1}`}
          </button>
        )}
      </header>
      <StatusMessages notice={notice} onDismiss={() => setNotice(null)} className="event-notice" />
      {archived && (
        <div className="notice info event-notice banner" role="region" aria-label="Archived">
          <span>
            <strong>Archived.</strong> This event is read-only.{isAdmin ? ' Restore it on the Events page to make changes.' : ''}
          </span>
        </div>
      )}
      <div className="event-body">
        <nav className="event-nav" aria-label="Requirements sections">
          {VIEWS.filter(([key]) => !(isTemplate && key === 'partner')).map(([key, label]) => {
            const count = key === 'ethernet' ? points : overview.counts?.[key]
            return (
              <button key={key} className={view === key ? 'active' : ''} onClick={() => goTo(key)}
                aria-current={view === key ? 'page' : undefined}
                disabled={!overview.has_records && overview.venues.length === 0 && key !== 'overview' && key !== 'history'}>
                <span>{label}</span>
                {count !== undefined && (
                  <span className="nav-count">
                    {count}<span className="visually-hidden">{key === 'ethernet' ? ' Ethernet points' : ' items'}</span>
                  </span>
                )}
                {key === 'lists' && overview.flagged_list_values > 0 && (
                  <span className="nav-flag">
                    {overview.flagged_list_values}<span className="visually-hidden"> values to review</span>
                  </span>
                )}
              </button>
            )
          })}
        </nav>
        <main id="main" className="event-main" tabIndex={0} aria-label={viewLabel}>
          <h2 className="view-title" tabIndex={-1} ref={viewHeading}>{viewLabel}</h2>
          {view === 'overview' && <Overview {...props} onPublish={publish} goTo={goTo} />}
          {['wifi', 'ethernet', 'partner'].includes(view) && <RecordTable key={view} type={view} {...props} />}
          {view === 'lists' && <ListsPanel {...props} />}
          {view === 'history' && <HistoryPanel {...props} />}
        </main>
      </div>
    </div>
  )
}
