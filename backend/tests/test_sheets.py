from __future__ import annotations

import io

import pytest
from openpyxl import load_workbook
from pydantic import ValidationError

from app.sheets.engine import compute, evaluate_expr
from app.sheets.exporter import export_workbook
from app.sheets.importer import import_workbook
from app.sheets.model import Cell, Row, Sheet, Workbook

from .sheet_fixture import sheet_workbook


@pytest.fixture(scope="module")
def wb() -> Workbook:
    return import_workbook(sheet_workbook())


def _sheet(wb: Workbook, name: str) -> Sheet:
    return next(s for s in wb.sheets if s.name == name)


def _cell(sheet: Sheet, row: int, col: int) -> Cell | None:
    return sheet.rows[row - 1].cells.get(f"c{col}")


def _value(sheet: Sheet, row: int, col: int):
    return compute(sheet).get((f"r{row}", f"c{col}"))


# --- import ---------------------------------------------------------------


def test_totals_are_recognised(wb: Workbook) -> None:
    eth = _sheet(wb, "Ethernet")
    assert [_cell(eth, r, 4).kind for r in (2, 6, 7, 9)] == [
        "section_total", "section_total", "section_total", "grand_total"]


def test_row_formula_uses_column_ids(wb: Workbook) -> None:
    cell = _cell(_sheet(wb, "Changes"), 2, 2)
    assert (cell.kind, cell.expr) == ("row_formula", "{c8}-{c7}")


def test_unsupported_formula_kept_with_warning(wb: Workbook) -> None:
    assert any("Changes!C2" in w and "AVERAGE" in w for w in wb.warnings)
    assert _cell(_sheet(wb, "Changes"), 2, 3) is None  # no cached value in the fixture


def test_dropdown_sources(wb: Workbook) -> None:
    eth, setup_id = _sheet(wb, "Ethernet"), _sheet(wb, "Setup").id
    loc, vlan = _cell(eth, 3, 2), _cell(eth, 3, 3)
    assert (loc.kind, loc.v, loc.source.sheet, loc.source.column) == ("dropdown", "MEO Arena", setup_id, "c1")
    assert (vlan.source.sheet, vlan.source.column) == (setup_id, "c4")  # via the VLANOptions name
    assert _cell(_sheet(wb, "Wi-Fi"), 3, 5).source.values == ["Yes", "No", "TBC"]
    assert _cell(_sheet(wb, "Wi-Fi"), 4, 5).kind == "dropdown"  # empty row that still has a dropdown


def test_checkboxes_merges_frozen_and_styles(wb: Workbook) -> None:
    vlans = _sheet(wb, "Extending Vlans")
    assert [(_cell(vlans, r, 2).kind, _cell(vlans, r, 2).v) for r in (2, 3)] == [("checkbox", True), ("checkbox", False)]
    wifi = _sheet(wb, "Wi-Fi")
    assert [(m.row, m.col, m.span) for m in wifi.merges] == [("r2", "c1", 4)]
    assert _cell(wifi, 2, 2) is None  # hidden under the merge
    eth = _sheet(wb, "Ethernet")
    assert eth.frozen_rows == 1 and eth.font == "Overpass" and eth.font_size == 10
    header = _cell(eth, 2, 1).style
    assert (header.fill, header.bold, header.color) == ("#666666", True, "#FFFFFF")
    assert eth.columns[0].width == round(32 * 7 + 5)


def test_setup_is_trimmed(wb: Workbook) -> None:
    setup = _sheet(wb, "Setup")
    assert (len(setup.rows), len(setup.columns)) == (4, 4)


# --- engine ---------------------------------------------------------------


def test_section_totals_count_every_row_in_the_section(wb: Workbook) -> None:
    eth = _sheet(wb, "Ethernet")
    # =SUM(D3:D4) would give 5; the section really holds 2 + 3 + 98.
    assert [_value(eth, r, 4) for r in (2, 6, 7, 9)] == [103, 0, 5, 108]


def test_new_row_in_a_section_is_counted(wb: Workbook) -> None:
    eth = _sheet(wb, "Ethernet").model_copy(deep=True)
    eth.rows.insert(3, Row(id="new1", cells={"c1": Cell(v="Kiosk"), "c4": Cell(v=10, kind="number")}))
    values = compute(eth)
    assert values[("r2", "c4")] == 113
    assert values[("r9", "c4")] == 118


