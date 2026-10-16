import { useEffect, useMemo, useRef, useState } from 'react'
import { CALCULATED_KINDS, compute, key } from './engine.js'
import {
  columnLetter,
  deleteColumn,
  deleteRow,
  dropdownOptions,
  insertColumn,
  insertRow,
  setCellValue,
} from './ops.js'

const HEADER_HEIGHT = 24 // column letters row
const ROW_HEIGHT = 22 // frozen rows are pinned at fixed heights
const FONT_FALLBACK = "'Overpass', 'Lato', system-ui, -apple-system, 'Segoe UI', sans-serif"

function cellStyle(cell, numeric) {
  const s = cell?.style || {}
  return {
    fontWeight: s.bold ? 700 : undefined,
    fontStyle: s.italic ? 'italic' : undefined,
    fontSize: s.size ? `${s.size}pt` : undefined,
    fontFamily: s.font ? `'${s.font}', ${FONT_FALLBACK}` : undefined,
    color: s.color,
    background: s.fill,
    textAlign: s.align || (numeric ? 'right' : undefined),
    whiteSpace: s.wrap ? 'pre-wrap' : 'nowrap',
  }
}

function ContextMenu({ menu, onAction, onClose }) {
  const ref = useRef(null)
  useEffect(() => {
    const close = (e) => {
      if (!ref.current?.contains(e.target)) onClose()
    }
    const esc = (e) => e.key === 'Escape' && onClose()
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', esc)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', esc)
    }
  }, [onClose])

  const rowItems = [
    ['insertRowAbove', 'Insert row above'],
    ['insertRowBelow', 'Insert row below'],
    ['deleteRow', 'Delete row'],
  ]
  const colItems = [
    ['insertColLeft', 'Insert column left'],
    ['insertColRight', 'Insert column right'],
    ['deleteCol', 'Delete column'],
  ]
  const items = menu.type === 'row' ? rowItems : menu.type === 'col' ? colItems : [...rowItems, null, ...colItems]
  return (
    <ul ref={ref} className="ctx-menu" style={{ left: menu.x, top: menu.y }} role="menu">
      {items.map((item, i) =>
        item ? (
          <li key={item[0]} role="menuitem">
            <button onClick={() => onAction(item[0])}>{item[1]}</button>
          </li>
        ) : (
          <li key={`sep${i}`} className="ctx-sep" />
        ),
      )}
    </ul>
  )
}

