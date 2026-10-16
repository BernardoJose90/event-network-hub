// Pure edits on the live sheet. Each returns a new workbook object, so the
// previous one can be kept for undo.
import { CALCULATED_KINDS, TOTAL_KINDS } from './engine.js'

export const newId = (prefix) => `${prefix}${Math.random().toString(36).slice(2, 10)}`

// Plain numbers typed into a cell are stored as numbers ("007" stays text).
const NUMERIC_INPUT = /^-?(0|[1-9]\d*)(\.\d+)?$/

function mapSheet(workbook, sheetIndex, fn) {
  return { ...workbook, sheets: workbook.sheets.map((s, i) => (i === sheetIndex ? fn(s) : s)) }
}

function mapRow(sheet, rowIndex, fn) {
  return { ...sheet, rows: sheet.rows.map((r, i) => (i === rowIndex ? fn(r) : r)) }
}

export function setCellValue(workbook, sheetIndex, rowIndex, colId, input) {
  return mapSheet(workbook, sheetIndex, (sheet) =>
    mapRow(sheet, rowIndex, (row) => {
      const cell = row.cells[colId] || { kind: 'text' }
      if (CALCULATED_KINDS.has(cell.kind)) return row
      const cells = { ...row.cells }
      if (cell.kind === 'checkbox') {
        cells[colId] = { ...cell, v: !!input }
      } else if (cell.kind === 'dropdown') {
        cells[colId] = { ...cell, v: input === '' ? null : input }
      } else {
        const text = typeof input === 'string' ? input : String(input ?? '')
        if (text.trim() === '') {
          // Keep the look (fill, bold…) of a cleared cell for whatever is typed next.
          if (cell.style) cells[colId] = { kind: 'text', style: cell.style }
          else delete cells[colId]
        } else if (NUMERIC_INPUT.test(text.trim())) {
          cells[colId] = { ...cell, kind: 'number', v: Number(text.trim()) }
        } else {
          cells[colId] = { ...cell, kind: 'text', v: text }
        }
      }
      return { ...row, cells }
    }),
  )
}

const hasTotals = (row) => Object.values(row.cells).some((c) => TOTAL_KINDS.has(c.kind))

// A new row copies the cell kinds and styles of a nearby ordinary row, so a new
// row under a section is already a dropdown/tick-box row with the same look.
function templateRow(sheet, index, below) {
  const order = below ? [index, index + 1, index - 1] : [index - 1, index, index - 2]
  for (const i of order) {
    const row = sheet.rows[i]
    if (row && !hasTotals(row)) return row
  }
  return null
}

function blankFrom(template) {
  const cells = {}
  for (const [colId, cell] of Object.entries(template?.cells || {})) {
    if (cell.kind === 'checkbox') cells[colId] = { kind: 'checkbox', v: false, ...(cell.style && { style: cell.style }) }
    else if (cell.kind === 'dropdown') cells[colId] = { kind: 'dropdown', v: null, source: cell.source, ...(cell.style && { style: cell.style }) }
    else if (cell.kind === 'row_formula') cells[colId] = { kind: 'row_formula', expr: cell.expr, ...(cell.style && { style: cell.style }) }
    else if (cell.style) cells[colId] = { kind: 'text', v: null, style: cell.style }
  }
  return cells
}

export function insertRow(workbook, sheetIndex, rowIndex, below) {
  return mapSheet(workbook, sheetIndex, (sheet) => {
    const at = below ? rowIndex + 1 : rowIndex
    const row = { id: newId('r'), cells: blankFrom(templateRow(sheet, rowIndex, below)) }
    return { ...sheet, rows: [...sheet.rows.slice(0, at), row, ...sheet.rows.slice(at)] }
  })
}

export function deleteRow(workbook, sheetIndex, rowIndex) {
  return mapSheet(workbook, sheetIndex, (sheet) => {
    const gone = sheet.rows[rowIndex]?.id
    return {
      ...sheet,
      rows: sheet.rows.filter((_, i) => i !== rowIndex),
      merges: (sheet.merges || []).filter((m) => m.row !== gone),
      frozen_rows: rowIndex < (sheet.frozen_rows || 0) ? sheet.frozen_rows - 1 : sheet.frozen_rows,
    }
  })
}

export function insertColumn(workbook, sheetIndex, colIndex, right) {
  return mapSheet(workbook, sheetIndex, (sheet) => {
    const at = right ? colIndex + 1 : colIndex
    const column = { id: newId('c'), width: 100 }
    const ids = sheet.columns.map((c) => c.id)
    // A column inserted inside a merged range widens it.
    const merges = (sheet.merges || []).map((m) => {
      const start = ids.indexOf(m.col)
      return at > start && at < start + m.span ? { ...m, span: m.span + 1 } : m
    })
    return { ...sheet, columns: [...sheet.columns.slice(0, at), column, ...sheet.columns.slice(at)], merges }
  })
}

export function deleteColumn(workbook, sheetIndex, colIndex) {
  const sheet = workbook.sheets[sheetIndex]
  const gone = sheet.columns[colIndex]?.id
  if (!gone || sheet.columns.length <= 1) return workbook
  const ids = sheet.columns.map((c) => c.id)
  const merges = []
  for (const m of sheet.merges || []) {
    const start = ids.indexOf(m.col)
    if (colIndex < start || colIndex >= start + m.span) merges.push(m)
    else if (m.span > 2) merges.push({ ...m, col: colIndex === start ? ids[start + 1] : m.col, span: m.span - 1 })
  }
  const updated = {
    ...sheet,
    columns: sheet.columns.filter((_, i) => i !== colIndex),
    rows: sheet.rows.map((r) => {
      if (!(gone in r.cells)) return r
      const cells = { ...r.cells }
      delete cells[gone]
      return { ...r, cells }
    }),
    merges,
  }
  // Dropdowns anywhere in the workbook that listed this column's values become plain text.
  return {
    ...workbook,
    sheets: workbook.sheets.map((s, i) => {
      const base = i === sheetIndex ? updated : s
      let changed = false
      const rows = base.rows.map((r) => {
        let cells = r.cells
        for (const [colId, cell] of Object.entries(r.cells)) {
          if (cell.kind === 'dropdown' && cell.source?.sheet === sheet.id && cell.source?.column === gone) {
            if (cells === r.cells) cells = { ...r.cells }
            const { source, ...rest } = cell // eslint-disable-line no-unused-vars
            cells[colId] = { ...rest, kind: typeof cell.v === 'number' ? 'number' : 'text' }
            changed = true
          }
        }
        return cells === r.cells ? r : { ...r, cells }
      })
      return changed ? { ...base, rows } : base
    }),
  }
}

// Options for a dropdown cell: a fixed list, or the values of a column
// elsewhere in the workbook (bold cells there are headings, not options).
export function dropdownOptions(workbook, source) {
  if (!source) return []
  if (source.values) return source.values
  const sheet = workbook.sheets.find((s) => s.id === source.sheet)
  if (!sheet) return []
  const seen = new Set()
  for (const row of sheet.rows) {
    const cell = row.cells[source.column]
    if (!cell || cell.style?.bold) continue
    const v = cell.v
    if (v === null || v === undefined || v === '' || typeof v === 'boolean') continue
    seen.add(String(v))
  }
  return [...seen]
}

export function columnLetter(index) {
  let n = index + 1
  let s = ''
  while (n > 0) {
    const r = (n - 1) % 26
    s = String.fromCharCode(65 + r) + s
    n = Math.floor((n - 1) / 26)
  }
  return s
}
