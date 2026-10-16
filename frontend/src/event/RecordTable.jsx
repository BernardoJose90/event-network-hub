import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { CHOICE_LABELS, COLUMNS, recordName } from './columns.js'

const GROUPED = { wifi: 'wifi', ethernet: 'ethernet' }

function show(col, value) {
  if (value === null || value === undefined || value === '') return ''
  if (col.kind === 'choice') return CHOICE_LABELS[value] || value
  return String(value)
}

function matches(record, query) {
  if (!query) return true
  const q = query.toLowerCase()
  return Object.values(record.data).some((v) => v !== null && String(v).toLowerCase().includes(q))
}

function CellEditor({ col, value, options, onCommit, onCancel }) {
  const [draft, setDraft] = useState(col.kind === 'secret' ? '' : value ?? '')
  // Commit or cancel only once: Enter followed by the blur that unmounting causes
  // would otherwise save twice (the second time with an outdated revision).
  const done = useRef(false)
  const finish = (v) => {
    if (done.current) return
    done.current = true
    onCommit(v)
  }
  const cancel = () => {
    if (done.current) return
    done.current = true
    onCancel()
  }
  const keys = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      finish(e.currentTarget.value)
    } else if (e.key === 'Escape') {
      e.preventDefault()
      cancel()
    }
  }
  if (col.kind === 'choice' || col.kind === 'list') {
    const list = value && !options.includes(value) ? [value, ...options] : options
    return (
      <select autoFocus className="rec-editor" defaultValue={value ?? ''} onChange={(e) => finish(e.target.value)}
        onBlur={cancel} onKeyDown={keys}>
        <option value="">—</option>
        {list.map((o) => (
          <option key={o} value={o}>
            {col.kind === 'choice' ? CHOICE_LABELS[o] || o : o}
          </option>
        ))}
      </select>
    )
  }
  return (
    <input
      autoFocus
      className="rec-editor"
      type={col.kind === 'number' ? 'number' : 'text'}
      min={col.kind === 'number' ? 0 : undefined}
      value={draft}
      placeholder={col.kind === 'secret' ? 'New password' : undefined}
      onChange={(e) => setDraft(e.target.value)}
      onKeyDown={keys}
      onBlur={(e) => finish(e.target.value)}
    />
  )
}

