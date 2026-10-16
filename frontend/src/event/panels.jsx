import { useCallback, useEffect, useState } from 'react'
import { TITLES } from './columns.js'

// --- Overview ----------------------------------------------------------------

export function Overview({ overview, api, isAdmin, notify, onChanged, onPublish, goTo }) {
  const [changes, setChanges] = useState([])
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!overview.has_records) return
    api.changes().then(setChanges).catch((err) => notify(err.message, 'error'))
  }, [api, overview, notify])

  async function importLatest(replace) {
    if (replace && !window.confirm(
      `Replace all requirements with the contents of V${overview.latest_version}? Current edits that weren't published will be lost. The history is kept.`,
    )) return
    setBusy(true)
    try {
      const result = await api.importVersion(replace)
      const notes = result.warnings.length ? ` ${result.warnings.length} note(s): ${result.warnings.join(' · ')}` : ''
      notify(`Imported from V${overview.latest_version}.${notes}`, result.warnings.length ? 'info' : 'ok')
      onChanged()
    } catch (err) {
      notify(err.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  if (!overview.has_records) {
    return (
      <div className="ov-empty panel">
        <h3>No requirements yet</h3>
        {overview.venues.length > 0 && (
          <>
            <p className="muted">
              Set up with {overview.venues.length === 1 ? 'one venue' : `${overview.venues.length} venues`} (
              {overview.venues.map((v) => v.name).join(', ')}).{' '}
              {isAdmin
                ? 'Add requirements on each tab. VLANs and locations come from Setup, so add those values first. Publishing creates the next version.'
                : 'A network admin is adding the requirements.'}
            </p>
            <div className="row">
              {['wifi', 'ethernet', 'partner', 'vlan_extension', 'lists'].map((key) => (
                <button key={key} className="secondary" onClick={() => goTo(key)}>{TITLES[key] || 'Setup'}</button>
              ))}
            </div>
          </>
        )}
        {overview.venues.length > 0 ? (
          isAdmin && overview.latest_version && (
            <p className="muted small">
              Or{' '}
              <button className="link" onClick={() => importLatest(true)} disabled={busy}>
                replace this setup with the workbook uploaded as V{overview.latest_version}
              </button>
              .
            </p>
          )
        ) : overview.latest_version ? (
          isAdmin ? (
            <>
              <p className="muted">Bring in the workbook uploaded as V{overview.latest_version}: venues, Wi-Fi networks, Ethernet requirements, partner orders, VLAN extensions and the Setup lists.</p>
              <button onClick={() => importLatest(false)} disabled={busy}>
                {busy ? 'Importing…' : `Import from V${overview.latest_version}`}
              </button>
            </>
          ) : (
            <p className="muted">A network admin needs to import them first.</p>
          )
        ) : (
          <p className="muted">Upload a workbook on the documents page first.</p>
        )}
      </div>
    )
  }

  const isTemplate = overview.document.kind === 'template'
  const sections = overview.totals.ethernet[0]?.sections.map((s) => s.section) || []
  const count = (type) => overview.counts[type] || 0
  const totalPoints = overview.totals.ethernet.reduce((sum, t) => sum + t.total, 0)
  return (
    <div className="ov">
      <div className="ov-cards">
        {overview.totals.ethernet.map((t) => (
          <div key={t.venue_id} className="panel ov-card">
            <h3>{t.venue}</h3>
            <p><strong>{t.total}</strong> <span className="small">Ethernet points</span></p>
          </div>
        ))}
        {['wifi', 'ethernet', 'partner', 'vlan_extension'].filter((t) => !(isTemplate && t === 'partner')).map((type) => (
          <button key={type} className="panel ov-card link-card" onClick={() => goTo(type)}>
            <h3>{TITLES[type]}</h3>
            {type === 'ethernet' ? (
              <p>
                <strong>{totalPoints}</strong> points
                <span className="muted small"> · {count(type)} item{count(type) === 1 ? '' : 's'}</span>
                {overview.archived?.[type] ? <span className="muted small"> · {overview.archived[type]} archived</span> : null}
              </p>
            ) : (
              <p><strong>{count(type)}</strong>{overview.archived?.[type] ? <span className="muted"> · {overview.archived[type]} archived</span> : null}</p>
            )}
          </button>
        ))}
      </div>

      {overview.flagged_list_values > 0 && (
        <div className="notice info">
          {overview.flagged_list_values} list value(s) were added by the import because records used them.{' '}
          <button className="link" onClick={() => goTo('lists')}>Review them</button>
        </div>
      )}

      <div className="panel">
        <h3>Ethernet points per section</h3>
        <table className="ov-table">
          <thead>
            <tr>
              <th>Section</th>
              {overview.totals.ethernet.map((t) => <th key={t.venue_id} className="num">{t.venue}</th>)}
            </tr>
          </thead>
          <tbody>
            {sections.map((name, i) => (
              <tr key={name}>
                <td>{name}</td>
                {overview.totals.ethernet.map((t) => <td key={t.venue_id} className="num">{t.sections[i].ports}</td>)}
              </tr>
            ))}
            <tr className="ov-total">
              <td>Total</td>
              {overview.totals.ethernet.map((t) => <td key={t.venue_id} className="num">{t.total}</td>)}
            </tr>
          </tbody>
        </table>
      </div>

      {isTemplate ? (
        <div className="panel">
          <h3>Template</h3>
          <p className="muted small">
            New events can start from this template (&ldquo;+ New event&rdquo; → Start from). They get a copy of everything
            here; later changes to the template don&apos;t affect events already created. Templates have no partner
            orders and aren&apos;t published.
          </p>
        </div>
      ) : (
      <div className="panel">
        <div className="row space-between">
          <h3>
            {changes.length} change{changes.length === 1 ? '' : 's'} not yet published
            {overview.latest_version ? <span className="muted"> · latest version V{overview.latest_version}</span> : null}
          </h3>
          {isAdmin && (
            <button onClick={onPublish} disabled={!changes.length}>
              Publish V{(overview.latest_version || 0) + 1}
            </button>
          )}
        </div>
        {changes.length > 0 ? (
          <ul className="ov-changes">
            {changes.slice(-50).reverse().map((c) => (
              <li key={c.id}><span className="muted">{c.at.replace('T', ' ').slice(0, 16)} · {c.user}</span> {c.summary}</li>
            ))}
          </ul>
        ) : (
          <p className="muted small">Everything is published.</p>
        )}
      </div>
      )}

      {isAdmin && overview.latest_version && (
        <details className="ov-danger">
          <summary>Import again from a workbook</summary>
          <p className="muted small">Replaces all requirements with the contents of V{overview.latest_version} (for example after uploading a corrected workbook). The history is kept.</p>
          <button className="danger" onClick={() => importLatest(true)} disabled={busy}>Replace from V{overview.latest_version}</button>
        </details>
      )}
    </div>
  )
}

// --- VLAN extensions ------------------------------------------------------------

export function VlanMatrix({ api, overview, isAdmin, notify, onChanged }) {
  const [rows, setRows] = useState([])
  const vlans = overview.lists.filter((i) => i.kind === 'vlan').map((i) => i.value)

  const load = useCallback(() => {
    api.records('vlan_extension').then(setRows).catch((err) => notify(err.message, 'error'))
  }, [api, notify])
  useEffect(load, [load])

  async function save(row, data, message) {
    try {
      const updated = await api.update('vlan_extension', row.id, row.revision, data)
      setRows((rs) => rs.map((r) => (r.id === row.id ? updated : r)))
      if (message) notify(message, 'ok')
      onChanged()
    } catch (err) {
      notify(err.message, 'error', err.status === 409 ? load : undefined)
    }
  }

  async function add() {
    try {
      const created = await api.create('vlan_extension', { data: { venues: {} } })
      setRows((rs) => [...rs, created])
      onChanged()
    } catch (err) {
      notify(err.message, 'error')
    }
  }

  async function archive(row) {
    try {
      await api.archive('vlan_extension', row.id, row.revision)
      setRows((rs) => rs.filter((r) => r.id !== row.id))
      onChanged()
    } catch (err) {
      notify(err.message, 'error')
    }
  }

  return (
    <div className="panel">
      <table className="rec-table matrix">
        <thead>
          <tr>
            <th>VLAN</th>
            {overview.venues.map((v) => <th key={v.id} className="center">{v.name}</th>)}
            {isAdmin && <th />}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>
                {isAdmin ? (
                  <select value={row.data.vlan || ''} onChange={(e) => save(row, { vlan: e.target.value || null })}>
                    <option value="">—</option>
                    {[...new Set([row.data.vlan, ...vlans].filter(Boolean))].map((v) => <option key={v}>{v}</option>)}
                  </select>
                ) : (
                  row.data.vlan
                )}
              </td>
              {overview.venues.map((v) => (
                <td key={v.id} className="center">
                  <input type="checkbox" disabled={!isAdmin} checked={!!row.data.venues?.[v.id]}
                    aria-label={`${row.data.vlan || 'VLAN'} at ${v.name}`}
                    onChange={(e) => save(row, { venues: { ...row.data.venues, [v.id]: e.target.checked } })} />
                </td>
              ))}
              {isAdmin && <td className="rec-actions"><button className="archive" onClick={() => archive(row)}>Archive</button></td>}
            </tr>
          ))}
          {rows.length === 0 && <tr><td colSpan={overview.venues.length + 2} className="muted">No VLAN extensions.</td></tr>}
        </tbody>
      </table>
      {isAdmin && <button className="secondary rec-add-bottom" onClick={add}>+ Add VLAN</button>}
    </div>
  )
}

