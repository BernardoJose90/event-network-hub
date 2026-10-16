"""Live sheet data model, validated on every save.

Rows and columns have stable ids, so inserting or deleting never breaks the
calculated cells: totals are defined by position relative to their own row,
and row formulas refer to columns by id, e.g. ``"{c8}-{c7}"``.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_SHEETS = 40
MAX_ROWS = 5000
MAX_COLUMNS = 200
MAX_TEXT = 5000

CellKind = Literal["text", "number", "checkbox", "dropdown", "section_total", "grand_total", "row_formula"]
CALCULATED_KINDS = frozenset({"section_total", "grand_total", "row_formula"})
TOTAL_KINDS = frozenset({"section_total", "grand_total"})

_ID = r"^[A-Za-z0-9_-]{1,40}$"
_HEX = r"^#[0-9A-Fa-f]{6}$"
EXPR_REF = re.compile(r"\{([A-Za-z0-9_-]{1,40})\}")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Style(_Strict):
    font: str | None = Field(default=None, max_length=60)  # only when it differs from the sheet's font
    bold: bool | None = None
    italic: bool | None = None
    size: float | None = Field(default=None, ge=4, le=72)
    color: str | None = Field(default=None, pattern=_HEX)
    fill: str | None = Field(default=None, pattern=_HEX)
    align: Literal["left", "center", "right"] | None = None
    wrap: bool | None = None


class Source(_Strict):
    """Where a dropdown's options come from: another sheet's column, or a fixed list."""

    sheet: str | None = Field(default=None, pattern=_ID)
    column: str | None = Field(default=None, pattern=_ID)
    values: list[str] | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _one_kind(self) -> Source:
        if (self.values is None) == (self.sheet is None or self.column is None):
            raise ValueError("a dropdown source is either sheet+column or a list of values")
        return self


class Cell(_Strict):
    v: str | bool | int | float | None = None
    kind: CellKind = "text"
    style: Style | None = None
    source: Source | None = None
    expr: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _consistent(self) -> Cell:
        if isinstance(self.v, str) and len(self.v) > MAX_TEXT:
            raise ValueError(f"cell text longer than {MAX_TEXT} characters")
        if self.kind == "dropdown" and self.source is None:
            raise ValueError("dropdown cell without a source")
        if self.kind == "row_formula" and not self.expr:
            raise ValueError("row formula without an expression")
        if self.kind == "checkbox" and self.v is not None and not isinstance(self.v, bool):
            raise ValueError("checkbox value must be true or false")
        return self


class Column(_Strict):
    id: str = Field(pattern=_ID)
    width: int = Field(default=100, ge=10, le=2000)


class Row(_Strict):
    id: str = Field(pattern=_ID)
    cells: dict[str, Cell] = Field(default_factory=dict)


class Merge(_Strict):
    row: str = Field(pattern=_ID)
    col: str = Field(pattern=_ID)
    span: int = Field(ge=2, le=MAX_COLUMNS)


class Sheet(_Strict):
    id: str = Field(pattern=_ID)
    name: str = Field(min_length=1, max_length=100)
    frozen_rows: int = Field(default=0, ge=0, le=20)
    font: str = Field(default="Overpass", max_length=60)  # the sheet's default font
    font_size: float = Field(default=10, ge=4, le=72)
    columns: list[Column] = Field(max_length=MAX_COLUMNS)
    rows: list[Row] = Field(max_length=MAX_ROWS)
    merges: list[Merge] = Field(default_factory=list, max_length=2000)

    @model_validator(mode="after")
    def _references(self) -> Sheet:
        col_ids = [c.id for c in self.columns]
        if len(set(col_ids)) != len(col_ids):
            raise ValueError(f"sheet {self.name!r}: duplicate column ids")
        row_ids = [r.id for r in self.rows]
        if len(set(row_ids)) != len(row_ids):
            raise ValueError(f"sheet {self.name!r}: duplicate row ids")
        known = set(col_ids)
        for row in self.rows:
            unknown = set(row.cells) - known
            if unknown:
                raise ValueError(f"sheet {self.name!r}: cells in unknown columns {sorted(unknown)}")
        rows = set(row_ids)
        for m in self.merges:
            if m.row not in rows or m.col not in known:
                raise ValueError(f"sheet {self.name!r}: merge refers to an unknown row or column")
        return self


class Workbook(_Strict):
    sheets: list[Sheet] = Field(min_length=1, max_length=MAX_SHEETS)
    warnings: list[str] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def _unique_sheets(self) -> Workbook:
        ids = [s.id for s in self.sheets]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate sheet ids")
        names = [s.name for s in self.sheets]
        if len(set(names)) != len(names):
            raise ValueError("duplicate sheet names")
        columns = {s.id: {c.id for c in s.columns} for s in self.sheets}
        for sheet in self.sheets:
            for row in sheet.rows:
                for cell in row.cells.values():
                    src = cell.source
                    if src is not None and src.sheet is not None and src.column not in columns.get(src.sheet, ()):
                        raise ValueError(f"sheet {sheet.name!r}: dropdown source refers to a missing sheet or column")
        return self