export default function RecordTable({ type, api, overview, isAdmin, notify, onChanged }) {
  const columns = COLUMNS[type]
  const groupKind = GROUPED[type]
  const venues = overview.venues
  const groups = groupKind ? overview.groups.filter((g) => g.kind === groupKind) : [null]
  const [venueId, setVenueId] = useState(venues[0]?.id ?? null)
  const [records, setRecords] = useState([])
  const [archivedMode, setArchivedMode] = useState(false)
  const [search, setSearch] = useState('')
  const [editing, setEditing] = useState(null) // { id, field }
  const [revealed, setRevealed] = useState({})
  const [loading, setLoading] = useState(true)

  const listOptions = useMemo(() => {
    const out = {}
    for (const item of overview.lists) (out[item.kind] ||= []).push(item.value)
    return out
  }, [overview.lists])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setRecords(await api.records(type, archivedMode))
    } catch (err) {
      notify(err.message, 'error')
    } finally {
      setLoading(false)
    }
  }, [api, type, archivedMode, notify])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    setEditing(null)
    setSearch('')
    setArchivedMode(false)
  }, [type])

  const replace = (updated) => setRecords((rs) => rs.map((r) => (r.id === updated.id ? updated : r)))

  async function act(fn, success) {
    try {
      const result = await fn()
      if (success) notify(success, 'ok')
      onChanged()
      return result
    } catch (err) {
      if (err.status === 409) notify(`${err.message}`, 'error', load)
      else notify(err.message, 'error')
      return null
    }
  }

  async function commit(record, col, raw) {
    setEditing(null)
    let value = typeof raw === 'string' ? raw.trim() : raw
    if (col.kind === 'secret' && value === '') return // blank = keep the current password
    if (col.kind === 'number') value = value === '' ? null : Number(value)
    if (value === '') value = null
    if ((record.data[col.field] ?? null) === value) return
    const updated = await act(() => api.update(type, record.id, record.revision, { [col.field]: value }))
    if (updated) {
      replace(updated)
      if (col.kind === 'secret') setRevealed((r) => ({ ...r, [record.id]: undefined }))
    }
  }

  async function add(groupId) {
    const body = groupKind ? { venue_id: venueId, group_id: groupId, data: {} } : { data: {} }
    const created = await act(() => api.create(type, body))
    if (created) {
      setRecords((rs) => [...rs, created])
      setSearch('')
      setEditing({ id: created.id, field: columns[groupKind === 'wifi' ? 1 : 0].field })
    }
  }

  function siblings(record) {
    return records.filter((r) => r.venue_id === record.venue_id && r.group_id === record.group_id)
  }

  async function move(record, direction) {
    const list = siblings(record)
    const i = list.findIndex((r) => r.id === record.id)
    let beforeId
    if (direction < 0) {
      if (i === 0) return
      beforeId = list[i - 1].id
    } else {
      if (i === list.length - 1) return
      beforeId = list[i + 2]?.id ?? null
    }
    if (await act(() => api.move(type, record.id, record.revision, beforeId))) load()
  }

  async function archive(record) {
    if (await act(() => api.archive(type, record.id, record.revision), `Archived ${recordName(type, record.data)}`)) {
      setRecords((rs) => rs.filter((r) => r.id !== record.id))
    }
  }

  async function restore(record) {
    if (await act(() => api.restore(type, record.id, record.revision), `Restored ${recordName(type, record.data)}`)) {
      setRecords((rs) => rs.filter((r) => r.id !== record.id))
    }
  }

  async function destroy(record) {
    const ok = window.confirm(`Permanently delete ${recordName(type, record.data)}? This can't be undone.`)
    if (!ok) return
    if (await act(() => api.remove(type, record.id, record.revision), 'Deleted permanently')) {
      setRecords((rs) => rs.filter((r) => r.id !== record.id))
    }
  }

  async function reveal(record) {
    if (revealed[record.id] !== undefined) {
      setRevealed((r) => ({ ...r, [record.id]: undefined }))
      return
    }
    try {
      const { password } = await api.reveal(record.id)
      setRevealed((r) => ({ ...r, [record.id]: password ?? '' }))
    } catch (err) {
      notify(err.message, 'error')
    }
  }

  const visible = records.filter((r) => (!groupKind || archivedMode || r.venue_id === venueId) && matches(r, search))
  const canEdit = isAdmin && !archivedMode
  const totalCol = columns.find((c) => c.total)
  const sum = (rows) => rows.reduce((t, r) => t + (Number(r.data[totalCol?.field]) || 0), 0)

  function cell(record, col) {
    const isEditing = editing?.id === record.id && editing.field === col.field
    if (isEditing) {
      const options = col.kind === 'choice' ? col.options : (col.lists || []).flatMap((k) => listOptions[k] || [])
      return (
        <td key={col.field} className="rec-cell editing">
          <CellEditor col={col} value={record.data[col.field]} options={[...new Set(options)]}
            onCommit={(v) => commit(record, col, v)} onCancel={() => setEditing(null)} />
        </td>
      )
    }
    let content = show(col, record.data[col.field])
    if (col.kind === 'secret') {
      const shown = revealed[record.id]
      content = record.has_password ? (
        <span className="secret">
          <span>{shown !== undefined ? shown : '••••••••'}</span>
          <button className="link" onClick={(e) => { e.stopPropagation(); reveal(record) }}>
            {shown !== undefined ? 'Hide' : 'Reveal'}
          </button>
        </span>
      ) : ''
    }
    return (
      <td key={col.field} className={`rec-cell${col.kind === 'number' ? ' num' : ''}${canEdit ? ' editable' : ''}`}
        onClick={() => canEdit && setEditing({ id: record.id, field: col.field })}
        title={canEdit ? 'Click to edit' : undefined}>
        {content}
      </td>
    )
  }

  function row(record) {
    const list = siblings(record)
    const index = list.findIndex((r) => r.id === record.id)
    return (
      <tr key={record.id}>
        {columns.map((col) => cell(record, col))}
        {isAdmin && (
          <td className="rec-actions">
            {archivedMode ? (
              <>
                <button className="secondary" onClick={() => restore(record)}>Restore</button>
                <button className="danger" onClick={() => destroy(record)}>Delete</button>
              </>
            ) : (
              <>
                <button className="icon" title="Move up" disabled={index <= 0 || !!search} onClick={() => move(record, -1)}>↑</button>
                <button className="icon" title="Move down" disabled={index === list.length - 1 || !!search}
                  onClick={() => move(record, 1)}>↓</button>
                <button className="archive" onClick={() => archive(record)}>Archive</button>
              </>
            )}
          </td>
        )}
      </tr>
    )
  }

  const span = columns.length + (isAdmin ? 1 : 0)
  const totalIndex = totalCol ? columns.indexOf(totalCol) : -1

  return (
    <div className="rec-panel">
      <div className="rec-toolbar">
        {groupKind && !archivedMode && (
          <div className="venue-tabs" role="tablist">
            {venues.map((v) => (
              <button key={v.id} role="tab" className={v.id === venueId ? 'active' : ''} onClick={() => setVenueId(v.id)}>
                {v.name}
              </button>
            ))}
          </div>
        )}
        <input className="rec-search" type="search" placeholder="Search…" value={search}
          onChange={(e) => setSearch(e.target.value)} />
        {totalCol && !archivedMode && (
          <span className="rec-total">Total {totalCol.label.toLowerCase()}: <strong>{sum(visible)}</strong></span>
        )}
        <label className="rec-archived-toggle">
          <input type="checkbox" checked={archivedMode} onChange={(e) => setArchivedMode(e.target.checked)} /> Show archived
        </label>
      </div>
      <div className="rec-scroll">
        <table className="rec-table">
          <colgroup>
            {columns.map((c) => <col key={c.field} style={{ width: c.width }} />)}
            {isAdmin && <col style={{ width: archivedMode ? 150 : 140 }} />}
          </colgroup>
          <thead>
            <tr>
              {columns.map((c) => <th key={c.field} className={c.kind === 'number' ? 'num' : undefined}>{c.label}</th>)}
              {isAdmin && <th />}
            </tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={span} className="muted">Loading…</td></tr>}
            {!loading && archivedMode && visible.length === 0 && (
              <tr><td colSpan={span} className="muted">Nothing archived.</td></tr>
            )}
            {!loading && archivedMode && visible.map(row)}
            {!loading && !archivedMode && groups.map((group) => {
              const rows = visible.filter((r) => (group ? r.group_id === group.id : true))
              return [
                group && (
                  <tr key={`g${group.id}`} className={`rec-group ${groupKind}`}>
                    {totalIndex >= 0 ? (
                      <>
                        <td colSpan={totalIndex}>{group.name}</td>
                        <td className="num">{sum(rows)}</td>
                        <td colSpan={span - totalIndex - 1}>
                          {canEdit && <button className="add" onClick={() => add(group.id)}>+ Add</button>}
                        </td>
                      </>
                    ) : (
                      <td colSpan={span}>
                        {group.name}
                        {canEdit && <button className="add" onClick={() => add(group.id)}>+ Add</button>}
                      </td>
                    )}
                  </tr>
                ),
                ...rows.map(row),
              ]
            })}
            {!loading && !archivedMode && !groupKind && visible.length === 0 && (
              <tr><td colSpan={span} className="muted">No {type === 'partner' ? 'partner orders' : 'items'} yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {canEdit && !groupKind && (
        <button className="secondary rec-add-bottom" onClick={() => add(null)}>+ Add partner order</button>
      )}
    </div>
  )
}