export default function Grid({ workbook, sheetIndex, onChange }) {
  const sheet = workbook.sheets[sheetIndex]
  const values = useMemo(() => compute(sheet), [sheet])
  const [sel, setSel] = useState({ row: 0, col: 0 })
  const [editing, setEditing] = useState(null) // { value } while a cell is being edited
  const [menu, setMenu] = useState(null) // { x, y, type: 'row'|'col'|'cell', row, col }
  const wrapRef = useRef(null)
  // Set once an edit is committed or cancelled, so the editor's blur (fired when
  // focus returns to the grid) doesn't commit it a second time or undo a cancel.
  const editDoneRef = useRef(false)

  useEffect(() => {
    setSel({ row: 0, col: 0 })
    setEditing(null)
    setMenu(null)
  }, [sheetIndex])

  // Merged cells: span by "rowIndex|colIndex" of the first cell; covered cells skipped.
  const { spans, coveredBy } = useMemo(() => {
    const colIndex = new Map(sheet.columns.map((c, i) => [c.id, i]))
    const rowIndex = new Map(sheet.rows.map((r, i) => [r.id, i]))
    const spans = new Map()
    const coveredBy = new Map()
    for (const m of sheet.merges || []) {
      const r = rowIndex.get(m.row)
      const c = colIndex.get(m.col)
      if (r === undefined || c === undefined) continue
      const span = Math.min(m.span, sheet.columns.length - c)
      if (span < 2) continue
      spans.set(`${r}|${c}`, span)
      for (let k = 1; k < span; k += 1) coveredBy.set(`${r}|${c + k}`, c)
    }
    return { spans, coveredBy }
  }, [sheet])

  const rows = sheet.rows.length
  const cols = sheet.columns.length
  const cellAt = (r, c) => sheet.rows[r]?.cells[sheet.columns[c]?.id]
  const anchor = (r, c) => coveredBy.get(`${r}|${c}`) ?? c

  useEffect(() => {
    if (sel.row >= rows || sel.col >= cols) setSel({ row: Math.min(sel.row, rows - 1), col: Math.min(sel.col, cols - 1) })
  }, [rows, cols, sel])

  useEffect(() => {
    wrapRef.current
      ?.querySelector(`[data-cell="${sel.row}-${sel.col}"]`)
      ?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [sel])

  function move(dr, dc) {
    setSel(({ row, col }) => {
      const r = Math.max(0, Math.min(rows - 1, row + dr))
      let c = Math.max(0, Math.min(cols - 1, col + dc))
      if (dc > 0) {
        const span = spans.get(`${row}|${col}`)
        if (span && dr === 0) c = Math.min(cols - 1, col + span)
        while (coveredBy.has(`${r}|${c}`) && c < cols - 1) c += 1
      }
      return { row: r, col: anchor(r, c) }
    })
  }

  function select(r, c) {
    setSel({ row: r, col: anchor(r, c) })
    wrapRef.current?.focus({ preventScroll: true })
  }

  const colId = (c) => sheet.columns[c].id
  const apply = (fn) => onChange(fn)

  function commit(value, dr = 0, dc = 0) {
    if (editDoneRef.current) return
    editDoneRef.current = true
    const { row, col } = sel
    apply((wb) => setCellValue(wb, sheetIndex, row, colId(col), value))
    setEditing(null)
    if (dr || dc) move(dr, dc)
    wrapRef.current?.focus({ preventScroll: true })
  }

  function startEdit(initial) {
    const cell = cellAt(sel.row, sel.col)
    if (cell && CALCULATED_KINDS.has(cell.kind)) return
    if (cell?.kind === 'checkbox') {
      apply((wb) => setCellValue(wb, sheetIndex, sel.row, colId(sel.col), !cell.v))
      return
    }
    const current = cell?.v === null || cell?.v === undefined ? '' : String(cell.v)
    editDoneRef.current = false
    setEditing({ value: initial ?? current })
  }

  function onKeyDown(e) {
    if (editing || e.target !== wrapRef.current) return
    const cell = cellAt(sel.row, sel.col)
    const keys = {
      ArrowUp: () => move(-1, 0),
      ArrowDown: () => move(1, 0),
      ArrowLeft: () => move(0, -1),
      ArrowRight: () => move(0, 1),
      Tab: () => move(0, e.shiftKey ? -1 : 1),
      Enter: () => startEdit(),
      F2: () => startEdit(),
    }
    if (keys[e.key]) {
      e.preventDefault()
      keys[e.key]()
    } else if ((e.key === 'Delete' || e.key === 'Backspace') && !(cell && CALCULATED_KINDS.has(cell.kind))) {
      e.preventDefault()
      if (cell?.kind !== 'checkbox') apply((wb) => setCellValue(wb, sheetIndex, sel.row, colId(sel.col), ''))
    } else if (e.key === ' ' && cell?.kind === 'checkbox') {
      e.preventDefault()
      startEdit()
    } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault()
      startEdit(cell?.kind === 'dropdown' ? undefined : e.key)
    }
  }

  function onEditorKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      commit(e.currentTarget.value, 1, 0)
    } else if (e.key === 'Tab') {
      e.preventDefault()
      commit(e.currentTarget.value, 0, e.shiftKey ? -1 : 1)
    } else if (e.key === 'Escape') {
      e.preventDefault()
      editDoneRef.current = true
      setEditing(null)
      wrapRef.current?.focus({ preventScroll: true })
    }
    e.stopPropagation()
  }

  function openMenu(e, type, r, c) {
    e.preventDefault()
    if (r !== undefined && c !== undefined) select(r, c)
    else if (r !== undefined) setSel((s) => ({ row: r, col: s.col }))
    else if (c !== undefined) setSel((s) => ({ row: s.row, col: c }))
    setMenu({ x: e.clientX, y: e.clientY, type, row: r ?? sel.row, col: c ?? sel.col })
  }

  function onMenuAction(action) {
    const { row, col } = menu
    setMenu(null)
    const confirmDelete = (what) =>
      window.confirm(`Delete this ${what}? Its contents will be removed. You can undo with Ctrl+Z.`)
    if (action === 'insertRowAbove') apply((wb) => insertRow(wb, sheetIndex, row, false))
    if (action === 'insertRowBelow') {
      apply((wb) => insertRow(wb, sheetIndex, row, true))
      setSel({ row: row + 1, col })
    }
    if (action === 'deleteRow' && rows > 1 && confirmDelete('row')) apply((wb) => deleteRow(wb, sheetIndex, row))
    if (action === 'insertColLeft') apply((wb) => insertColumn(wb, sheetIndex, col, false))
    if (action === 'insertColRight') {
      apply((wb) => insertColumn(wb, sheetIndex, col, true))
      setSel({ row, col: col + 1 })
    }
    if (action === 'deleteCol' && cols > 1 && confirmDelete('column')) apply((wb) => deleteColumn(wb, sheetIndex, col))
    wrapRef.current?.focus({ preventScroll: true })
  }

  function renderCell(row, r, c) {
    const id = colId(c)
    const cell = row.cells[id]
    const span = spans.get(`${r}|${c}`)
    const selected = sel.row === r && sel.col === c
    const calculated = cell && CALCULATED_KINDS.has(cell.kind)
    const value = calculated ? values.get(key(row.id, id)) : cell?.v
    const numeric = typeof value === 'number'
    let content
    if (selected && editing) {
      if (cell?.kind === 'dropdown') {
        const options = dropdownOptions(workbook, cell.source)
        const list = cell.v && !options.includes(String(cell.v)) ? [String(cell.v), ...options] : options
        content = (
          <select
            className="cell-editor"
            autoFocus
            defaultValue={editing.value}
            onChange={(e) => commit(e.target.value)}
            onKeyDown={onEditorKeyDown}
            onBlur={() => setEditing(null)}
          >
            <option value="">—</option>
            {list.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        )
      } else {
        content = (
          <input
            className="cell-editor"
            autoFocus
            defaultValue={editing.value}
            onKeyDown={onEditorKeyDown}
            onBlur={(e) => commit(e.target.value)}
          />
        )
      }
    } else if (cell?.kind === 'checkbox') {
      content = (
        <input
          type="checkbox"
          checked={!!cell.v}
          onChange={() => apply((wb) => setCellValue(wb, sheetIndex, r, id, !cell.v))}
          aria-label={`${columnLetter(c)}${r + 1}`}
        />
      )
    } else if (cell?.kind === 'dropdown') {
      content = (
        <span className="dd">
          <span>{value ?? ''}</span>
          <span className="dd-arrow">▾</span>
        </span>
      )
    } else {
      content = value === null || value === undefined ? '' : String(value)
    }
    const classes = ['cell']
    if (selected) classes.push('selected')
    if (calculated) classes.push('calculated')
    if (cell?.kind === 'checkbox') classes.push('check')
    return (
      <td
        key={id}
        colSpan={span}
        data-cell={`${r}-${c}`}
        className={classes.join(' ')}
        style={cellStyle(cell, numeric)}
        title={calculated ? 'Calculated automatically' : undefined}
        onMouseDown={(e) => {
          if (e.button === 0 && !(selected && editing)) select(r, c)
        }}
        onDoubleClick={() => startEdit()}
        onContextMenu={(e) => openMenu(e, 'cell', r, c)}
      >
        {content}
      </td>
    )
  }

  return (
    <div className="grid-wrap" tabIndex={0} ref={wrapRef} onKeyDown={onKeyDown}>
      <table className="grid" style={{ fontFamily: `'${sheet.font}', ${FONT_FALLBACK}`, fontSize: `${sheet.font_size}pt` }}>
        <colgroup>
          <col style={{ width: 44 }} />
          {sheet.columns.map((c) => (
            <col key={c.id} style={{ width: c.width }} />
          ))}
        </colgroup>
        <thead>
          <tr>
            <th className="corner" />
            {sheet.columns.map((c, i) => (
              <th
                key={c.id}
                className={`col-head${i === sel.col ? ' active' : ''}`}
                onMouseDown={() => setSel((s) => ({ row: s.row, col: i }))}
                onContextMenu={(e) => openMenu(e, 'col', undefined, i)}
                title="Right-click to insert or delete columns"
              >
                {columnLetter(i)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sheet.rows.map((row, r) => {
            const frozen = r < (sheet.frozen_rows || 0)
            return (
              <tr
                key={row.id}
                className={frozen ? 'frozen' : undefined}
                style={frozen ? { '--top': `${HEADER_HEIGHT + r * ROW_HEIGHT}px`, height: ROW_HEIGHT } : undefined}
              >
                <th
                  className={`row-head${r === sel.row ? ' active' : ''}`}
                  onMouseDown={() => setSel((s) => ({ row: r, col: s.col }))}
                  onContextMenu={(e) => openMenu(e, 'row', r)}
                  title="Right-click to insert or delete rows"
                >
                  {r + 1}
                </th>
                {sheet.columns.map((_, c) => (coveredBy.has(`${r}|${c}`) ? null : renderCell(row, r, c)))}
              </tr>
            )
          })}
        </tbody>
      </table>
      {menu && <ContextMenu menu={menu} onAction={onMenuAction} onClose={() => setMenu(null)} />}
    </div>
  )
}
