"""Copying the SQLite database into PostgreSQL (app/copy_from_sqlite.py)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import psycopg
import pytest

from app import copy_from_sqlite, db
from app.storage import LocalStorage, version_key

from .conftest import TEST_DATABASE_URL

# The SQLite tables as earlier builds created them (a few of them: missing ones stay empty).
SQLITE_SCHEMA = """
CREATE TABLE documents (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'event', archived_at TEXT, archived_by TEXT);
CREATE TABLE tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER, event_code TEXT, title TEXT NOT NULL,
    description TEXT, category TEXT NOT NULL, severity TEXT NOT NULL, status TEXT NOT NULL, team TEXT NOT NULL,
    assignee TEXT, location TEXT, stand TEXT, partner TEXT, reporter_name TEXT, reporter_phone TEXT,
    source TEXT NOT NULL, external_id TEXT UNIQUE, merged_into INTEGER, root_cause TEXT, resolution TEXT,
    created_by TEXT NOT NULL, created_at TEXT NOT NULL, acknowledged_at TEXT, acknowledged_by TEXT,
    resolved_at TEXT, closed_at TEXT, updated_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE comments (id INTEGER PRIMARY KEY AUTOINCREMENT, document_id INTEGER NOT NULL, parent_id INTEGER,
    record_id INTEGER, record_type TEXT, item_label TEXT, item_where TEXT, version INTEGER, body TEXT NOT NULL,
    user TEXT NOT NULL, role TEXT NOT NULL, created_at TEXT NOT NULL, resolved_at TEXT, resolved_by TEXT);
CREATE TABLE helpdesk_settings (key TEXT PRIMARY KEY, value TEXT);
"""
NOW = "2026-10-01T09:00:00+00:00"


def _ticket(source: sqlite3.Connection, ticket_id: int, merged_into: int | None = None) -> None:
    source.execute("INSERT INTO tickets (id, event_id, title, category, severity, status, team, source, merged_into, "
                   "created_by, created_at, updated_at) VALUES (?, 7, 'Wi-Fi down', 'wifi', 'P2', 'new', 'network', "
                   "'manual', ?, 'alice', ?, ?)", (ticket_id, merged_into, NOW, NOW))


@pytest.fixture
def source(tmp_path: Path) -> sqlite3.Connection:
    path = tmp_path / "portal.sqlite3"
    src = sqlite3.connect(path)
    src.executescript(SQLITE_SCHEMA)
    src.execute("INSERT INTO documents (id, name, created_at) VALUES (7, 'LIS26 | Event_Network_Requirements', ?)", (NOW,))
    src.execute("INSERT INTO documents (id, name, created_at) VALUES (9, 'VAN26 | Event_Network_Requirements', ?)", (NOW,))
    src.execute("DELETE FROM documents WHERE id = 9")  # 9 was used once: new ids must start at 10
    _ticket(src, 3, merged_into=5)  # merged into a ticket that comes later
    _ticket(src, 5)
    src.execute("INSERT INTO comments (id, document_id, parent_id, body, user, role, created_at) "
                "VALUES (1, 7, NULL, 'Is the press Wi-Fi 6GHz?', 'bob', 'viewer', ?)", (NOW,))
    src.execute("INSERT INTO comments (id, document_id, parent_id, body, user, role, created_at) "
                "VALUES (2, 7, 1, 'Yes', 'alice', 'network-admin', ?)", (NOW,))
    src.execute("INSERT INTO helpdesk_settings (key, value) VALUES ('live_event_id', 7)")  # an integer in a TEXT column
    src.commit()
    yield path
    src.close()


def _copy(path: Path) -> dict[str, int]:
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        with psycopg.connect(TEST_DATABASE_URL) as pg:
            return copy_from_sqlite.copy_rows(src, pg, log=lambda _: None)
    finally:
        src.close()


def test_rows_keep_their_ids_and_links(source: Path, conn: db.Connection) -> None:
    counts = _copy(source)
    assert (counts["documents"], counts["tickets"], counts["comments"], counts["records"]) == (1, 2, 2, 0)
    assert db.get_document(conn, 7)["name"] == "LIS26 | Event_Network_Requirements"
    assert conn.execute("SELECT id, merged_into FROM tickets ORDER BY id").fetchall() == [(3, 5), (5, None)]
    assert conn.execute('SELECT id, parent_id, "user" FROM comments ORDER BY id').fetchall() == [
        (1, None, "bob"), (2, 1, "alice")]
    assert conn.execute("SELECT value FROM helpdesk_settings").fetchone()[0] == "7"
    # new rows continue after the highest id SQLite ever handed out
    assert db.create_document(conn, "RIO26 | Event_Network_Requirements")["id"] == 10
    assert conn.execute("INSERT INTO tickets (title, category, severity, status, team, source, created_by, created_at, "
                        "updated_at) VALUES ('x', 'wifi', 'P3', 'new', 'network', 'manual', 'a', %s, %s) RETURNING id",
                        (NOW, NOW)).fetchone()[0] == 6


def test_refuses_a_target_that_already_has_rows(source: Path, conn: db.Connection) -> None:
    db.create_document(conn, "LIS25 | Event_Network_Requirements")
    with pytest.raises(copy_from_sqlite.CopyError, match="isn't empty"):
        _copy(source)


def test_refuses_columns_postgres_has_no_place_for(source: Path) -> None:
    src = sqlite3.connect(source)
    src.execute("ALTER TABLE documents ADD COLUMN colour TEXT")
    src.commit()
    src.close()
    with pytest.raises(copy_from_sqlite.CopyError, match="colour"):
        _copy(source)


def test_a_failed_copy_writes_nothing(source: Path, conn: db.Connection) -> None:
    src = sqlite3.connect(source)
    src.execute("UPDATE comments SET parent_id = 99 WHERE id = 2")  # a reply to a comment that doesn't exist
    src.commit()
    src.close()
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _copy(source)
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


def test_main_copies_files_then_rows(source: Path, tmp_path: Path, monkeypatch, capsys) -> None:
    files = tmp_path / "files"
    LocalStorage(files).put(version_key(7, 1, "full.pdf"), b"%PDF")
    (files / "documents" / "7" / "v1" / ".tmp-abc").write_bytes(b"half-written")  # skipped
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "target"))
    assert copy_from_sqlite.main(["--sqlite", str(source), "--dry-run"]) == 0
    assert "rolled back" in capsys.readouterr().out
    conn = db.connect(TEST_DATABASE_URL)
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    conn.close()

    assert copy_from_sqlite.main(["--sqlite", str(source), "--files-dir", str(files)]) == 0
    assert "1 files copied" in capsys.readouterr().out
    assert LocalStorage(tmp_path / "target" / "files").get(version_key(7, 1, "full.pdf")) == b"%PDF"
    conn = db.connect(TEST_DATABASE_URL)
    assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 2
    conn.close()
    assert copy_from_sqlite.main(["--sqlite", str(source)]) == 1  # a second run is refused
    assert "isn't empty" in capsys.readouterr().err
