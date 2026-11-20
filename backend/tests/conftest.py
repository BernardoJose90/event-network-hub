from __future__ import annotations

import io
import os
import uuid
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from openpyxl import Workbook

from app import db, pg
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

    def html_to_pdf(self, html: str) -> bytes:
        from app.converter import ConversionError

        self.calls.append("index.html")
        self.html = html
        if self.fail:
            raise ConversionError("PDF conversion failed (HTTP 503)")
        return b"%PDF-1.7 fake report"


# A PostgreSQL server the tests may create and drop schemas in (docker compose's postgres service).
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/event_network_hub")


@pytest.fixture(scope="session")
def _schemas() -> Iterator[list[str]]:
    """Test schemas, dropped once at the end: a test's background workers may still be
    finishing a query when the test itself ends."""
    created: list[str] = []
    yield created
    admin = pg.connect(pg.Database.from_url(TEST_DATABASE_URL))
    try:
        for schema in created:
            admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    finally:
        admin.close()


@pytest.fixture(autouse=True)
def database(monkeypatch: pytest.MonkeyPatch, _schemas: list[str]) -> pg.Database:
    """Every test gets its own empty schema, and every Settings() built in a test points at it."""
    server = pg.Database.from_url(TEST_DATABASE_URL)
    schema = f"test_{uuid.uuid4().hex[:16]}"
    admin = pg.connect(server)
    try:
        admin.execute(f"CREATE SCHEMA {schema}")
    finally:
        admin.close()
    _schemas.append(schema)
    database = replace(server, search_path=schema)
    monkeypatch.setattr(pg.Database, "from_env", classmethod(lambda cls: database))
    return database


@pytest.fixture
def conn(database: pg.Database) -> Iterator[pg.Connection]:
    db.init_db(database)
    c = db.connect(database)
    yield c
    c.close()


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "files")


@pytest.fixture
def converter() -> FakeConverter:
    return FakeConverter()


@pytest.fixture
def document(conn: pg.Connection) -> dict:
    return db.create_document(conn, "LIS25 Network Requirements")