// --- Lists, venues, sections ------------------------------------------------------

function EditableItem({ label, flagged, isAdmin, onRename, onDelete, onAccept }) {
  const [editing, setEditing] = useState(false)
  if (editing) {
    return (
      <li className="list-item">
        <input autoFocus defaultValue={label}
          onKeyDown={(e) => {
            if (e.key === 'Enter') { setEditing(false); if (e.currentTarget.value.trim() !== label) onRename(e.currentTarget.value) }
            if (e.key === 'Escape') setEditing(false)
          }}
          onBlur={(e) => { setEditing(false); if (e.target.value.trim() !== label) onRename(e.target.value) }} />
      </li>
    )
  }
  return (
    <li className="list-item">
      <span className={isAdmin ? 'editable' : undefined} onClick={() => isAdmin && setEditing(true)}
        title={isAdmin ? 'Click to rename' : undefined}>{label}</span>
      {flagged && <span className="flag" title="Added by the import because records used it">review</span>}
      {isAdmin && flagged && <button className="link" onClick={onAccept}>Keep</button>}
      {isAdmin && <button className="icon" title="Remove" onClick={onDelete}>×</button>}
    </li>
  )
}

function ListBox({ title, items, isAdmin, onAdd, help }) {
  const [value, setValue] = useState('')
  return (
    <div className="panel list-box">
      <h3>{title} <span className="muted small">{items.length}</span></h3>
      {help && <p className="muted small">{help}</p>}
      {isAdmin && (
        <form className="row" onSubmit={(e) => { e.preventDefault(); if (value.trim()) { onAdd(value.trim()); setValue('') } }}>
          <input value={value} onChange={(e) => setValue(e.target.value)} placeholder={`Add to ${title.toLowerCase()}`} maxLength={200} />
          <button type="submit" disabled={!value.trim()}>Add</button>
        </form>
      )}
      <ul className="list-items">{items}</ul>
    </div>
  )
}

