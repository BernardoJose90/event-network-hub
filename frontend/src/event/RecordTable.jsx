import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useDialog } from '../ui/Dialog.jsx'
import { CHOICE_LABELS, COLUMNS, TITLES, recordName } from './columns.js'
import { useGridKeyboard } from './grid.js'
import { usePendingDeletes } from './pendingDeletes.js'

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

// The in-cell editor. Enter saves, Escape cancels, Tab saves and moves on; leaving the
// cell (blur) saves. `seed` is the character typed to start editing (spreadsheet style).
function CellEditor({ col, value, options, seed, label, onCommit, onCancel }) {
  const initial = col.kind === 'secret' ? '' : value ?? ''
  const [draft, setDraft] = useState(seed !== undefined ? seed : initial)
  // Commit or cancel only once: Enter followed by the blur that unmounting causes
  // would otherwise save twice (the second time with an outdated revision).
  const done = useRef(false)
  const pointer = useRef(false)
  const finish = (v, how) => {
    if (done.current) return
    done.current = true
    onCommit(v, how)
  }
  const cancel = () => {
    if (done.current) return
    done.current = true
    onCancel()
  }
  const keys = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      e.stopPropagation()
      finish(e.currentTarget.value, 'enter')
    } else if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      cancel()
    } else if (e.key === 'Tab') {
      e.preventDefault()
      e.stopPropagation()
      finish(e.currentTarget.value, e.shiftKey ? 'shift-tab' : 'tab')
    }
  }
  if (col.kind === 'choice' || col.kind === 'list') {
    const list = value && !options.includes(value) ? [value, ...options] : options
    return (
      <select
        autoFocus
        className="rec-editor"
        aria-label={label}
        value={draft}
        onPointerDown={() => { pointer.current = true }}
        // A pointer choice saves at once; with the keyboard, arrows change the choice
        // and Enter saves (so arrowing through options doesn't save each one).
        onChange={(e) => { setDraft(e.target.value); if (pointer.current) finish(e.target.value, 'pointer') }}
        onBlur={(e) => (e.target.value !== (value ?? '') ? finish(e.target.value, 'blur') : cancel())}
        onKeyDown={keys}
      >
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
      aria-label={label}
      type={col.kind === 'number' ? 'number' : 'text'}
      inputMode={col.kind === 'number' ? 'decimal' : undefined}
      min={col.kind === 'number' ? 0 : undefined}
      value={draft}
      placeholder={col.kind === 'secret' ? 'New password' : undefined}
      onChange={(e) => setDraft(e.target.value)}
      onKeyDown={keys}
      onBlur={(e) => finish(e.target.value, 'blur')}
    />
  )
}

