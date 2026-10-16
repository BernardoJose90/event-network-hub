"""Workarounds applied to a workbook before it is sent to LibreOffice.

The original upload is stored untouched; only the copy that is converted is
rewritten. Edits are made directly on the sheet XML inside the zip (not via an
openpyxl round-trip) so nothing else in the workbook changes.
"""

from __future__ import annotations

import io
import re
import zipfile

_SHEET_XML = re.compile(r"^xl/worksheets/[^/]+\.xml$")
_PRINT_OPTIONS = re.compile(rb"<printOptions\b[^>]*>")
_GRIDLINES_ATTR = re.compile(rb"""\s(?:gridLines|gridLinesSet)\s*=\s*(?:"[^"]*"|'[^']*')""")


def _strip_print_gridlines(xml: bytes) -> bytes:
    return _PRINT_OPTIONS.sub(lambda m: _GRIDLINES_ATTR.sub(b"", m.group(0)), xml)


def prepare_for_pdf(data: bytes) -> bytes:
    """Return a copy of the workbook with LibreOffice workarounds applied.

    Print gridlines are turned off. With singlePageSheets=true, LibreOffice
    (seen with 26.8, the version in gotenberg 8.37.0) draws printed gridlines
    at the wrong positions, so lines cut through text. Cell borders are
    unaffected and still render.
    """
    src = zipfile.ZipFile(io.BytesIO(data))
    changed = False
    entries: list[tuple[zipfile.ZipInfo, bytes]] = []
    for info in src.infolist():
        content = src.read(info)
        if _SHEET_XML.match(info.filename):
            new = _strip_print_gridlines(content)
            if new != content:
                content, changed = new, True
        entries.append((info, content))
    if not changed:
        return data

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info, content in entries:
            dst.writestr(info, content)
    return out.getvalue()
