import { useCallback, useEffect, useId, useMemo, useState } from 'react'
import { useDialog } from '../ui/Dialog.jsx'

// Network dependency register: per area, what relies on the network, who is accountable
// (RACI: one Accountable), how critical it is, the network items it uses and the runbook.

const CRITICALITY = {
  P1: 'P1 Critical — the event stops or a keynote/stage/registration fails',
  P2: 'P2 High — a critical area or partner is affected',
  P3: 'P3 Medium — degraded, with a workaround',
  P4: 'P4 Low — inconvenient',
}
const TEAMS = { network: 'Network team', it_support: 'IT Support' }
const PHASES = [['build', 'Build'], ['live', 'Live days'], ['breakdown', 'Breakdown']]

function fields(entry, { areas, vlans, items, teams }) {
  const d = entry?.data || {}
  return [
    { name: 'area', label: 'Area or space', required: true, maxLength: 200, list: areas, initial: d.area,
      hint: 'Use the same name as in Setup, e.g. "Main stage" or "Registration A"' },
    { name: 'service', label: 'What depends on the network', required: true, maxLength: 200, initial: d.service,
      hint: 'e.g. "Live stream encoders", "Badge printers", "CCTV recorders"' },
    { name: 'team', label: 'Team or process', maxLength: 200, list: teams, initial: d.team },
    { name: 'criticality', label: 'Criticality', type: 'select', required: true, initial: d.criticality || 'P3',
      options: Object.entries(CRITICALITY).map(([value, label]) => ({ value, label })) },
    { name: 'accountable', label: 'Accountable (one owner)', maxLength: 200, initial: d.accountable,
      hint: 'The one person or role who owns this dependency' },
    { name: 'responsible', label: 'Responsible ICT team', type: 'select', required: false, initial: d.responsible || '',
      options: Object.entries(TEAMS).map(([value, label]) => ({ value, label })) },
    { name: 'consulted', label: 'Consulted', maxLength: 200, initial: d.consulted },
    { name: 'informed', label: 'Informed', maxLength: 200, initial: d.informed },
    { name: 'contact', label: 'On-site contact', maxLength: 200, initial: d.contact, hint: 'Name and phone or radio channel' },
    { name: 'phase', label: 'Needed during', type: 'checkboxes', required: true, initial: d.phase || ['live'],
      options: PHASES.map(([value, label]) => ({ value, label })) },
    { name: 'vlans', label: 'VLANs it uses', type: 'checkboxes', initial: d.vlans || [], full: true,
      options: vlans.map((v) => ({ value: v, label: v })) },
    { name: 'records', label: 'Network items it uses', type: 'checkboxes', initial: d.records || [], full: true,
      options: items, hint: 'Wi-Fi networks and Ethernet items of this event' },
    { name: 'runbook', label: 'If it fails, do this', type: 'textarea', maxLength: 4000, initial: d.runbook,
      hint: 'Fallback and first steps, so anyone on shift can act' },
    { name: 'notes', label: 'Notes', type: 'textarea', maxLength: 4000, initial: d.notes },
  ]
}

const clean = (values) => Object.fromEntries(Object.entries(values).filter(([, v]) => v !== null && v !== ''))

