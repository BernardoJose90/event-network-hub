// Calculated cells of a live sheet. Mirrors backend/app/sheets/engine.py,
// which is what the downloaded Excel/PDF use; keep the two in step.
//
// - section_total: sum of the numbers in its column from the next row until
//   the next section/grand total in that column (or the end of the sheet).
// - grand_total: sum of every section total in its column.
// - row_formula: + - * / and parentheses over same-row cells, written with
//   column ids, e.g. "{c8}-{c7}". Parsed here, never eval'd.

export const ERROR = '#VALUE!'
export const REF_ERROR = '#REF!'
export const TOTAL_KINDS = new Set(['section_total', 'grand_total'])
export const CALCULATED_KINDS = new Set(['section_total', 'grand_total', 'row_formula'])

const NUMBER = /^\s*-?\d+(\.\d+)?\s*$/
const TOKEN = /\s*(?:(\d+(?:\.\d+)?)|\{([A-Za-z0-9_-]+)\}|([-+*/()]))/y

export function asNumber(value) {
  if (value === null || value === undefined || typeof value === 'boolean') return null
  if (typeof value === 'number') return value
  if (typeof value === 'string' && NUMBER.test(value)) return parseFloat(value)
  return null
}

const tidy = (x) => (Number.isInteger(x) ? x : Math.round(x * 1e10) / 1e10)

class ExprError extends Error {
  constructor(code) {
    super(code)
    this.code = code
  }
}

export function evaluateExpr(expr, lookup) {
  const tokens = []
  let pos = 0
  while (pos < expr.length) {
    if (expr.slice(pos).trim() === '') break
    TOKEN.lastIndex = pos
    const m = TOKEN.exec(expr)
    if (!m) return ERROR
    const [, num, ref, op] = m
    tokens.push(num !== undefined ? ['num', num] : ref !== undefined ? ['ref', ref] : ['op', op])
    pos = TOKEN.lastIndex
  }
  let i = 0
  const peek = () => tokens[i]
  const take = () => {
    if (i >= tokens.length) throw new ExprError(ERROR)
    return tokens[i++]
  }
  const isOp = (t, ...ops) => t && t[0] === 'op' && ops.includes(t[1])

  function atom() {
    const [kind, text] = take()
    if (kind === 'num') return parseFloat(text)
    if (kind === 'ref') {
      const value = lookup(text) // throws ExprError(REF_ERROR) for unknown columns
      if (value === null || value === undefined || value === '') return 0
      const n = asNumber(value)
      if (n === null) throw new ExprError(ERROR)
      return n
    }
    if (text === '(') {
      const v = expression()
      if (!isOp(take(), ')')) throw new ExprError(ERROR)
      return v
    }
    if (text === '-') return -atom()
    if (text === '+') return atom()
    throw new ExprError(ERROR)
  }
  function term() {
    let v = atom()
    while (isOp(peek(), '*', '/')) {
      const op = take()[1]
      const rhs = atom()
      if (op === '/' && rhs === 0) throw new ExprError('#DIV/0!')
      v = op === '*' ? v * rhs : v / rhs
    }
    return v
  }
  function expression() {
    let v = term()
    while (isOp(peek(), '+', '-')) {
      const op = take()[1]
      const rhs = term()
      v = op === '+' ? v + rhs : v - rhs
    }
    return v
  }
  try {
    const result = expression()
    return i === tokens.length ? tidy(result) : ERROR
  } catch (err) {
    if (err instanceof ExprError) return err.code
    throw err
  }
}

// Row indexes [start, end) summed by a section total at rowIndex in colId.
export function sectionRows(sheet, rowIndex, colId) {
  let end = rowIndex + 1
  while (end < sheet.rows.length) {
    const cell = sheet.rows[end].cells[colId]
    if (cell && TOTAL_KINDS.has(cell.kind)) break
    end += 1
  }
  return [rowIndex + 1, end]
}

export const key = (rowId, colId) => `${rowId}|${colId}`

// Values of all calculated cells, keyed by key(rowId, colId).
export function compute(sheet) {
  const values = new Map()
  const colIds = new Set(sheet.columns.map((c) => c.id))

  for (const row of sheet.rows) {
    for (const [colId, cell] of Object.entries(row.cells)) {
      if (cell.kind !== 'row_formula') continue
      values.set(
        key(row.id, colId),
        evaluateExpr(cell.expr || '', (ref) => {
          if (!colIds.has(ref)) throw new ExprError(REF_ERROR)
          const other = row.cells[ref]
          if (!other) return null
          if (other.kind === 'row_formula') return values.has(key(row.id, ref)) ? values.get(key(row.id, ref)) : ERROR
          return other.v
        }),
      )
    }
  }

  const numeric = (rowIndex, colId) => {
    const row = sheet.rows[rowIndex]
    const cell = row.cells[colId]
    if (!cell) return 0
    const v = cell.kind === 'row_formula' ? values.get(key(row.id, colId)) : cell.v
    return asNumber(v) ?? 0
  }

  sheet.rows.forEach((row, index) => {
    for (const [colId, cell] of Object.entries(row.cells)) {
      if (cell.kind !== 'section_total') continue
      const [start, end] = sectionRows(sheet, index, colId)
      let total = 0
      for (let i = start; i < end; i += 1) total += numeric(i, colId)
      values.set(key(row.id, colId), tidy(total))
    }
  })

  for (const row of sheet.rows) {
    for (const [colId, cell] of Object.entries(row.cells)) {
      if (cell.kind !== 'grand_total') continue
      let total = 0
      for (const r of sheet.rows) {
        const c = r.cells[colId]
        if (c && c.kind === 'section_total') total += asNumber(values.get(key(r.id, colId))) ?? 0
      }
      values.set(key(row.id, colId), tidy(total))
    }
  }
  return values
}
