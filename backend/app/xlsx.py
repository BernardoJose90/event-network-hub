"""Workbook inspection with openpyxl."""

from __future__ import annotations

import io

from openpyxl import load_workbook

from .config import FORMULA_ERRORS


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
