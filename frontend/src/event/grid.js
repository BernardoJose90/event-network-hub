import { useCallback, useRef } from 'react'

// Keyboard navigation for a table with role="grid" (WAI-ARIA APG data grid):
// - one Tab stop: the active cell has tabindex=0, every other [data-nav] item -1;
// - arrows move one cell (no wrapping), Home/End to the row's ends, Ctrl+Home/End to
//   the grid's first/last cell, PageUp/PageDown by PAGE rows;
// - other keys on a cell go to onCellKey (Enter/F2/typing to edit, handled by the table);
// - after an edit, focus returns to the cell (Enter/Escape) or moves on (Tab).
// Items are found in the DOM: cells and buttons marked with data-nav="<unique key>".

const PAGE = 10

export function useGridKeyboard(tableRef) {
  const active = useRef(null) // key of the item in the tab sequence
  const pendingFocus = useRef(null) // { key } or { from, step } to focus after the next render
  const desiredCol = useRef(0) // column kept while moving up/down through shorter rows
  const onCellKey = useRef(null)

  const rows = useCallback(() => {
    const table = tableRef.current
    if (!table) return []
    return [...table.querySelectorAll('tr')]
      .map((tr) => [...tr.querySelectorAll('[data-nav]')].filter((el) => !el.disabled))
      .filter((r) => r.length)
  }, [tableRef])

  const find = (key) => tableRef.current?.querySelector(`[data-nav="${CSS.escape(key)}"]`)

  const focusEl = useCallback((el) => {
    if (!el) return
    active.current = el.dataset.nav
    for (const other of tableRef.current.querySelectorAll('[data-nav]')) other.tabIndex = other === el ? 0 : -1
    el.focus()
    el.scrollIntoView?.({ block: 'nearest', inline: 'nearest' })
  }, [tableRef])

  const position = useCallback((key) => {
    const r = rows()
    for (let i = 0; i < r.length; i++) {
      const j = r[i].findIndex((el) => el.dataset.nav === key)
      if (j >= 0) return { r, i, j }
    }
    return { r, i: -1, j: -1 }
  }, [rows])

  // Run after every render: keep exactly one tab stop and apply any pending focus.
  const sync = useCallback(() => {
    const table = tableRef.current
    if (!table) return
    const want = pendingFocus.current
    if (want) {
      let el = null
      if (want.key) {
        el = find(want.key)
        if (!el || el.disabled) {
          const id = want.key.split(':')[1] // fall back to the first cell of the same row
          el = table.querySelector(`[data-nav^="c:${CSS.escape(id)}:"]`)
        }
      } else if (want.from) {
        const { r, i, j } = position(want.from)
        if (i >= 0) el = r[i][Math.min(Math.max(j + want.step, 0), r[i].length - 1)]
      }
      if (el) {
        pendingFocus.current = null
        focusEl(el)
        return
      }
    }
    const items = [...table.querySelectorAll('[data-nav]')]
    if (!items.length) return
    // The tab stop is the last active item, else the first data cell (not a section's "+ Add").
    const current = items.find((el) => el.dataset.nav === active.current && !el.disabled)
      || items.find((el) => el.tagName === 'TD') || items.find((el) => !el.disabled)
    for (const el of items) el.tabIndex = el === current ? 0 : -1
  }, [tableRef, position, focusEl])

  const onFocus = useCallback((e) => {
    const key = e.target?.dataset?.nav
    if (!key) return
    active.current = key
    const { j } = position(key)
    if (j >= 0) desiredCol.current = j
    for (const el of tableRef.current.querySelectorAll('[data-nav]')) el.tabIndex = el === e.target ? 0 : -1
  }, [tableRef, position])

  const onKeyDown = useCallback((e) => {
    const el = e.target.closest?.('[data-nav]')
    if (!el || el !== e.target) return // keys inside an editor or a nested button
    const { r, i, j } = position(el.dataset.nav)
    if (i < 0) return
    let target = null
    const ctrl = e.ctrlKey || e.metaKey
    const rowAt = (n) => r[Math.min(Math.max(n, 0), r.length - 1)]
    const colIn = (row) => row[Math.min(desiredCol.current, row.length - 1)]
    switch (e.key) {
      case 'ArrowRight': target = r[i][j + 1]; if (target) desiredCol.current = j + 1; break
      case 'ArrowLeft': target = r[i][j - 1]; if (target) desiredCol.current = j - 1; break
      case 'ArrowDown': target = i + 1 < r.length ? colIn(r[i + 1]) : null; break
      case 'ArrowUp': target = i > 0 ? colIn(r[i - 1]) : null; break
      case 'PageDown': target = colIn(rowAt(i + PAGE)); break
      case 'PageUp': target = colIn(rowAt(i - PAGE)); break
      case 'Home': target = ctrl ? r[0][0] : r[i][0]; desiredCol.current = 0; break
      case 'End': {
        const row = ctrl ? r[r.length - 1] : r[i]
        target = row[row.length - 1]
        desiredCol.current = row.length - 1
        break
      }
      default:
        if (el.tagName === 'TD' && onCellKey.current?.(el.dataset.nav, e)) e.preventDefault()
        return
    }
    e.preventDefault() // arrows/Home/End/Page keys never scroll the page from inside the grid
    if (target) focusEl(target)
  }, [position, focusEl])

  return {
    onKeyDown,
    onFocus,
    sync,
    onCellKey,
    setActive: (key) => { active.current = key },
    // how: 'enter' | 'escape' | 'pointer' keep focus on the cell; 'tab'/'shift-tab' move on; 'blur' leaves it.
    afterEdit: (key, how) => {
      active.current = key
      if (how === 'tab' || how === 'shift-tab') pendingFocus.current = { from: key, step: how === 'tab' ? 1 : -1 }
      else if (how !== 'blur') pendingFocus.current = { key }
    },
    focusKeyLater: (key) => { pendingFocus.current = { key } },
    // Before removing an item's row: focus the same column in the next row (or the previous one).
    focusNeighbourOf: (key) => {
      const { r, i, j } = position(key)
      if (i < 0) return
      const row = r[i + 1] || r[i - 1]
      const el = row?.[Math.min(j, row.length - 1)]
      if (el) pendingFocus.current = { key: el.dataset.nav }
    },
  }
}
