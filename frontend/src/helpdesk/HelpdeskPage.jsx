import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { loadIdentity, saveIdentity } from '../identity.js'
import { useDialog } from '../ui/Dialog.jsx'
import { StatusMessages } from '../ui/Notice.jsx'
import TopBar from '../ui/TopBar.jsx'
import { download, helpdeskApi } from './api.js'
import { SeverityBadge, SlaBadge, StatusBadge } from './badges.jsx'
import { duration, minutesSince, slaFocus, timeOfDay, useNow, usePolling } from './format.js'
import { ticketFields } from './forms.js'
import TicketDetail from './TicketDetail.jsx'

// Help desk, /helpdesk: ICT tickets for live events (see README "Help desk").
// Everyone on shift can see tickets; network admins (the ICT team) change them.

const SECTIONS = [
  ['dashboard', 'Dashboard'],
  ['tickets', 'Tickets'],
  ['reports', 'Reports'],
  ['recurring', 'Recurring issues'],
]

// Saved views for the queue (Spiceworks-style): name -> list filters.
const VIEWS = [
  ['open', 'All open', { status: 'open' }],
  ['new', 'Needs acknowledging', { status: 'new' }],
  ['mine', 'My tickets', { status: 'open', mine: true }],
  ['unassigned', 'Unassigned', { status: 'open', unassigned: 'true' }],
  ['partner', 'Partner stands', { status: 'open', category: 'partner_stand' }],
  ['overdue', 'Overdue SLA', { status: 'open', overdue: true }],
  ['done', 'Resolved & closed', { status: 'done' }],
  ['all', 'All tickets', { status: 'all' }],
]

function useUrlState(key, initial) {
  const [value, setValue] = useState(() => new URLSearchParams(window.location.search).get(key) ?? initial)
  const set = useCallback((next) => {
    setValue(next)
    const url = new URL(window.location.href)
    if (next === null || next === undefined || next === '' || next === initial) url.searchParams.delete(key)
    else url.searchParams.set(key, next)
    window.history.replaceState(null, '', url)
  }, [key, initial])
  return [value, set]
}