export function DependenciesPanel({ api, overview, isAdmin, notify, onChanged }) {
  const dialog = useDialog()
  const [data, setData] = useState(null)
  const [items, setItems] = useState([])
  const [filters, setFilters] = useState({ q: '', criticality: '', vlan: '', review: false })
  const ids = { q: useId(), crit: useId(), vlan: useId(), review: useId() }

  const load = useCallback(async () => {
    try {
      const [deps, wifi, ethernet] = await Promise.all([api.dependencies(), api.records('wifi'), api.records('ethernet')])
      setData(deps)
      setItems([
        ...wifi.map((r) => ({ value: r.id, label: `Wi-Fi: ${r.data.ssid || r.data.ref_id || `#${r.id}`}` })),
        ...ethernet.map((r) => ({ value: r.id, label: `Ethernet: ${r.data.purpose || `#${r.id}`}${r.data.location ? ` (${r.data.location})` : ''}` })),
      ])
    } catch (err) {
      notify(err.message, 'error')
    }
  }, [api, notify])
  useEffect(() => { load() }, [load])

  const vlans = useMemo(() => overview.lists.filter((i) => i.kind === 'vlan').map((i) => i.value), [overview.lists])
  const areas = useMemo(() => [...new Set(overview.lists.filter((i) => i.kind === 'location' || i.kind === 'building')
    .map((i) => i.value))], [overview.lists])

  async function act(fn, message) {
    try {
      await fn()
      if (message) notify(message, 'ok')
      await load()
      onChanged()
    } catch (err) {
      notify(err.message, 'error', err.status === 409 ? load : undefined)
    }
  }

  async function edit(entry) {
    const values = await dialog.form({
      title: entry ? 'Edit dependency' : 'Add a dependency', wide: true,
      confirmLabel: entry ? 'Save' : 'Add dependency',
      body: entry ? 'Saving also marks the entry as reviewed.' : undefined,
      fields: fields(entry, { areas, vlans, items, teams: data.team_suggestions }),
    })
    if (!values) return
    const body = clean(values)
    await act(() => (entry ? api.updateDependency(entry.id, entry.revision, body) : api.createDependency(body)),
      entry ? 'Dependency saved.' : 'Dependency added.')
  }

  async function remove(entry) {
    const ok = await dialog.confirm({
      title: `Remove "${entry.data.service}"?`, body: `From ${entry.data.area}. The history keeps what it said.`,
      confirmLabel: 'Remove', danger: true,
    })
    if (ok) await act(() => api.deleteDependency(entry.id, entry.revision), 'Dependency removed.')
  }

  if (!data) return <div className="skeleton-block" aria-hidden="true" />

  const q = filters.q.trim().toLowerCase()
  const shown = data.entries.filter((e) => {
    const d = e.data
    if (filters.criticality && d.criticality !== filters.criticality) return false
    if (filters.vlan && !(d.vlans || []).includes(filters.vlan)) return false
    if (filters.review && !e.needs_review && !e.missing_owner) return false
    if (!q) return true
    return [d.area, d.service, d.team, d.accountable, d.contact, d.runbook].some((v) => v && v.toLowerCase().includes(q))
  })
  const byArea = new Map()
  for (const e of [...shown].sort((a, b) => a.data.criticality.localeCompare(b.data.criticality))) {
    const key = e.data.area
    if (!byArea.has(key)) byArea.set(key, [])
    byArea.get(key).push(e)
  }
  const attention = data.entries.filter((e) => e.needs_review || e.missing_owner).length

  return (
    <div className="deps">
      <p className="muted">
        Who and what relies on the network in each area, who is accountable, and what to do if it fails, so delivery
        doesn&apos;t depend on one person&apos;s knowledge. Entries are copied into templates and new events; review them
        each event (entries older than {data.review_after_days} days are flagged).
      </p>
      <div className="hd-filters">
        <label htmlFor={ids.q} className="visually-hidden">Search dependencies</label>
        <input id={ids.q} type="search" placeholder="Search area, service, team, owner…" value={filters.q}
          onChange={(e) => setFilters((f) => ({ ...f, q: e.target.value }))} />
        <label className="inline-label" htmlFor={ids.crit}>Criticality</label>
        <select id={ids.crit} value={filters.criticality} onChange={(e) => setFilters((f) => ({ ...f, criticality: e.target.value }))}>
          <option value="">Any</option>
          {Object.keys(CRITICALITY).map((k) => <option key={k} value={k}>{k}</option>)}
        </select>
        <label className="inline-label" htmlFor={ids.vlan}>If this VLAN fails</label>
        <select id={ids.vlan} value={filters.vlan} onChange={(e) => setFilters((f) => ({ ...f, vlan: e.target.value }))}>
          <option value="">Any VLAN</option>
          {vlans.map((v) => <option key={v} value={v}>{v}</option>)}
        </select>
        <label className="inline-label" htmlFor={ids.review}>
          <input id={ids.review} type="checkbox" checked={filters.review}
            onChange={(e) => setFilters((f) => ({ ...f, review: e.target.checked }))} />
          Needs attention ({attention})
        </label>
        {isAdmin && <button type="button" className="hd-new" onClick={() => edit(null)}>+ Add dependency</button>}
      </div>
      {filters.vlan && (
        <p className="notice info" role="status">
          {shown.length} dependenc{shown.length === 1 ? 'y' : 'ies'} in {byArea.size} area{byArea.size === 1 ? '' : 's'} would be
          affected if <strong>{filters.vlan}</strong> failed.
        </p>
      )}
      {data.entries.length === 0 && (
        <div className="panel ov-empty">
          <h3>No dependencies recorded yet</h3>
          <p className="muted">Start with the critical areas: stages, registration, broadcast, CCTV and partner zones.</p>
        </div>
      )}
      {[...byArea.entries()].map(([area, entries]) => (
        <section key={area} className="dep-area" aria-labelledby={`dep-${entries[0].id}`}>
          <h3 id={`dep-${entries[0].id}`}>{area}</h3>
          <div className="dep-cards">
            {entries.map((e) => {
              const d = e.data
              return (
                <article key={e.id} className="panel dep-card" aria-label={`${d.service}, ${d.criticality}`}>
                  <div className="row space-between">
                    <h4>{d.service}</h4>
                    <span className={`badge-pill sev-${d.criticality}`}>{d.criticality}</span>
                  </div>
                  <dl className="facts">
                    <div><dt>Team or process</dt><dd>{d.team || '—'}</dd></div>
                    <div><dt>Accountable</dt><dd>{d.accountable || <span className="flag-text">No owner</span>}</dd></div>
                    <div><dt>Responsible (ICT)</dt><dd>{TEAMS[d.responsible] || '—'}</dd></div>
                    <div><dt>Consulted / informed</dt><dd>{[d.consulted, d.informed].filter(Boolean).join(' / ') || '—'}</dd></div>
                    <div><dt>Needed during</dt><dd>{(d.phase || []).map((p) => PHASES.find(([k]) => k === p)?.[1]).join(', ')}</dd></div>
                    <div><dt>On-site contact</dt><dd>{d.contact || '—'}</dd></div>
                    <div><dt>VLANs</dt><dd>{(d.vlans || []).join(', ') || '—'}</dd></div>
                    <div><dt>Network items</dt><dd>{e.linked.map((l) => l.name).join(', ') || '—'}</dd></div>
                  </dl>
                  {d.runbook && <p className="runbook"><strong>If it fails:</strong> <span className="pre-wrap">{d.runbook}</span></p>}
                  {d.notes && <p className="muted small pre-wrap">{d.notes}</p>}
                  <div className="row space-between dep-foot">
                    <span className={`small ${e.needs_review ? 'flag-text' : 'muted'}`}>
                      {e.last_reviewed_at
                        ? `Reviewed ${new Date(e.last_reviewed_at).toLocaleDateString()} by ${e.last_reviewed_by}`
                        : 'Never reviewed'}
                      {e.needs_review ? ' · needs review' : ''}
                    </span>
                    {isAdmin && (
                      <span className="row nowrap">
                        <button type="button" className="secondary" onClick={() => act(() => api.reviewDependency(e.id, e.revision), 'Marked as reviewed.')}
                          aria-label={`Confirm ${d.service} is still correct`}>Still correct</button>
                        <button type="button" className="secondary" onClick={() => edit(e)} aria-label={`Edit ${d.service}`}>Edit</button>
                        <button type="button" className="danger" onClick={() => remove(e)} aria-label={`Remove ${d.service}`}>Remove</button>
                      </span>
                    )}
                  </div>
                </article>
              )
            })}
          </div>
        </section>
      ))}
    </div>
  )
}