export function ListsPanel({ api, overview, isAdmin, notify, onChanged }) {
  const run = async (fn, message) => {
    try {
      await fn()
      if (message) notify(message, 'ok')
      onChanged()
    } catch (err) {
      notify(err.message, 'error')
    }
  }
  const lists = [
    ['vlan', 'VLANs'], ['location', 'Locations'], ['building', 'Buildings'], ['bandwidth', 'Bandwidths'],
  ]
  const item = (i) => (
    <EditableItem key={i.id} label={i.value} flagged={i.flagged} isAdmin={isAdmin}
      onRename={(v) => run(() => api.renameListValue(i.id, v), `Renamed to '${v}' everywhere it is used`)}
      onDelete={() => window.confirm(`Remove '${i.value}' from the list?`) && run(() => api.deleteListValue(i.id))}
      onAccept={() => run(() => api.acceptListValue(i.id))} />
  )
  const named = (x, rename, remove) => (
    <EditableItem key={x.id} label={x.name} isAdmin={isAdmin}
      onRename={(v) => run(() => rename(x.id, v))}
      onDelete={() => window.confirm(`Remove '${x.name}'?`) && run(() => remove(x.id))} />
  )
  return (
    <div className="lists-grid">
      <ListBox title="Venues" isAdmin={isAdmin} onAdd={(v) => run(() => api.addVenue(v))}
        items={overview.venues.map((v) => named(v, api.renameVenue, api.deleteVenue))} />
      <ListBox title="Ethernet sections" isAdmin={isAdmin} onAdd={(v) => run(() => api.addGroup('ethernet', v))}
        items={overview.groups.filter((g) => g.kind === 'ethernet').map((g) => named(g, api.renameGroup, api.deleteGroup))} />
      <ListBox title="Wi-Fi groups" isAdmin={isAdmin} onAdd={(v) => run(() => api.addGroup('wifi', v))}
        items={overview.groups.filter((g) => g.kind === 'wifi').map((g) => named(g, api.renameGroup, api.deleteGroup))} />
      {lists.map(([kind, title]) => (
        <ListBox key={kind} title={title} isAdmin={isAdmin} onAdd={(v) => run(() => api.addListValue(kind, v))}
          help={kind === 'vlan' ? 'Renaming a VLAN updates every record that uses it.' : undefined}
          items={overview.lists.filter((i) => i.kind === kind).map(item)} />
      ))}
    </div>
  )
}

