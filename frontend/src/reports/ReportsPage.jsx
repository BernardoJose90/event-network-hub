import { useCallback, useEffect, useId, useMemo, useState } from 'react'
import { download } from '../helpdesk/api.js'
import { loadIdentity, saveIdentity } from '../identity.js'
import { StatusMessages } from '../ui/Notice.jsx'
import TopBar from '../ui/TopBar.jsx'

// Reports, /reports: each event against the ICT objectives, events side by side (oldest first).
// Follows the Government Analysis Function data visualisation guidance: headline content first;
// charts with statement titles and subtitles, bars ranked by value and starting at zero, goals
// as reference marks, a text description and a data table for every chart; sources, definitions
// and the generated time on the page; data downloadable in other formats.

const MAX_COLUMNS = 6
const intFmt = new Intl.NumberFormat('en-GB')
const pctFmt = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 1 })

const n = (v) => (v === null || v === undefined ? '—' : intFmt.format(v))
const pct = (v) => (v === null || v === undefined ? '—' : `${pctFmt.format(v)}%`)
const hours = (v) => (v === null || v === undefined ? '—' : `${pctFmt.format(v)} h`)
const minutes = (v) => (v === null || v === undefined ? '—' : `${pctFmt.format(v)} min`)
const day = (v) => (v ? new Date(v).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) : '—')
const stamp = (v) => (v ? new Date(v).toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—')
const plural = (count, word) => `${intFmt.format(count)} ${word}${count === 1 ? '' : 's'}`
const signed = (v) => (v > 0 ? `+${intFmt.format(v)}` : v < 0 ? `−${intFmt.format(-v)}` : 'no change')

function headers(identity) {
  const h = {}
  if (identity.role) h['X-Role'] = identity.role
  if (identity.user) h['X-User'] = identity.user
  return h
}

function initialSelection() {
  const raw = new URLSearchParams(window.location.search).get('events')
  const ids = (raw || '').split(',').filter((x) => /^\d+$/.test(x)).map(Number)
  return ids.length ? ids : null
}

// A value against its goal, as text (never colour alone).
function Goal({ value, goal, lowerIsBetter = false, format = pct }) {
  if (value === null || value === undefined) return <span className="muted">—</span>
  const ok = lowerIsBetter ? value <= goal : value >= goal
  return (
    <span className={ok ? 'goal-ok' : 'goal-miss'}>
      {format(value)}
      <span className="small"> {ok ? '· meets goal' : `· goal ${lowerIsBetter ? format(goal) : pct(goal)}`}</span>
    </span>
  )
}

// A chart: statement title + subtitle in the caption, ranked bars (decorative), and a text
// description pointing to the data table (WCAG 1.1.1; W3C complex images; Analysis Function).
function Figure({ id, title, subtitle, items, percent = false, goal, description }) {
  const ranked = [...items].sort((a, b) => (b.value ?? -1) - (a.value ?? -1))
  const max = percent ? 100 : Math.max(1, ...ranked.map((i) => i.value || 0))
  return (
    <figure className="report-figure" role="group" aria-labelledby={`${id}-title`} aria-describedby={`${id}-desc`}>
      <figcaption>
        <h3 id={`${id}-title`} className="figure-title">{title}</h3>
        <p className="figure-subtitle">{subtitle}</p>
      </figcaption>
      <div className="bars" aria-hidden="true">
        {ranked.map((i) => (
          <div key={i.key} className="bar-row">
            <span className="bar-label">{i.label}</span>
            <span className="bar-track">
              {i.value !== null && i.value !== undefined && <span className="bar-fill" style={{ width: `${(100 * i.value) / max}%` }} />}
              {goal !== undefined && <span className="bar-goal" style={{ left: `${goal}%` }} />}
            </span>
            <span className="bar-value">{i.value === null || i.value === undefined ? 'no data' : percent ? pct(i.value) : n(i.value)}</span>
          </div>
        ))}
        {goal !== undefined && <p className="bar-goal-key">Line marks the goal of {pct(goal)}</p>}
      </div>
      <p id={`${id}-desc`} className="figure-desc">{description} The figures are in the table below.</p>
    </figure>
  )
}

function Compare({ caption, events, rows }) {
  return (
    <div className="table-wrap">
      <table className="ov-table report-table">
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Measure</th>
            {events.map((e) => <th key={e.id} scope="col" className="num">{e.code}{e.archived ? ' (archived)' : ''}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, cell, hint]) => (
            <tr key={label}>
              <th scope="row">{label}{hint && <div className="muted small">{hint}</div>}</th>
              {events.map((e) => <td key={e.id} className="num">{cell(e)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Source({ text }) {
  return <p className="report-source">Source: {text}</p>
}

// Headline cards: the latest selected event, compared with the one before it.
function Headlines({ events, goals }) {
  const latest = events[events.length - 1]
  const previous = events.length > 1 ? events[events.length - 2] : null
  const vs = (get) => {
    if (!previous) return null
    const a = get(previous)
    const b = get(latest)
    if (a === null || a === undefined || b === null || b === undefined) return null
    return `${signed(b - a)} vs ${previous.code}`
  }
  const cards = [
    { title: 'Ethernet points planned', value: n(latest.cable_planning.points), detail: vs((e) => e.cable_planning.points) },
    { title: 'ICT requests on time', value: <Goal value={latest.requests.on_time_pct} goal={goals.requests_on_time_pct} />,
      detail: latest.requests.requests ? plural(latest.requests.requests, 'request') : 'No requests yet' },
    { title: 'Tickets acknowledged within 5 min', value: <Goal value={latest.operations.ack_within_target_pct} goal={goals.ack_within_target_pct} />,
      detail: latest.operations.tickets ? plural(latest.operations.tickets, 'ticket') : 'No tickets yet' },
    { title: 'Tickets closed with root cause and fix', value: <Goal value={latest.operations.closure_quality_pct} goal={goals.closure_quality_pct} />,
      detail: null },
    { title: 'Changes from first to last version', value: latest.publishing.first_to_last
      ? plural(latest.publishing.first_to_last.added + latest.publishing.first_to_last.removed + latest.publishing.first_to_last.changed, 'item')
      : '—', detail: latest.publishing.first_to_last
      ? `V${latest.publishing.first_to_last.from} → V${latest.publishing.first_to_last.to}, points ${signed(latest.publishing.first_to_last.points_delta)}`
      : 'Needs two published versions' },
  ]
  return (
    <section className="panel report-section" aria-labelledby="rep-glance">
      <h2 id="rep-glance">At a glance: {latest.code}</h2>
      <p className="muted small">
        The most recent selected event{previous ? `, compared with ${previous.code}` : ''}. Details for every event follow below.
      </p>
      <div className="tiles headline-tiles">
        {cards.map((c) => (
          <div key={c.title} className="panel tile">
            <span className="tile-label">{c.title}</span>
            <span className="tile-value">{c.value}</span>
            {c.detail && <span className="small">{c.detail}</span>}
          </div>
        ))}
      </div>
    </section>
  )
}

export default function ReportsPage() {
  const [identity, setIdentity] = useState(loadIdentity)
  useEffect(() => saveIdentity(identity), [identity])
  const [data, setData] = useState(null)
  const [chosen, setChosen] = useState(initialSelection) // event ids; null = server default (all active)
  const [notice, setNotice] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)
  const pickerId = useId()

  const query = chosen ? `?events=${chosen.join(',')}` : ''
  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch(`/api/reports${query}`, { headers: headers(identity) })
      const body = await res.json().catch(() => null)
      if (!res.ok) throw Object.assign(new Error(body?.detail || `Request failed (HTTP ${res.status})`), { status: res.status })
      setData(body)
      setError(null)
    } catch (err) {
      setError(err.status === 403 ? 'Choose a role (Viewer or Network admin) in the top bar to see reports.' : err.message)
    } finally {
      setLoading(false)
    }
  }, [query, identity])
  useEffect(() => { load() }, [load])
  useEffect(() => { document.title = 'Reports · Event Network Hub' }, [])

  // Keep the selection in the URL so a view can be bookmarked or shared.
  useEffect(() => {
    const url = new URL(window.location.href)
    if (chosen) url.searchParams.set('events', chosen.join(','))
    else url.searchParams.delete('events')
    window.history.replaceState(null, '', url)
  }, [chosen])

  // Printing expands the collapsed details so the printout is complete.
  useEffect(() => {
    const open = () => document.querySelectorAll('.reports-page details').forEach((d) => { d.dataset.wasOpen = d.open; d.open = true })
    const restore = () => document.querySelectorAll('.reports-page details').forEach((d) => { d.open = d.dataset.wasOpen === 'true' })
    window.addEventListener('beforeprint', open)
    window.addEventListener('afterprint', restore)
    return () => {
      window.removeEventListener('beforeprint', open)
      window.removeEventListener('afterprint', restore)
    }
  }, [])

  // Oldest event first, so "event over event" reads left to right.
  const events = useMemo(() => [...(data?.events || [])].sort((a, b) => a.created_at.localeCompare(b.created_at)), [data])
  const selected = useMemo(() => new Set(chosen ?? events.map((e) => e.id)), [chosen, events])
  const goals = data?.goals || {}
  const teams = data?.teams || {}

  function toggle(id, on) {
    const next = new Set(selected)
    if (on) next.add(id)
    else next.delete(id)
    const ordered = (data?.available || []).map((e) => e.id).filter((x) => next.has(x))
    if (ordered.length) setChosen(ordered)
  }

  async function exportAs(format) {
    try {
      await download(`/api/reports/export${query ? `${query}&` : '?'}format=${format}`, identity)
    } catch (err) {
      setNotice({ message: err.message, tone: 'error' })
    }
  }

  const met = (values, goal) => values.filter((v) => v !== null && v !== undefined && v >= goal).length
  const withData = (values) => values.filter((v) => v !== null && v !== undefined).length
  const noted = events.filter((e) => e.notes.length)

  return (
    <>
      <a className="skip-link" href="#main">Skip to main content</a>
      <TopBar identity={identity} onChange={setIdentity} current="reports" />
      <main id="main" className="reports-page" tabIndex={-1} aria-busy={loading || undefined}>
        <div className="hd-bar">
          <h1 className="hd-title">Reports</h1>
          <p className="muted small">How each event did against the ICT objectives, side by side.</p>
          {data && (
            <div className="row report-actions">
              <button type="button" className="secondary" onClick={load} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh'}</button>
              <button type="button" className="secondary" onClick={() => window.print()}>Print</button>
            </div>
          )}
        </div>
        {data && (
          <p className="report-meta">
            Live data from Event Network Hub, generated <time dateTime={data.generated_at}>{stamp(data.generated_at)}</time> (your local time).
            Covers each event&apos;s current requirements, ICT requests and help desk tickets.
          </p>
        )}
        <StatusMessages notice={notice} onDismiss={() => setNotice(null)} className="hd-notice" />
        <div className="hd-main">
          {error && <p className="error" role="alert">{error}</p>}
          {!data && !error && <div className="skeleton-block" aria-hidden="true" />}
          {data && (
            <>
              <fieldset className="panel event-picker" aria-describedby={pickerId}>
                <legend className="field-label">Events to compare</legend>
                <p id={pickerId} className="muted small">
                  Shown oldest first. Archived events can be included. {selected.size > MAX_COLUMNS
                    ? `More than ${MAX_COLUMNS} events make the tables hard to read; consider fewer.` : ''}
                </p>
                <div className="checkbox-list">
                  {data.available.map((e) => (
                    <label key={e.id} className="checkbox-option">
                      <input type="checkbox" checked={selected.has(e.id)} onChange={(ev) => toggle(e.id, ev.target.checked)}
                        disabled={selected.has(e.id) && selected.size === 1} />
                      {e.code}{e.archived ? ' (archived)' : ''}
                    </label>
                  ))}
                </div>
              </fieldset>

              {events.length === 0 ? <p className="panel muted">No events to report on yet.</p> : (
                <>
                  <Headlines events={events} goals={goals} />

                  {noted.length > 0 && (
                    <section className="panel report-section data-notes" aria-labelledby="rep-notes">
                      <h2 id="rep-notes">Before you compare</h2>
                      <p className="muted small">Gaps in the data that affect the figures below.</p>
                      <ul className="plain-list">
                        {noted.map((e) => <li key={e.id}><strong>{e.code}:</strong> {e.notes.join(' ')}</li>)}
                      </ul>
                    </section>
                  )}

                  <section className="panel report-section" aria-labelledby="rep-cables">
                    <h2 id="rep-cables">1. Cable planning</h2>
                    <p className="muted small">Objective: fewer unused cables and fewer &ldquo;unknown&rdquo; requirements during deployment.</p>
                    {(() => {
                      const top = [...events].sort((a, b) => b.cable_planning.points - a.cable_planning.points)[0]
                      const title = events.every((e) => !e.cable_planning.points) ? 'No Ethernet points planned yet'
                        : events.length === 1 ? `${top.code} plans ${n(top.cable_planning.points)} Ethernet points`
                          : `${top.code} plans the most Ethernet points (${n(top.cable_planning.points)})`
                      return (
                        <Figure id="fig-points" title={title} subtitle="Ethernet points planned per event, current requirements"
                          items={events.map((e) => ({ key: e.id, label: e.code, value: e.cable_planning.points }))}
                          description={`Points planned: ${events.map((e) => `${e.code} ${n(e.cable_planning.points)}`).join(', ')}.`} />
                      )
                    })()}
                    <Compare caption="Cable planning by event" events={events} rows={[
                      ['Ethernet points', (e) => n(e.cable_planning.points)],
                      ['Ethernet items', (e) => n(e.cable_planning.items)],
                      ['Points via ICT requests', (e) => `${n(e.cable_planning.points_via_requests)} (${pct(e.cable_planning.via_requests_pct)})`],
                      ['Request deadline', (e) => (e.cable_planning.deadline ? day(e.cable_planning.deadline) : '—')],
                      ['Added after the deadline', (e) => (e.cable_planning.items_added_after_deadline === null ? '—'
                        : `${n(e.cable_planning.items_added_after_deadline)} items · ${n(e.cable_planning.points_added_after_deadline)} points`)],
                      ['Wired tickets on site', (e) => n(e.cable_planning.wired_tickets_on_site)],
                    ]} />
                    <details className="report-more">
                      <summary>Points per venue and section</summary>
                      {events.map((e) => (
                        <div key={e.id} className="report-breakdown">
                          <h3>{e.code}</h3>
                          {e.cable_planning.by_section.length ? (
                            <table className="ov-table report-table">
                              <caption className="visually-hidden">{e.code}: Ethernet points per section</caption>
                              <thead><tr><th scope="col">Section</th><th scope="col" className="num">Points</th></tr></thead>
                              <tbody>{e.cable_planning.by_section.map((s) => (
                                <tr key={s.section}><th scope="row">{s.section}</th><td className="num">{n(s.points)}</td></tr>
                              ))}</tbody>
                            </table>
                          ) : <p className="muted small">No points in any section.</p>}
                          <p className="muted small">{e.cable_planning.by_venue.map((v) => `${v.venue}: ${n(v.points)}`).join(' · ')}</p>
                        </div>
                      ))}
                    </details>
                    <Source text={data.sources.cable_planning} />
                  </section>

                  <section className="panel report-section" aria-labelledby="rep-requests">
                    <h2 id="rep-requests">2. ICT request adoption</h2>
                    <p className="muted small">Objective: AV, Photographers &amp; Videographers, Engineering and CCTV request connectivity through the standard form, on time.</p>
                    {(() => {
                      const values = events.map((e) => e.requests.on_time_pct)
                      const known = withData(values)
                      const title = !known ? 'No ICT requests yet'
                        : `${met(values, goals.requests_on_time_pct)} of ${known} event${known === 1 ? '' : 's'} with requests met the on-time goal`
                      return (
                        <Figure id="fig-ontime" title={title} subtitle="ICT requests submitted on or before the deadline, % per event"
                          percent goal={goals.requests_on_time_pct}
                          items={events.map((e) => ({ key: e.id, label: e.code, value: e.requests.on_time_pct }))}
                          description={`On time: ${events.map((e) => `${e.code} ${pct(e.requests.on_time_pct)}`).join(', ')}.`} />
                      )
                    })()}
                    <Compare caption="ICT request adoption by event" events={events} rows={[
                      ['Requests', (e) => n(e.requests.requests)],
                      ['Teams using the form', (e) => `${n(e.requests.teams_using)} of ${Object.keys(teams).length}`],
                      ['Submitted on time', (e) => <Goal value={e.requests.on_time_pct} goal={goals.requests_on_time_pct} />],
                      ["With 'not sure' answers", (e) => pct(e.requests.not_sure_pct), 'Lower is better'],
                      ['Mean review time', (e) => hours(e.requests.review_hours)],
                      ['Waiting for review', (e) => n(e.requests.open)],
                      ...Object.entries(teams).map(([key, label]) => [
                        label, (e) => {
                          const t = e.requests.by_team[key]
                          return t.requests ? `${n(t.requests)} · ${n(t.ports_requested)} ports · ${pct(t.on_time_pct)} on time` : '—'
                        },
                      ]),
                    ]} />
                    <Source text={data.sources.requests} />
                  </section>

                  <section className="panel report-section" aria-labelledby="rep-ops">
                    <h2 id="rep-ops">3. Live operations</h2>
                    <p className="muted small">Objective: every ICT issue on site tracked, acknowledged quickly and closed with a documented cause.</p>
                    {(() => {
                      const values = events.map((e) => e.operations.ack_within_target_pct)
                      const known = withData(values)
                      const title = !known ? 'No help desk tickets yet'
                        : `${met(values, goals.ack_within_target_pct)} of ${known} event${known === 1 ? '' : 's'} with tickets met the 5-minute goal`
                      return (
                        <Figure id="fig-ack" title={title} subtitle="Tickets acknowledged within 5 minutes, % per event"
                          percent goal={goals.ack_within_target_pct}
                          items={events.map((e) => ({ key: e.id, label: e.code, value: e.operations.ack_within_target_pct }))}
                          description={`Acknowledged within 5 minutes: ${events.map((e) => `${e.code} ${pct(e.operations.ack_within_target_pct)}`).join(', ')}.`} />
                      )
                    })()}
                    <Compare caption="Live operations by event" events={events} rows={[
                      ['Tickets', (e) => n(e.operations.tickets)],
                      ['Acknowledged within 5 min', (e) => <Goal value={e.operations.ack_within_target_pct} goal={goals.ack_within_target_pct} />],
                      ['Median time to acknowledge', (e) => minutes(e.operations.ack_median_minutes)],
                      ['Closed with root cause and fix', (e) => <Goal value={e.operations.closure_quality_pct} goal={goals.closure_quality_pct} />],
                      ['Mean time to resolve', (e) => minutes(e.operations.resolve_mean_minutes)],
                      ['Partner-impacting tickets', (e) => `${n(e.operations.partner_impacting)} · ${minutes(e.operations.partner_mean_minutes)} to resolve`],
                      ['Recurring issues', (e) => pct(e.operations.recurrence_pct), 'Lower is better'],
                    ]} />
                    <Source text={data.sources.operations} />
                  </section>

                  <section className="panel report-section" aria-labelledby="rep-pub">
                    <h2 id="rep-pub">4. Change and publishing</h2>
                    <p className="muted small">How stable the plan was: versions, and what changed from the first to the last published version.</p>
                    <Compare caption="Change and publishing by event" events={events} rows={[
                      ['Versions', (e) => `${n(e.publishing.versions)} (${n(e.publishing.published)} published)`],
                      ['First published', (e) => day(e.publishing.first_published)],
                      ['Last published', (e) => day(e.publishing.last_published)],
                      ['First → last version', (e) => {
                        const f = e.publishing.first_to_last
                        return f ? `V${f.from} → V${f.to}: ${n(f.added)} added · ${n(f.removed)} removed · ${n(f.changed)} changed` : '—'
                      }],
                      ['Ethernet points first → last', (e) => (e.publishing.first_to_last ? signed(e.publishing.first_to_last.points_delta) : '—')],
                      ['Unpublished changes', (e) => n(e.publishing.unpublished_changes)],
                    ]} />
                    <Source text={data.sources.publishing} />
                  </section>

                  <section className="panel report-section" aria-labelledby="rep-data">
                    <h2 id="rep-data">Download the data</h2>
                    <p className="muted small">The same figures as above, one column per event, for the selected events.</p>
                    <div className="row">
                      <button type="button" onClick={() => exportAs('xlsx')}>Download the report data (Excel, XLSX)</button>
                      <button type="button" className="secondary" onClick={() => exportAs('csv')}>Download the report data (CSV)</button>
                    </div>
                    <details className="report-more">
                      <summary>Sources and definitions</summary>
                      <dl className="definitions">
                        {Object.entries(data.definitions).map(([term, text]) => (
                          <div key={term}><dt>{term}</dt><dd>{text}</dd></div>
                        ))}
                      </dl>
                      <p className="muted small">Goals: acknowledged within 5 minutes {pct(goals.ack_within_target_pct)}, closed with root cause and fix {pct(goals.closure_quality_pct)}, ICT requests on time {pct(goals.requests_on_time_pct)}.</p>
                    </details>
                  </section>
                </>
              )}
            </>
          )}
        </div>
      </main>
    </>
  )
}
