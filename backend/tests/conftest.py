from __future__ import annotations

import io
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from openpyxl import Workbook

from app import db
from app.storage import LocalStorage


def xlsx_bytes(build: Callable[[Workbook], None] | None = None) -> bytes:
    """Build a workbook in memory. ``build`` receives a fresh Workbook to populate."""
    wb = Workbook()
    if build is None:
        wb.active["A1"] = "hello"
    else:
        build(wb)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


class FakeConverter:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail = False

    def xlsx_to_pdf(self, data: bytes, *, filename: str) -> bytes:
        from app.converter import ConversionError

        self.calls.append(filename)
        if self.fail:
            raise ConversionError("PDF conversion failed (HTTP 503)")
        return b"%PDF-1.7 fake " + filename.encode()


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    path = tmp_path / "test.sqlite3"
    db.init_db(path)
    c = db.connect(path)
    yield c
    c.close()


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "files")


@pytest.fixture
def converter() -> FakeConverter:
    return FakeConverter()


@pytest.fixture
def document(conn: sqlite3.Connection) -> dict:
    return db.create_document(conn, "LIS25 Network Requirements")