export default function HelpdeskPage() {
  const [identity, setIdentity] = useState(loadIdentity)
  useEffect(() => saveIdentity(identity), [identity])
  const api = useMemo(() => helpdeskApi(identity), [identity])
  const dialog = useDialog()
  const canEdit = identity.role === 'network-admin'
  const [meta, setMeta] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [section, setSection] = useUrlState('view', 'dashboard')
  const [eventId, setEventId] = useUrlState('event', '')
  const [ticketId, setTicketId] = useUrlState('ticket', '')
  const [version, setVersion] = useState(0) // bumps after changes so lists reload
  const headingRef = useRef(null)
  const sectionChanged = useRef(false)
  const now = useNow(30000)

  const loadMeta = useCallback(async () => {
    try {
      setMeta(await api.meta())
      setError(null)
    } catch (err) {
      setError(err.status === 403 ? 'Choose a role (Viewer or Network admin) in the top bar to use the help desk.' : err.message)
    }
  }, [api])
  useEffect(() => { loadMeta() }, [loadMeta])

  // Ticket links (e.g. from the WhatsApp bot) open the queue on that ticket.
  useEffect(() => {
    if (ticketId && section === 'dashboard' && !sectionChanged.current) setSection('tickets')
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const label = SECTIONS.find(([k]) => k === section)?.[1]
    document.title = `${ticketId && section === 'tickets' ? `#${ticketId} · ` : ''}${label} · Help desk · Event Network Hub`
  }, [section, ticketId])

  useEffect(() => {
    if (sectionChanged.current) headingRef.current?.focus()
    sectionChanged.current = false
  }, [section])

  const notify = useCallback((message, tone = 'info', retry) => {
    const actions = retry ? [{ label: 'Reload', run: retry }] : []
    setNotice({ message, tone, actions })
    if (tone === 'ok') setTimeout(() => setNotice((n) => (n?.message === message ? null : n)), 5000)
  }, [])

  const changed = useCallback(() => {
    setVersion((v) => v + 1)
    loadMeta()
  }, [loadMeta])

  function go(next) {
    sectionChanged.current = true
    setSection(next)
  }

  function openTicket(id) {
    setTicketId(String(id))
    if (section !== 'tickets') go('tickets')
  }

  async function newTicket() {
    const live = meta.live_event_id
    const target = eventId ? Number(eventId) : live
    let locations = []
    let partners = []
    if (target) {
      try {
        const [ov, orders] = await Promise.all([api.eventLists(target), api.partnerOrders(target)])
        locations = [...new Set(ov.lists.filter((i) => i.kind === 'location' || i.kind === 'building').map((i) => i.value))]
        partners = [...new Set(orders.map((o) => o.data.partner).filter(Boolean))]
      } catch {
        // suggestions are optional
      }
    }
    const values = await dialog.form({
      title: 'Raise a ticket', wide: true, confirmLabel: 'Raise ticket',
      body: 'For issues reported in person, by radio or phone. WhatsApp reports create tickets automatically.',
      fields: ticketFields(meta, { initial: { event_id: target }, locations, partners, events: meta.events,
        withEvent: true, withOwner: true }),
    })
    if (!values) return
    try {
      const body = { ...values, event_id: values.event_id ? Number(values.event_id) : null }
      const t = await api.create(body)
      notify(`Ticket #${t.id} raised.`, 'ok')
      changed()
      openTicket(t.id)
    } catch (err) {
      notify(err.message, 'error')
    }
  }

  async function setLive(id) {
    try {
      await api.setLiveEvent(id ? Number(id) : null)
      notify(id ? 'WhatsApp reports without an event code now go to this event.' : 'Live event cleared.', 'ok')
      loadMeta()
    } catch (err) {
      notify(err.message, 'error')
    }
  }

  const sectionLabel = SECTIONS.find(([k]) => k === section)?.[1] || 'Dashboard'
  const liveCode = meta?.events.find((e) => e.id === meta.live_event_id)?.code

  return (
    <>
      <a className="skip-link" href="#main">Skip to main content</a>
      <TopBar identity={identity} onChange={setIdentity} current="helpdesk" />
      <main id="main" className="hd-page" tabIndex={-1}>
      <div className="hd-bar">
        <h1 className="hd-title">Help desk</h1>
        {meta && (
          <>
            <label className="inline-label">
              Event
              <select value={eventId} onChange={(e) => setEventId(e.target.value)}>
                <option value="">All events</option>
                {meta.events.map((e) => <option key={e.id} value={e.id}>{e.code}{e.archived ? ' (archived)' : ''}</option>)}
              </select>
            </label>
            <span className="muted small live-event">
              Live event for WhatsApp: <strong>{liveCode || 'not set'}</strong>
              {canEdit && eventId && Number(eventId) !== meta.live_event_id && (
                <button type="button" className="link" onClick={() => setLive(eventId)}>Make this the live event</button>
              )}
            </span>
            {canEdit && <button type="button" className="primary-cta hd-new" onClick={newTicket}>+ Raise ticket</button>}
          </>
        )}
      </div>
      <nav className="hd-sections" aria-label="Help desk sections">
        {SECTIONS.map(([key, label]) => (
          <button key={key} type="button" className={section === key ? 'active' : ''} aria-current={section === key ? 'page' : undefined}
            onClick={() => go(key)}>
            {label}
          </button>
        ))}
      </nav>
      <StatusMessages notice={notice} onDismiss={() => setNotice(null)} className="hd-notice" />
      <div className="hd-main">
        <h2 className="visually-hidden" tabIndex={-1} ref={headingRef}>{sectionLabel}</h2>
        {error && <p className="error" role="alert">{error}</p>}
        {meta && !error && section === 'dashboard' && (
          <Dashboard api={api} meta={meta} eventId={eventId} version={version} now={now} onOpen={openTicket} />
        )}
        {meta && !error && section === 'tickets' && (
          <Tickets api={api} meta={meta} eventId={eventId} version={version} now={now} identity={identity}
            canEdit={canEdit} notify={notify} onChanged={changed} ticketId={ticketId} setTicketId={setTicketId} />
        )}
        {meta && !error && section === 'reports' && (
          <Reports api={api} meta={meta} eventId={eventId} identity={identity} notify={notify} version={version} />
        )}
        {meta && !error && section === 'recurring' && <Recurring api={api} meta={meta} onOpen={openTicket} />}
      </div>
      </main>
    </>
  )
}

// --- Tickets: queue + detail -------------------------------------------------------------