def test_typed_numbers_as_text_still_add_up(wb: Workbook) -> None:
    eth = _sheet(wb, "Ethernet").model_copy(deep=True)
    eth.rows[2].cells["c4"] = Cell(v="7")
    assert compute(eth)[("r2", "c4")] == 108


def test_row_formulas(wb: Workbook) -> None:
    changes = _sheet(wb, "Changes")
    assert (_value(changes, 2, 2), _value(changes, 3, 2)) == (2, -21)


@pytest.mark.parametrize(
    ("expr", "cells", "expected"),
    [
        ("({a}+2)*3", {"a": 1}, 9),
        ("-{a}+{b}/2", {"a": 1, "b": 5}, 1.5),
        ("{a}-{b}", {"a": None, "b": 4}, -4),
        ("{a}*2", {"a": "text"}, "#VALUE!"),
        ("{a}/{b}", {"a": 1, "b": 0}, "#DIV/0!"),
        ("{gone}+1", {}, "#REF!"),
        ("{a}+", {"a": 1}, "#VALUE!"),
        ("__import__('os')", {}, "#VALUE!"),
    ],
)
def test_expression_parser(expr, cells, expected) -> None:
    def lookup(ref):
        if ref not in cells:
            raise KeyError(ref)
        return cells[ref]

    assert evaluate_expr(expr, lookup) == expected


# --- export ---------------------------------------------------------------


def test_export_round_trip_is_lossless(wb: Workbook) -> None:
    again = import_workbook(export_workbook(wb))
    for a, b in zip(wb.sheets, again.sheets):
        assert (a.name, a.frozen_rows, a.merges, [c.width for c in a.columns]) == \
               (b.name, b.frozen_rows, b.merges, [c.width for c in b.columns])
        assert [r.cells for r in a.rows] == [r.cells for r in b.rows]
        assert compute(a) == compute(b)


def test_export_writes_real_formulas_and_dropdowns(wb: Workbook) -> None:
    x = load_workbook(io.BytesIO(export_workbook(wb)))
    eth = x["Ethernet"]
    assert [eth[c].value for c in ("D2", "D6", "D7", "D9")] == [
        "=SUM(D3:D5)", '=N("section total")', "=SUM(D8:D8)", "=SUM(D2,D6,D7)"]
    assert x["Changes"]["B2"].value == "=H2-G2"
    assert x["Extending Vlans"]["B2"].value is True
    sources = {dv.formula1 for dv in eth.data_validations.dataValidation}
    assert sources == {"'Setup'!$A$1:$A$4", "'Setup'!$D$1:$D$4"}
    assert {dv.formula1 for dv in x["Wi-Fi"].data_validations.dataValidation} == {'"Yes,No,TBC"'}
    assert eth["A2"].font.name == "Overpass" and eth["A2"].fill.fgColor.rgb == "FF666666"


# --- validation -----------------------------------------------------------


def _one_sheet(**overrides) -> dict:
    sheet = {"id": "s1", "name": "S", "columns": [{"id": "c1"}], "rows": [{"id": "r1", "cells": {}}]}
    sheet.update(overrides)
    return {"sheets": [sheet]}


@pytest.mark.parametrize(
    "data",
    [
        _one_sheet(columns=[{"id": "c1"}, {"id": "c1"}]),
        _one_sheet(rows=[{"id": "r1", "cells": {"c9": {"v": "x"}}}]),
        _one_sheet(rows=[{"id": "r1", "cells": {"c1": {"kind": "dropdown"}}}]),
        _one_sheet(rows=[{"id": "r1", "cells": {"c1": {"kind": "checkbox", "v": "yes"}}}]),
        _one_sheet(rows=[{"id": "r1", "cells": {"c1": {"kind": "dropdown", "source": {"sheet": "s1", "column": "c7"}}}}]),
        _one_sheet(rows=[{"id": "r1", "cells": {"c1": {"v": "x", "style": {"fill": "red"}}}}]),
        _one_sheet(rows=[{"id": "r1", "cells": {"c1": {"v": "x", "onclick": "alert(1)"}}}]),
        _one_sheet(id="bad id!"),
    ],
)
def test_invalid_sheets_are_rejected(data) -> None:
    with pytest.raises(ValidationError):
        Workbook.model_validate(data)