// --- History ----------------------------------------------------------------------

const ENTITY_LABELS = {
  '': 'Everything', wifi: 'Wi-Fi networks', ethernet: 'Ethernet', partner: 'Partner orders',
  vlan_extension: 'VLAN extensions', list: 'Setup lists', venue: 'Venues', group: 'Sections', event: 'Imports & publishing',
  legacy: 'Old change log (imported)',
}

export function HistoryPanel({ api, notify }) {
  const [entity, setEntity] = useState('')
  const [user, setUser] = useState('')
  const [entries, setEntries] = useState([])

  useEffect(() => {
    const t = setTimeout(() => {
      api.history({ entity, user, limit: 1000 }).then(setEntries).catch((err) => notify(err.message, 'error'))
    }, 250)
    return () => clearTimeout(t)
  }, [api, entity, user, notify])

  const detail = (e) => {
    if (!e.field) return ''
    const fmt = (v) => (v === null ? '—' : v.length > 60 ? `${v.slice(0, 60)}…` : v)
    if (e.old === null && e.new === null && e.field === 'password') return 'password changed'
    return `${fmt(e.old)} → ${fmt(e.new)}`
  }

  return (
    <div className="panel">
      <div className="row">
        <select value={entity} onChange={(e) => setEntity(e.target.value)}>
          {Object.entries(ENTITY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <input value={user} onChange={(e) => setUser(e.target.value)} placeholder="Filter by person" />
        <span className="muted small">{entries.length} entr{entries.length === 1 ? 'y' : 'ies'}</span>
      </div>
      <table className="rec-table history">
        <thead><tr><th style={{ width: 140 }}>When</th><th style={{ width: 120 }}>Who</th><th>What</th><th style={{ width: 260 }}>Change</th></tr></thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.id} className={e.action === 'reveal' ? 'muted' : undefined}>
              <td>{e.at.replace('T', ' ').slice(0, 16)}</td>
              <td>{e.user}</td>
              <td>{e.summary}</td>
              <td className="small">{detail(e)}</td>
            </tr>
          ))}
          {entries.length === 0 && <tr><td colSpan={4} className="muted">No entries.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}
