"""Shared fixtures. The tests run on PostgreSQL, on a database built by the migrations.

TEST_DATABASE_URL names a throwaway database (its name must end in _test: every table is
emptied before each test). It is created if missing; docker compose run --rm tests sets it up.
"""

from __future__ import annotations

import io
import os
from collections.abc import Callable, Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from openpyxl import Workbook
from psycopg.conninfo import conninfo_to_dict, make_conninfo

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://enh:test@127.0.0.1:55432/enh_test")
if not conninfo_to_dict(TEST_DATABASE_URL).get("dbname", "").endswith("_test"):
    raise RuntimeError("TEST_DATABASE_URL must name a database ending in _test: the tests empty it")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL  # app.main builds an app from the environment on import

from app import db  # noqa: E402
from app.storage import LocalStorage  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent


def alembic_config(url: str = TEST_DATABASE_URL) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.attributes["database_url"] = url
    config.attributes["configure_logger"] = False
    return config


def _create_database(url: str) -> None:
    params = conninfo_to_dict(url)
    name = params["dbname"]
    with psycopg.connect(make_conninfo(url, dbname="postgres"), autocommit=True) as admin:
        if not admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            admin.execute(f'CREATE DATABASE "{name}"')


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> Iterator[str]:
    """A fresh schema, built the way production builds it: alembic upgrade head."""
    _create_database(TEST_DATABASE_URL)
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE")
        c.execute("CREATE SCHEMA public")
    command.upgrade(alembic_config(), "head")
    yield TEST_DATABASE_URL
    db.close_pools()


@pytest.fixture(autouse=True)
def empty_tables(migrated_database: str) -> None:
    """Every test starts with empty tables and ids from 1."""
    with psycopg.connect(migrated_database, autocommit=True) as c:
        tables = [r[0] for r in c.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'")]
        c.execute("SET lock_timeout = '5s'")  # a leaked open transaction fails the test, not hangs it
        c.execute(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")


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


@pytest.fixture
def conn() -> Iterator[db.Connection]:
    c = db.connect(TEST_DATABASE_URL)
    yield c
    c.close()


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "files")


@pytest.fixture
def converter() -> FakeConverter:
    return FakeConverter()


@pytest.fixture
def document(conn: db.Connection) -> dict:
    return db.create_document(conn, "LIS25 Network Requirements")
