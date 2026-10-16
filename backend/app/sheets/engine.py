"""Calculated cells of a live sheet. Mirrored in frontend/src/sheet/engine.js.

- section_total: sum of the numbers in its column from the next row until the
  next section/grand total in that column (or the end of the sheet). Rows added
  inside a section are therefore always counted.
- grand_total: sum of every section total in its column.
- row_formula: + - * / and parentheses over cells of the same row, written with
  column ids, e.g. "{c8}-{c7}". Parsed by a tiny recursive-descent parser.
"""

from __future__ import annotations

import re
from typing import Any

from .model import TOTAL_KINDS, Sheet

ERROR = "#VALUE!"
REF_ERROR = "#REF!"

_NUMBER = re.compile(r"^\s*-?\d+(\.\d+)?\s*$")
_TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d+)?)|\{([A-Za-z0-9_-]+)\}|([-+*/()]))")


def as_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and _NUMBER.match(value):
        return float(value)
    return None


def _tidy(x: float) -> int | float:
    return int(x) if float(x).is_integer() else round(x, 10)


class _ExprError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


def evaluate_expr(expr: str, lookup) -> int | float | str:
    """Evaluate a row formula; ``lookup(col_id)`` returns the cell value or raises KeyError."""
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(expr):
        if expr[pos:].strip() == "":
            break
        m = _TOKEN.match(expr, pos)
        if not m:
            return ERROR
        num, ref, op = m.groups()
        tokens.append(("num", num) if num else ("ref", ref) if ref else ("op", op))
        pos = m.end()

    i = 0

    def peek() -> tuple[str, str] | None:
        return tokens[i] if i < len(tokens) else None

    def take() -> tuple[str, str]:
        nonlocal i
        if i >= len(tokens):
            raise _ExprError(ERROR)
        i += 1
        return tokens[i - 1]

    def atom() -> float:
        kind, text = take()
        if kind == "num":
            return float(text)
        if kind == "ref":
            try:
                value = lookup(text)
            except KeyError:
                raise _ExprError(REF_ERROR)
            if value is None or value == "":
                return 0.0
            n = as_number(value)
            if n is None:
                raise _ExprError(ERROR)
            return n
        if text == "(":
            v = expr_()
            if take() != ("op", ")"):
                raise _ExprError(ERROR)
            return v
        if text == "-":
            return -atom()
        if text == "+":
            return atom()
        raise _ExprError(ERROR)

    def term() -> float:
        v = atom()
        while peek() in (("op", "*"), ("op", "/")):
            op = take()[1]
            rhs = atom()
            if op == "/" and rhs == 0:
                raise _ExprError("#DIV/0!")
            v = v * rhs if op == "*" else v / rhs
        return v

    def expr_() -> float:
        v = term()
        while peek() in (("op", "+"), ("op", "-")):
            op = take()[1]
            rhs = term()
            v = v + rhs if op == "+" else v - rhs
        return v

    try:
        result = expr_()
        if i != len(tokens):
            return ERROR
        return _tidy(result)
    except _ExprError as exc:
        return exc.code


def section_rows(sheet: Sheet, row_index: int, col_id: str) -> range:
    """Row indexes summed by a section total at ``row_index`` in ``col_id``."""
    end = row_index + 1
    while end < len(sheet.rows):
        cell = sheet.rows[end].cells.get(col_id)
        if cell is not None and cell.kind in TOTAL_KINDS:
            break
        end += 1
    return range(row_index + 1, end)


def compute(sheet: Sheet) -> dict[tuple[str, str], Any]:
    """Values of all calculated cells, keyed by (row id, column id)."""
    values: dict[tuple[str, str], Any] = {}
    col_ids = {c.id for c in sheet.columns}

    # Row formulas first: totals may add them up.
    for row in sheet.rows:
        for col_id, cell in row.cells.items():
            if cell.kind == "row_formula":
                def lookup(ref: str, row=row) -> Any:
                    if ref not in col_ids:
                        raise KeyError(ref)
                    other = row.cells.get(ref)
                    if other is None:
                        return None
                    if other.kind == "row_formula":
                        return values.get((row.id, ref), ERROR)
                    return other.v
                values[(row.id, col_id)] = evaluate_expr(cell.expr or "", lookup)

    def numeric(row_index: int, col_id: str) -> float:
        row = sheet.rows[row_index]
        cell = row.cells.get(col_id)
        if cell is None:
            return 0.0
        v = values.get((row.id, col_id)) if cell.kind == "row_formula" else cell.v
        return as_number(v) or 0.0

    for index, row in enumerate(sheet.rows):
        for col_id, cell in row.cells.items():
            if cell.kind == "section_total":
                total = sum(numeric(i, col_id) for i in section_rows(sheet, index, col_id))
                values[(row.id, col_id)] = _tidy(total)

    for row in sheet.rows:
        for col_id, cell in row.cells.items():
            if cell.kind == "grand_total":
                total = sum(
                    as_number(values.get((r.id, col_id))) or 0.0
                    for r in sheet.rows
                    if (c := r.cells.get(col_id)) is not None and c.kind == "section_total"
                )
                values[(row.id, col_id)] = _tidy(total)
    return values
