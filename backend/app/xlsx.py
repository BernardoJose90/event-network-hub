"""Workbook inspection and redaction with openpyxl."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from .config import FORMULA_ERRORS, HEADER_SEARCH_ROWS, REDACTED_PLACEHOLDER, SENSITIVE_HEADERS


@dataclass(frozen=True)
class SensitiveColumn:
    column: int
    header_row: int


def _is_sensitive_header(value: Any) -> bool:
    return isinstance(value, str) and value.strip().casefold() in SENSITIVE_HEADERS


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def sensitive_columns(ws: Worksheet) -> list[SensitiveColumn]:
    found: list[SensitiveColumn] = []
    for row in ws.iter_rows(min_row=1, max_row=HEADER_SEARCH_ROWS):
        for cell in row:
            if _is_sensitive_header(cell.value):
                found.append(SensitiveColumn(column=cell.column, header_row=cell.row))
    return found


def _cells_below(ws: Worksheet, col: SensitiveColumn):
    for (cell,) in ws.iter_rows(
        min_row=col.header_row + 1, max_row=ws.max_row, min_col=col.column, max_col=col.column
    ):
        yield cell


def redact_workbook(data: bytes) -> tuple[bytes, int]:
    """Return (redacted .xlsx bytes, number of cells redacted).

    Every non-empty cell below a sensitive header (searched in the first
    HEADER_SEARCH_ROWS rows of each sheet) is replaced with REDACTED_PLACEHOLDER.
    """
    wb = load_workbook(io.BytesIO(data))
    redacted = 0
    for ws in wb.worksheets:
        if not isinstance(ws, Worksheet):
            continue  # chartsheets have no cells
        for col in sensitive_columns(ws):
            for cell in _cells_below(ws, col):
                # Merged-cell continuations are read-only and always None.
                if not _is_empty(cell.value):
                    cell.value = REDACTED_PLACEHOLDER
                    redacted += 1
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue(), redacted


def find_formula_errors(data: bytes) -> list[dict[str, str]]:
    """Return [{sheet, cell, error}] for every cell whose (cached) value is a spreadsheet error."""
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        found: list[dict[str, str]] = []
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    value = getattr(cell, "value", None)
                    if isinstance(value, str) and value.strip() in FORMULA_ERRORS:
                        found.append(
                            {"sheet": ws.title, "cell": cell.coordinate, "error": value.strip()}
                        )
        return found
    finally:
        wb.close()


def visible_sheet_count(data: bytes) -> int:
    wb = load_workbook(io.BytesIO(data), read_only=True)
    try:
        return sum(1 for name in wb.sheetnames if wb[name].sheet_state == "visible")
    finally:
        wb.close()


def sensitive_values(data: bytes) -> list[str]:
    """All non-empty values under sensitive headers, as displayed strings. Used by the smoke test."""
    wb = load_workbook(io.BytesIO(data), data_only=True)
    values: list[str] = []
    for ws in wb.worksheets:
        if not isinstance(ws, Worksheet):
            continue
        for col in sensitive_columns(ws):
            for cell in _cells_below(ws, col):
                if not _is_empty(cell.value):
                    values.append(str(cell.value).strip())
    return values