function Tickets({ api, meta, eventId, version, now, identity, canEdit, notify, onChanged, ticketId, setTicketId }) {
  const [view, setView] = useUrlState('list', 'open')
  const [q, setQ] = useState('')
  const [filters, setFilters] = useState({ severity: '', team: '', category: '' })
  const [tickets, setTickets] = useState(null)
  const ids = { search: useId(), sev: useId(), team: useId(), cat: useId() }
  const spec = VIEWS.find(([k]) => k === view)?.[2] || VIEWS[0][2]

  const load = useCallback(async () => {
    const params = {
      status: spec.status, unassigned: spec.unassigned, category: spec.category || filters.category || undefined,
      assignee: spec.mine ? identity.user || '—' : undefined, severity: filters.severity || undefined,
      team: filters.team || undefined, q: q.trim() || undefined, event_id: eventId || undefined,
    }
    try {
      let list = await api.list(params)
      if (spec.overdue) list = list.filter((t) => slaFocus(t).state === 'breached')
      setTickets(list)
    } catch (err) {
      notify(err.message, 'error')
    }
  }, [api, spec, filters, q, eventId, identity.user, notify])

  usePolling(load, 15000, [load, version])

  const selected = ticketId ? Number(ticketId) : null
  return (
    <div className={`hd-tickets${selected ? ' with-detail' : ''}`}>
      <nav className="hd-views" aria-label="Ticket views">
        <ul>
          {VIEWS.map(([key, label]) => (
            <li key={key}>
              <button type="button" className={view === key ? 'active' : ''} aria-current={view === key ? 'true' : undefined}
                onClick={() => setView(key)}>{label}</button>
            </li>
          ))}
        </ul>
      </nav>
      <section className="hd-queue" aria-label="Ticket list">
        <div className="hd-filters">
          <label htmlFor={ids.search} className="visually-hidden">Search tickets</label>
          <input id={ids.search} type="search" placeholder="Search title, area, partner, reporter or #number" value={q}
            onChange={(e) => setQ(e.target.value)} />
          <label className="inline-label" htmlFor={ids.sev}>Severity</label>
          <select id={ids.sev} value={filters.severity} onChange={(e) => setFilters((f) => ({ ...f, severity: e.target.value }))}>
            <option value="">Any</option>
            {Object.entries(meta.severities).map(([k, s]) => <option key={k} value={k}>{s.label}</option>)}
          </select>
          <label className="inline-label" htmlFor={ids.team}>Team</label>
          <select id={ids.team} value={filters.team} onChange={(e) => setFilters((f) => ({ ...f, team: e.target.value }))}>
            <option value="">Any</option>
            {Object.entries(meta.teams).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          {!spec.category && (
            <>
              <label className="inline-label" htmlFor={ids.cat}>Category</label>
              <select id={ids.cat} value={filters.category} onChange={(e) => setFilters((f) => ({ ...f, category: e.target.value }))}>
                <option value="">Any</option>
                {Object.entries(meta.categories).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            </>
          )}
          <span className="muted small" aria-live="polite">{tickets ? `${tickets.length} ticket${tickets.length === 1 ? '' : 's'}` : ''}</span>
        </div>
        <div className="table-wrap">
          <table className="hd-table">
            <caption className="visually-hidden">Tickets, newest first</caption>
            <thead>
              <tr>
                <th scope="col">#</th><th scope="col">Ticket</th><th scope="col">Severity</th><th scope="col">Status</th>
                <th scope="col">Owner</th><th scope="col">Age</th><th scope="col">SLA</th>
              </tr>
            </thead>
            <tbody aria-busy={!tickets || undefined}>
              {!tickets && [0, 1, 2].map((i) => <tr key={i} aria-hidden="true"><td colSpan={7}><span className="skeleton" /></td></tr>)}
              {tickets?.map((t) => (
                <tr key={t.id} className={`${t.id === selected ? 'selected' : ''} ${slaFocus(t).state === 'breached' && t.status !== 'closed' && t.status !== 'resolved' ? 'overdue' : ''}`}>
                  <td className="num">{t.id}</td>
                  <td>
                    <button type="button" className="ticket-link" onClick={() => setTicketId(String(t.id))}
                      aria-current={t.id === selected ? 'true' : undefined}>
                      {t.title}
                    </button>
                    <div className="muted small">
                      {[meta.categories[t.category], t.stand || t.location, t.partner, t.event_code].filter(Boolean).join(' · ')}
                    </div>
                  </td>
                  <td><SeverityBadge severity={t.severity} meta={meta} /></td>
                  <td><StatusBadge status={t.status} meta={meta} /></td>
                  <td>{t.assignee || <span className="muted">Unassigned</span>}</td>
                  <td className="num">{duration(minutesSince(t.created_at, now))}</td>
                  <td><SlaBadge ticket={t} now={now} /></td>
                </tr>
              ))}
              {tickets?.length === 0 && (
                <tr><td colSpan={7} className="empty-cell muted">No tickets in this view.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
      {selected && (
        <TicketDetail key={selected} id={selected} api={api} meta={meta} canEdit={canEdit} identity={identity}
          notify={notify} now={now} onChanged={() => { onChanged(); load() }} onClose={() => setTicketId('')}
          onOpen={(id) => setTicketId(String(id))} />
      )}
    </div>
  )
}

// --- Dashboard ----------------------------------------------------------------------------------

function Dashboard({ api, meta, eventId, version, now, onOpen }) {
  const [open, setOpen] = useState(null)
  const [done, setDone] = useState([])
  const [big, setBig] = useState(false)
  const [announce, setAnnounce] = useState('')
  const lastNew = useRef(null)
  const boardRef = useRef(null)

  const load = useCallback(async () => {
    const params = { event_id: eventId || undefined }
    const [o, d] = await Promise.all([api.list({ ...params, status: 'open' }), api.list({ ...params, status: 'done' })])
    setOpen(o)
    setDone(d.slice(0, 8))
    const fresh = o.filter((t) => t.status === 'new').length
    if (lastNew.current !== null && fresh > lastNew.current) {
      setAnnounce(`${fresh - lastNew.current} new ticket${fresh - lastNew.current === 1 ? '' : 's'} need acknowledging`)
    }
    lastNew.current = fresh
  }, [api, eventId])

  usePolling(() => load().catch(() => {}), 10000, [load, version])

  async function toggleBig() {
    const next = !big
    setBig(next)
    try {
      if (next && boardRef.current?.requestFullscreen) await boardRef.current.requestFullscreen()
      else if (!next && document.fullscreenElement) await document.exitFullscreen()
    } catch {
      // fullscreen not allowed: the larger layout still applies
    }
  }
  useEffect(() => {
    const onChange = () => { if (!document.fullscreenElement) setBig(false) }
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])

  if (!open) return <div className="skeleton-block" aria-hidden="true" />

  const unacked = open.filter((t) => !t.acknowledged_at).sort((a, b) => a.created_at.localeCompare(b.created_at))
  const overdue = open.filter((t) => slaFocus(t).state === 'breached')
  const urgent = open.filter((t) => t.severity === 'P1' || t.severity === 'P2')
  const partner = open.filter((t) => t.category === 'partner_stand' || t.partner)
  const byTeam = Object.keys(meta.teams).map((k) => [meta.teams[k], open.filter((t) => t.team === k).length])
  const tiles = [
    ['Open', open.length, ''],
    ['Need acknowledging', unacked.length, unacked.length ? 'warn' : ''],
    ['Overdue SLA', overdue.length, overdue.length ? 'alert' : ''],
    ['P1 / P2 open', urgent.length, urgent.length ? 'alert' : ''],
    ['Partner stands', partner.length, ''],
  ]

  const list = (items, empty) => (
    items.length ? (
      <ul className="board-list">
        {items.map((t) => (
          <li key={t.id}>
            <button type="button" className="ticket-link" onClick={() => onOpen(t.id)}>
              <span className="ticket-number">#{t.id}</span> {t.title}
            </button>
            <div className="board-meta">
              <SeverityBadge severity={t.severity} meta={meta} />
              <span className="muted small">{[t.stand || t.location, t.partner, t.assignee || 'Unassigned'].filter(Boolean).join(' · ')}</span>
              <SlaBadge ticket={t} now={now} />
            </div>
          </li>
        ))}
      </ul>
    ) : <p className="muted">{empty}</p>
  )

  return (
    <div ref={boardRef} className={`board${big ? ' big' : ''}`}>
      <div className="visually-hidden" role="status" aria-live="polite">{announce}</div>
      <div className="row space-between board-head">
        <p className="muted small">Updates every 10 seconds · {timeOfDay(new Date(now).toISOString())}</p>
        <button type="button" className="secondary" onClick={toggleBig} aria-pressed={big}>
          {big ? 'Exit big screen' : 'Big screen'}
        </button>
      </div>
      <div className="tiles">
        {tiles.map(([label, value, tone]) => (
          <div key={label} className={`panel tile ${tone}`}>
            <span className="tile-value">{value}</span>
            <span className="tile-label">{label}</span>
          </div>
        ))}
        <div className="panel tile">
          {byTeam.map(([team, n]) => <span key={team} className="tile-split"><strong>{n}</strong> {team}</span>)}
        </div>
      </div>
      <div className="board-columns">
        <section className="panel" aria-labelledby="board-unacked"><h3 id="board-unacked">Needs acknowledging</h3>
          {list(unacked, 'Everything has an owner.')}</section>
        <section className="panel" aria-labelledby="board-overdue"><h3 id="board-overdue">Overdue SLA</h3>
          {list(overdue, 'Nothing overdue.')}</section>
        <section className="panel" aria-labelledby="board-done"><h3 id="board-done">Recently resolved</h3>
          {list(done, 'Nothing resolved yet.')}</section>
      </div>
    </div>
  )
}

// --- Reports --------------------------------------------------------------------------------

function Kpi({ label, value, goal, unit = '%', hint }) {
  const shown = value === null || value === undefined ? '—' : `${value}${unit}`
  const ok = goal === undefined || value === null || value === undefined ? null : value >= goal
  return (
    <div className={`panel kpi${ok === false ? ' below' : ok ? ' on-target' : ''}`}>
      <span className="tile-label">{label}</span>
      <span className="tile-value">{shown}</span>
      {goal !== undefined && <span className="small">{ok === null ? `Goal ${goal}${unit}` : ok ? `On target (goal ${goal}${unit})` : `Below goal of ${goal}${unit}`}</span>}
      {hint && <span className="muted small">{hint}</span>}
    </div>
  )
}

function Reports({ api, meta, eventId, identity, notify, version }) {
  const [informal, setInformal] = useState('')
  const [data, setData] = useState(null)
  const informalId = useId()

  useEffect(() => {
    api.report({ event_id: eventId || undefined, informal: informal === '' ? undefined : informal })
      .then(setData).catch((err) => notify(err.message, 'error'))
  }, [api, eventId, informal, version, notify])

  async function exportAs(format) {
    try {
      await download(api.exportUrl({ event_id: eventId || undefined, format }), identity)
    } catch (err) {
      notify(err.message, 'error')
    }
  }

  if (!data) return <div className="skeleton-block" aria-hidden="true" />
  return (
    <div className="reports">
      <div className="row space-between">
        <h3 className="report-title">{data.event}: {data.tickets} ticket{data.tickets === 1 ? '' : 's'}
          {data.duplicates ? <span className="muted small"> · {data.duplicates} duplicate{data.duplicates === 1 ? '' : 's'} merged</span> : null}
        </h3>
        <div className="row">
          <button type="button" className="secondary" onClick={() => exportAs('csv')}>Export CSV</button>
          <button type="button" onClick={() => exportAs('xlsx')}>Export Excel</button>
        </div>
      </div>
      <div className="tiles">
        <Kpi label={`Acknowledged within ${data.acknowledge.target_minutes} min`} value={data.acknowledge.within_target_pct}
          goal={data.acknowledge.goal_pct} hint={`Median ${duration(data.acknowledge.median_minutes)}`} />
        <Kpi label="Coverage" value={data.coverage.pct} goal={data.coverage.goal_pct}
          hint="Needs the number of issues handled outside the system" />
        <Kpi label="Closed with root cause and fix" value={data.closure_quality.pct} goal={data.closure_quality.goal_pct}
          hint={`${data.closure_quality.documented} of ${data.closure_quality.closed}`} />
        <Kpi label="Partner-impacting issues" value={data.partner_impacting.count} unit=""
          hint={`Mean time to resolve ${duration(data.partner_impacting.mean_minutes_to_resolve)}`} />
        <Kpi label="Recurring (same category and area)" value={data.recurrence.pct}
          hint={`${data.recurrence.recurring} of ${data.recurrence.with_area} with an area`} />
      </div>
      <div className="row coverage-input">
        <label htmlFor={informalId} className="field-label">Issues handled outside the system (radio, word of mouth)</label>
        <input id={informalId} type="number" min="0" inputMode="numeric" value={informal}
          onChange={(e) => setInformal(e.target.value.replace(/\D/g, ''))} placeholder="e.g. 4" />
      </div>
      <div className="board-columns">
        <section className="panel" aria-labelledby="rep-sev">
          <h3 id="rep-sev">Resolution by severity</h3>
          <table className="ov-table">
            <thead><tr><th scope="col">Severity</th><th scope="col" className="num">Tickets</th>
              <th scope="col" className="num">Resolved in SLA</th><th scope="col" className="num">Mean time to resolve</th></tr></thead>
            <tbody>
              {Object.entries(data.by_severity).map(([k, s]) => (
                <tr key={k}><td>{meta.severities[k].label} <span className="muted small">(target {duration(meta.severities[k].resolve_minutes)})</span></td>
                  <td className="num">{s.count}</td><td className="num">{s.resolved_within_sla_pct ?? '—'}{s.resolved_within_sla_pct !== null ? '%' : ''}</td>
                  <td className="num">{duration(s.mean_minutes_to_resolve)}</td></tr>
              ))}
            </tbody>
          </table>
        </section>
        <section className="panel" aria-labelledby="rep-cat">
          <h3 id="rep-cat">By category</h3>
          <table className="ov-table">
            <thead><tr><th scope="col">Category</th><th scope="col" className="num">Tickets</th></tr></thead>
            <tbody>
              {Object.entries(meta.categories).map(([k, l]) => (
                <tr key={k}><td>{l}</td><td className="num">{data.by_category[k] || 0}</td></tr>
              ))}
            </tbody>
          </table>
        </section>
        <section className="panel" aria-labelledby="rep-areas">
          <h3 id="rep-areas">Busiest areas</h3>
          {data.top_areas.length ? (
            <table className="ov-table">
              <thead><tr><th scope="col">Area or stand</th><th scope="col" className="num">Tickets</th></tr></thead>
              <tbody>{data.top_areas.map((a) => <tr key={a.area}><td>{a.area}</td><td className="num">{a.count}</td></tr>)}</tbody>
            </table>
          ) : <p className="muted">No areas recorded yet.</p>}
        </section>
      </div>
      <p className="muted small">
        Ticket sources: {Object.entries(data.by_source).map(([k, n]) => `${meta.sources[k]} ${n}`).join(' · ') || 'none yet'}.
        Duplicates merged into another ticket are left out of the figures.
      </p>
    </div>
  )
}

// --- Recurring issues (knowledge base) ---------------------------------------------------------

function Recurring({ api, meta, onOpen }) {
  const [groups, setGroups] = useState(null)
  useEffect(() => { api.recurring().then(setGroups).catch(() => setGroups([])) }, [api])
  if (!groups) return <div className="skeleton-block" aria-hidden="true" />
  return (
    <div className="recurring">
      <p className="muted">
        Problems seen more than once in the same category and area, across all events, with what fixed them.
        Use it to plan the next event and to fix faster on the day.
      </p>
      {groups.length === 0 && <p className="panel muted">No recurring issues yet.</p>}
      {groups.map((g) => (
        <section key={`${g.category}-${g.area}`} className="panel recurring-item" aria-label={`${g.area}, ${meta.categories[g.category]}`}>
          <div className="row space-between">
            <h3>{g.area} <span className="muted small">· {meta.categories[g.category]}</span></h3>
            <span className="badge-pill sev-P3">{g.count} times{g.open ? ` · ${g.open} open` : ''}</span>
          </div>
          <p className="muted small">Events: {g.events.join(', ') || '—'} · last seen {timeOfDay(g.last_seen)}</p>
          {g.fixes.length ? (
            <ul className="plain-list">
              {g.fixes.map((f) => (
                <li key={f.ticket}>
                  <button type="button" className="link" onClick={() => onOpen(f.ticket)}>#{f.ticket}</button>
                  {f.event ? ` (${f.event})` : ''}: <strong>{f.root_cause}</strong> — {f.resolution}
                </li>
              ))}
            </ul>
          ) : <p className="muted small">No fixes recorded yet.</p>}
        </section>
      ))}
    </div>
  )
}