export default function RecordTable({ type, api, overview, isAdmin, notify, onChanged }) {
  const columns = COLUMNS[type]
  const groupKind = GROUPED[type]
  const venues = overview.venues
  const groups = groupKind ? overview.groups.filter((g) => g.kind === groupKind) : [null]
  const dialog = useDialog()
  const [venueId, setVenueId] = useState(venues[0]?.id ?? null)
  const [records, setRecords] = useState([])
  const [search, setSearch] = useState('')
  const [editing, setEditing] = useState(null) // { id, field, seed? }
  const [revealed, setRevealed] = useState({})
  const [loading, setLoading] = useState(true)
  const tableRef = useRef(null)
  const tabRefs = useRef({})
  const ids = { hint: useId(), panel: useId(), search: useId() }

  const listOptions = useMemo(() => {
    const out = {}
    for (const item of overview.lists) (out[item.kind] ||= []).push(item.value)
    return out
  }, [overview.lists])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setRecords(await api.records(type))
    } catch (err) {
      notify(err.message, 'error')
    } finally {
      setLoading(false)
    }
  }, [api, type, notify])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    setEditing(null)
    setSearch('')
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

  const grid = useGridKeyboard(tableRef)

  async function commit(record, col, raw, how) {
    setEditing(null)
    grid.afterEdit(`c:${record.id}:${col.field}`, how)
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

  function cancelEdit(record, col) {
    setEditing(null)
    grid.afterEdit(`c:${record.id}:${col.field}`, 'escape')
  }

  async function add(groupId) {
    const body = groupKind ? { venue_id: venueId, group_id: groupId, data: {} } : { data: {} }
    const created = await act(() => api.create(type, body))
    if (created) {
      setRecords((rs) => [...rs, created])
      setSearch('')
      setEditing({ id: created.id, field: columns[1].field }) // the first column is the automatic ID
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
    if (await act(() => api.move(type, record.id, record.revision, beforeId))) {
      grid.focusKeyLater(`a:${record.id}:${direction < 0 ? 'up' : 'down'}`)
      load()
    }
  }

  const pending = usePendingDeletes(api, type, notify, onChanged, load)

  async function remove(record) {
    const name = recordName(type, record.data)
    const ok = await dialog.confirm({
      title: `Delete ${name}?`,
      body: 'You can undo this for a few seconds afterwards. The history keeps its values.',
      confirmLabel: 'Delete',
      danger: true,
    })
    if (!ok) return
    setEditing(null)
    grid.focusNeighbourOf(`a:${record.id}:delete`)
    setRecords((rs) => rs.filter((r) => r.id !== record.id))
    pending.schedule(record, `Deleted ${name}.`)
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

  const visible = records.filter((r) => !pending.isPending(r.id) && (!groupKind || r.venue_id === venueId)
    && matches(r, search))
  const canEdit = isAdmin
  const isTemplate = overview.document.kind === 'template'
  const totalCol = columns.find((c) => c.total)
  const sum = (rows) => rows.reduce((t, r) => t + (Number(r.data[totalCol?.field]) || 0), 0)
  const byId = useMemo(() => new Map(records.map((r) => [r.id, r])), [records])

  // The ID column is the row number: 1, 2, 3… per venue in display order (whole list for
  // partner orders). Rows waiting to be deleted don't count, so removing a row closes the gap.
  const rowNumbers = new Map()
  {
    const live = records.filter((r) => !pending.isPending(r.id))
    const venueIds = groupKind ? venues.map((v) => v.id) : [null]
    for (const vid of venueIds) {
      let count = 0
      for (const group of groups) {
        for (const r of live) {
          if ((groupKind ? r.venue_id === vid : true) && (group ? r.group_id === group.id : true)) {
            count += 1
            rowNumbers.set(r.id, count)
          }
        }
      }
    }
  }

  // Keyboard actions on a focused data cell (APG data grid: Enter/F2 edit, typing edits).
  grid.onCellKey.current = (key, e) => {
    const [, id, field] = key.split(':')
    const record = byId.get(Number(id))
    const col = columns.find((c) => c.field === field)
    if (!record || !col) return false
    const editable = canEdit && col.kind !== 'id' && !(isTemplate && col.templateLocked)
    if (col.kind === 'secret' && (e.key === ' ' || (!editable && e.key === 'Enter'))) {
      if (record.has_password) reveal(record)
      return true
    }
    if (!editable) return false
    if (e.key === 'Enter' || e.key === 'F2') {
      setEditing({ id: record.id, field })
      return true
    }
    const printable = e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey && e.key !== ' '
    if (printable && (col.kind === 'text' || col.kind === 'number')) {
      setEditing({ id: record.id, field, seed: e.key })
      return true
    }
    return false
  }

  useLayoutEffect(() => grid.sync())

  function cell(record, col) {
    const key = `c:${record.id}:${col.field}`
    const name = recordName(type, record.data)
    const isEditing = editing?.id === record.id && editing.field === col.field
    if (isEditing) {
      const options = col.kind === 'choice' ? col.options : (col.lists || []).flatMap((k) => listOptions[k] || [])
      return (
        <td key={col.field} className="rec-cell editing">
          <CellEditor col={col} value={record.data[col.field]} options={[...new Set(options)]} seed={editing.seed}
            label={`${col.label}, ${name}`}
            onCommit={(v, how) => commit(record, col, v, how)} onCancel={() => cancelEdit(record, col)} />
        </td>
      )
    }
    let content = col.kind === 'id' ? rowNumbers.get(record.id) ?? '' : show(col, record.data[col.field])
    const locked = isTemplate && col.templateLocked
    const editable = canEdit && !locked && col.kind !== 'id'
    if (col.kind === 'secret') {
      const shown = revealed[record.id]
      content = record.has_password ? (
        <span className="secret">
          <span>{shown !== undefined ? shown : <span aria-label="Hidden password">••••••••</span>}</span>
          <button className="link" tabIndex={-1} onClick={(e) => { e.stopPropagation(); reveal(record) }}
            aria-label={`${shown !== undefined ? 'Hide' : 'Reveal'} password of ${name}`}>
            {shown !== undefined ? 'Hide' : 'Reveal'}
          </button>
        </span>
      ) : ''
    }
    return (
      <td key={col.field} data-nav={key} tabIndex={-1}
        className={`rec-cell${col.kind === 'number' ? ' num' : ''}${col.kind === 'id' ? ' id-cell' : ''}${editable ? ' editable' : ''}`}
        aria-readonly={!editable || undefined}
        onClick={() => { grid.setActive(key); if (editable) setEditing({ id: record.id, field: col.field }) }}
        title={editable ? 'Click or press Enter to edit' : col.kind === 'id' ? 'The row number: it updates when rows are added, moved or removed' : locked && canEdit ? 'Always empty or 0 in templates; set it on the event' : undefined}>
        {content}
      </td>
    )
  }

  function row(record) {
    const list = siblings(record)
    const index = list.findIndex((r) => r.id === record.id)
    const name = recordName(type, record.data)
    return (
      <tr key={record.id}>
        {columns.map((col) => cell(record, col))}
        {isAdmin && (
          <td className="rec-actions">
            <button className="icon" data-nav={`a:${record.id}:up`} tabIndex={-1} aria-label={`Move ${name} up`}
              title="Move up" disabled={index <= 0 || !!search} onClick={() => move(record, -1)}>↑</button>
            <button className="icon" data-nav={`a:${record.id}:down`} tabIndex={-1} aria-label={`Move ${name} down`}
              title="Move down" disabled={index === list.length - 1 || !!search} onClick={() => move(record, 1)}>↓</button>
            <button className="danger" data-nav={`a:${record.id}:delete`} tabIndex={-1} aria-label={`Delete ${name}`}
              onClick={() => remove(record)}>Delete</button>
          </td>
        )}
      </tr>
    )
  }

  const span = columns.length + (isAdmin ? 1 : 0)
  const totalIndex = totalCol ? columns.indexOf(totalCol) : -1
  const venueName = venues.find((v) => v.id === venueId)?.name

  // Venue tabs: APG tabs pattern with automatic activation (the table is already loaded).
  function onTabKey(e, index) {
    const last = venues.length - 1
    const next = { ArrowRight: index === last ? 0 : index + 1, ArrowLeft: index === 0 ? last : index - 1, Home: 0, End: last }[e.key]
    if (next === undefined) return
    e.preventDefault()
    setVenueId(venues[next].id)
    tabRefs.current[venues[next].id]?.focus()
  }

  const table = (
    // An empty table has nothing focusable, so the scroll area itself takes focus (WCAG 2.1.1).
    <div className="rec-scroll" tabIndex={!loading && visible.length === 0 ? 0 : undefined}
      role={!loading && visible.length === 0 ? 'region' : undefined}
      aria-label={!loading && visible.length === 0 ? `${TITLES[type]} table` : undefined}>
      <table
        ref={tableRef}
        className="rec-table"
        role="grid"
        aria-label={`${TITLES[type]}${venueName ? `, ${venueName}` : ''}`}
        aria-describedby={ids.hint}
        aria-readonly={!canEdit || undefined}
        aria-busy={loading || undefined}
        onKeyDown={grid.onKeyDown}
        onFocus={grid.onFocus}
      >
        <colgroup>
          {columns.map((c) => <col key={c.field} style={{ width: c.width }} />)}
          {isAdmin && <col style={{ width: 150 }} />}
        </colgroup>
        <thead>
          <tr>
            {columns.map((c) => <th key={c.field} scope="col" className={c.kind === 'number' ? 'num' : undefined}>{c.label}</th>)}
            {isAdmin && <th scope="col"><span className="visually-hidden">Actions</span></th>}
          </tr>
        </thead>
        <tbody>
          {loading && [0, 1, 2, 3, 4].map((i) => (
            <tr key={`s${i}`} className="skeleton-row" aria-hidden="true">
              <td colSpan={span}><span className="skeleton" /></td>
            </tr>
          ))}
          {!loading && groups.map((group) => {
            const rows = visible.filter((r) => (group ? r.group_id === group.id : true))
            const addButton = canEdit && group && (
              <button className="add" data-nav={`g:${group.id}:add`} tabIndex={-1} onClick={() => add(group.id)}
                aria-label={`Add to ${group.name}`}>+ Add</button>
            )
            return [
              group && (
                <tr key={`g${group.id}`} className={`rec-group ${groupKind}`}>
                  {totalIndex >= 0 ? (
                    <>
                      <th colSpan={totalIndex} scope="rowgroup">{group.name}</th>
                      <td className="num">
                        {sum(rows)}<span className="visually-hidden"> {totalCol.label.toLowerCase()}</span>
                      </td>
                      <td colSpan={span - totalIndex - 1}>{addButton}</td>
                    </>
                  ) : (
                    <th colSpan={span} scope="rowgroup">
                      {group.name}
                      {addButton}
                    </th>
                  )}
                </tr>
              ),
              ...rows.map(row),
            ]
          })}
          {!loading && !groupKind && visible.length === 0 && (
            <tr><td colSpan={span} className="muted empty-cell">
              {search ? 'Nothing matches your search.' : `No ${type === 'partner' ? 'partner orders' : 'items'} yet.`}
            </td></tr>
          )}
        </tbody>
      </table>
    </div>
  )

  return (
    <div className="rec-panel">
      <div className="rec-toolbar">
        {groupKind && venues.length > 0 && (
          <div className="venue-tabs" role="tablist" aria-label="Venues">
            {venues.map((v, i) => (
              <button key={v.id} role="tab" id={`venue-tab-${v.id}`} ref={(el) => { tabRefs.current[v.id] = el }}
                aria-selected={v.id === venueId} aria-controls={ids.panel} tabIndex={v.id === venueId ? 0 : -1}
                className={v.id === venueId ? 'active' : ''} onClick={() => setVenueId(v.id)} onKeyDown={(e) => onTabKey(e, i)}>
                {v.name}
              </button>
            ))}
          </div>
        )}
        <label htmlFor={ids.search} className="visually-hidden">Search {TITLES[type].toLowerCase()}</label>
        <input id={ids.search} className="rec-search" type="search" placeholder="Search…" value={search}
          onChange={(e) => setSearch(e.target.value)} />
        {totalCol && (
          <span className="rec-total" aria-live="polite">
            Total {totalCol.label.toLowerCase()}: <strong>{sum(visible)}</strong>
          </span>
        )}
      </div>
      <p id={ids.hint} className="grid-hint">
        {canEdit
          ? 'Arrow keys move between cells. Enter, F2 or typing edits a cell; Enter saves, Escape cancels.'
          : 'Arrow keys move between cells.'}
        {type === 'wifi' && ' Space reveals a password.'}
      </p>
      {groupKind && venues.length > 0 ? (
        <div role="tabpanel" id={ids.panel} aria-labelledby={`venue-tab-${venueId}`}>{table}</div>
      ) : table}
      {canEdit && !groupKind && (
        <button className="secondary rec-add-bottom" onClick={() => add(null)}>+ Add partner order</button>
      )}
    </div>
  )
}
