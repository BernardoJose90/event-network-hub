import { useCallback, useEffect, useId, useMemo, useState } from 'react'
import { download } from '../helpdesk/api.js'
import { loadIdentity, saveIdentity } from '../identity.js'
import { StatusMessages } from '../ui/Notice.jsx'
import TopBar from '../ui/TopBar.jsx'

// Reports, /reports: one set of measures per event, grouped by objective, events side by side.

function headers(identity) {
  const h = {}
  if (identity.role) h['X-Role'] = identity.role
  if (identity.user) h['X-User'] = identity.user
  return h
}

const pct = (v) => (v === null || v === undefined ? '—' : `${v}%`)
const num = (v) => (v === null || v === undefined ? '—' : v)
const hours = (v) => (v === null || v === undefined ? '—' : `${v} h`)
const minutes = (v) => (v === null || v === undefined ? '—' : `${v} min`)
const day = (v) => (v ? new Date(v).toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' }) : '—')

// A value against its goal: text, never colour alone.
function Goal({ value, goal, lowerIsBetter = false }) {
  if (value === null || value === undefined) return <span className="muted">—</span>
  const ok = lowerIsBetter ? value <= goal : value >= goal
  return (
    <span className={ok ? 'goal-ok' : 'goal-miss'}>
      {lowerIsBetter ? value : `${value}%`}
      <span className="small"> {ok ? '· on target' : `· goal ${lowerIsBetter ? goal : `${goal}%`}`}</span>
    </span>
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

function Bars({ items, label }) {
  const max = Math.max(1, ...items.map((i) => i.value))
  return (
    <ul className="bars" aria-label={label}>
      {items.map((i) => (
        <li key={i.key}>
          <span className="bar-label">{i.label}</span>
          <span className="bar-track" aria-hidden="true"><span className="bar-fill" style={{ width: `${(100 * i.value) / max}%` }} /></span>
          <span className="bar-value">{i.value}</span>
        </li>
      ))}
    </ul>
  )
}

export default function ReportsPage() {
  const [identity, setIdentity] = useState(loadIdentity)
  useEffect(() => saveIdentity(identity), [identity])
  const [data, setData] = useState(null)
  const [chosen, setChosen] = useState(null) // event ids; null = server default (all active)
  const [notice, setNotice] = useState(null)
  const [error, setError] = useState(null)
  const pickerId = useId()

  const query = chosen ? `?events=${chosen.join(',')}` : ''
  const load = useCallback(async () => {
    try {
      const res = await fetch(`/api/reports${query}`, { headers: headers(identity) })
      const body = await res.json().catch(() => null)
      if (!res.ok) throw Object.assign(new Error(body?.detail || `Request failed (HTTP ${res.status})`), { status: res.status })
      setData(body)
      setError(null)
    } catch (err) {
      setError(err.status === 403 ? 'Choose a role (Viewer or Network admin) in the top bar to see reports.' : err.message)
    }
  }, [query, identity])
  useEffect(() => { load() }, [load])
  useEffect(() => { document.title = 'Reports · Event Network Hub' }, [])

  const selected = useMemo(() => new Set(chosen ?? data?.events.map((e) => e.id) ?? []), [chosen, data])

  function toggle(id, on) {
    const next = new Set(selected)
    if (on) next.add(id)
    else next.delete(id)
    const ordered = (data?.available || []).map((e) => e.id).filter((x) => next.has(x))
    if (ordered.length) setChosen(ordered)
  }

  async function exportExcel() {
    try {
      await download(`/api/reports/export${query}`, identity)
    } catch (err) {
      setNotice({ message: err.message, tone: 'error' })
    }
  }

  const events = data?.events || []
  const goals = data?.goals || {}
  const teams = data?.teams || {}

  return (
    <>
      <a className="skip-link" href="#main">Skip to main content</a>
      <TopBar identity={identity} onChange={setIdentity} current="reports" />
      <main id="main" className="reports-page" tabIndex={-1}>
        <div className="hd-bar">
          <h1 className="hd-title">Reports</h1>
          <p className="muted small">How each event did against the ICT objectives, side by side.</p>
          {data && <button type="button" className="hd-new" onClick={exportExcel}>Export Excel</button>}
        </div>
        <StatusMessages notice={notice} onDismiss={() => setNotice(null)} className="hd-notice" />
        <div className="hd-main">
          {error && <p className="error" role="alert">{error}</p>}
          {!data && !error && <div className="skeleton-block" aria-hidden="true" />}
          {data && (
            <>
              <fieldset className="panel event-picker" aria-describedby={pickerId}>
                <legend className="field-label">Events to compare</legend>
                <p id={pickerId} className="muted small">Archived events can be included for comparison.</p>
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
                  <section className="panel report-section" aria-labelledby="rep-cables">
                    <h2 id="rep-cables">1. Cable planning</h2>
                    <p className="muted small">Fewer unused cables and fewer &ldquo;unknown&rdquo; requirements: points planned per event,
                      how many came through ICT requests, and what arrived after the request deadline or on site.</p>
                    <Bars label="Ethernet points per event" items={events.map((e) => ({ key: e.id, label: e.code, value: e.cable_planning.points }))} />
                    <Compare caption="Cable planning by event" events={events} rows={[
                      ['Ethernet points', (e) => num(e.cable_planning.points)],
                      ['Ethernet items', (e) => num(e.cable_planning.items)],
                      ['Points via ICT requests', (e) => `${e.cable_planning.points_via_requests} (${pct(e.cable_planning.via_requests_pct)})`],
                      ['Request deadline', (e) => e.cable_planning.deadline || '—'],
                      ['Added after the deadline', (e) => (e.cable_planning.items_added_after_deadline === null ? '—'
                        : `${e.cable_planning.items_added_after_deadline} items · ${e.cable_planning.points_added_after_deadline} points`),
                      'Ethernet items created after the request deadline'],
                      ['Wired tickets on site', (e) => num(e.cable_planning.wired_tickets_on_site), 'Help desk tickets in the Wired category'],
                    ]} />
                    <details className="report-more">
                      <summary>Points per venue and section</summary>
                      {events.map((e) => (
                        <div key={e.id} className="report-breakdown">
                          <h3>{e.code}</h3>
                          <Bars label={`${e.code}: points per section`} items={e.cable_planning.by_section.map((s) => ({ key: s.section, label: s.section, value: s.points }))} />
                          <p className="muted small">{e.cable_planning.by_venue.map((v) => `${v.venue}: ${v.points}`).join(' · ')}</p>
                        </div>
                      ))}
                    </details>
                  </section>

                  <section className="panel report-section" aria-labelledby="rep-requests">
                    <h2 id="rep-requests">2. ICT request adoption</h2>
                    <p className="muted small">Are AV, Photographers &amp; Videographers, Engineering and CCTV using the request form, on time and with complete answers?</p>
                    <Compare caption="ICT request adoption by event" events={events} rows={[
                      ['Requests', (e) => num(e.requests.requests)],
                      ['Teams using the form', (e) => `${e.requests.teams_using} of ${Object.keys(teams).length}`],
                      ['Submitted on time', (e) => <Goal value={e.requests.on_time_pct} goal={goals.requests_on_time_pct} />],
                      ["With 'not sure' answers", (e) => pct(e.requests.not_sure_pct), 'Lower is better'],
                      ['Mean review time', (e) => hours(e.requests.review_hours)],
                      ['Waiting for review', (e) => num(e.requests.open)],
                      ...Object.entries(teams).map(([key, label]) => [
                        label, (e) => {
                          const t = e.requests.by_team[key]
                          return t.requests ? `${t.requests} · ${t.ports_requested} ports · ${pct(t.on_time_pct)} on time` : '—'
                        },
                      ]),
                    ]} />
                  </section>

                  <section className="panel report-section" aria-labelledby="rep-register">
                    <h2 id="rep-register">3. Dependency register health</h2>
                    <p className="muted small">Delivery that doesn&apos;t rely on one person: are the areas in use covered, owned, reviewed and runbooked?</p>
                    <Compare caption="Dependency register health by event" events={events} rows={[
                      ['Entries', (e) => num(e.register.entries)],
                      ['Areas covered', (e) => (e.register.areas_in_use
                        ? <><Goal value={e.register.coverage_pct} goal={goals.register_coverage_pct} /><div className="muted small">{e.register.areas_covered} of {e.register.areas_in_use} areas in use</div></>
                        : '—')],
                      ['Without an accountable owner', (e) => <Goal value={e.register.without_owner} goal={0} lowerIsBetter />],
                      ['Needing review', (e) => <Goal value={e.register.needs_review} goal={0} lowerIsBetter />],
                      ['P1 without a runbook', (e) => <Goal value={e.register.p1_without_runbook} goal={0} lowerIsBetter />],
                      ['By criticality', (e) => Object.entries(e.register.by_criticality).map(([k, v]) => `${k} ${v}`).join(' · ')],
                    ]} />
                    {events.some((e) => e.register.uncovered_areas.length) && (
                      <details className="report-more">
                        <summary>Areas in use with no register entry</summary>
                        {events.filter((e) => e.register.uncovered_areas.length).map((e) => (
                          <p key={e.id}><strong>{e.code}:</strong> {e.register.uncovered_areas.join(', ')}</p>
                        ))}
                      </details>
                    )}
                  </section>

                  <section className="panel report-section" aria-labelledby="rep-ops">
                    <h2 id="rep-ops">4. Live operations</h2>
                    <p className="muted small">Help desk results per event. Details are under Help desk → Reports.</p>
                    <Compare caption="Live operations by event" events={events} rows={[
                      ['Tickets', (e) => num(e.operations.tickets)],
                      ['Acknowledged within 5 min', (e) => <Goal value={e.operations.ack_within_target_pct} goal={goals.ack_within_target_pct} />],
                      ['Median time to acknowledge', (e) => minutes(e.operations.ack_median_minutes)],
                      ['Closed with root cause and fix', (e) => <Goal value={e.operations.closure_quality_pct} goal={goals.closure_quality_pct} />],
                      ['Mean time to resolve', (e) => minutes(e.operations.resolve_mean_minutes)],
                      ['Partner-impacting tickets', (e) => `${e.operations.partner_impacting} · ${minutes(e.operations.partner_mean_minutes)} to resolve`],
                      ['Recurring issues', (e) => pct(e.operations.recurrence_pct), 'Same category and area as an earlier ticket'],
                    ]} />
                  </section>

                  <section className="panel report-section" aria-labelledby="rep-pub">
                    <h2 id="rep-pub">5. Change and publishing</h2>
                    <p className="muted small">How stable the plan was: versions, and what changed from the first to the last published version.</p>
                    <Compare caption="Change and publishing by event" events={events} rows={[
                      ['Versions', (e) => `${e.publishing.versions} (${e.publishing.published} published)`],
                      ['First published', (e) => day(e.publishing.first_published)],
                      ['Last published', (e) => day(e.publishing.last_published)],
                      ['First → last version', (e) => {
                        const f = e.publishing.first_to_last
                        return f ? `V${f.from} → V${f.to}: ${f.added} added · ${f.removed} removed · ${f.changed} changed` : '—'
                      }],
                      ['Ethernet points first → last', (e) => {
                        const f = e.publishing.first_to_last
                        return f ? `${f.points_delta >= 0 ? '+' : ''}${f.points_delta}` : '—'
                      }],
                      ['Unpublished changes', (e) => num(e.publishing.unpublished_changes)],
                    ]} />
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
