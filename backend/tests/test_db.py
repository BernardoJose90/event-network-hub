from __future__ import annotations

import sqlite3
from pathlib import Path

from app import db


def test_migration_adds_format_column_and_keeps_rows(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE documents (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
        CREATE TABLE document_versions (id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL,
            version INTEGER NOT NULL, sha256 TEXT NOT NULL, uploaded_by TEXT NOT NULL,
            uploaded_at TEXT NOT NULL, status TEXT NOT NULL, warnings TEXT NOT NULL DEFAULT '[]',
            error TEXT, UNIQUE (document_id, version));
        CREATE TABLE download_log (id INTEGER PRIMARY KEY, version_id INTEGER NOT NULL,
            user TEXT NOT NULL, downloaded_at TEXT NOT NULL);
        INSERT INTO download_log (version_id, user, downloaded_at) VALUES (1, 'alice', 'then');
        """
    )
    old.commit()
    old.close()

    db.init_db(path)
    db.init_db(path)  # idempotent

    conn = db.connect(path)
    rows = [dict(r) for r in conn.execute("SELECT user, format FROM download_log")]
    version_columns = {r["name"] for r in conn.execute("PRAGMA table_info(document_versions)")}
    conn.close()
    assert rows == [{"user": "alice", "format": "pdf"}]
    assert "warnings" not in version_columns and {"edited_from", "source"} <= version_columns
