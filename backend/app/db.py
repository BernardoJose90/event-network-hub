"""SQLite persistence. All SQL lives in this module."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATUS_PROCESSING = "processing"
STATUS_READY = "ready"
STATUS_FAILED = "failed"

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS document_versions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version      INTEGER NOT NULL,
    sha256       TEXT NOT NULL,
    uploaded_by  TEXT NOT NULL,
    uploaded_at  TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('processing', 'ready', 'failed')),
    warnings     TEXT NOT NULL DEFAULT '[]',
    error        TEXT,
    edited_from  INTEGER,
    UNIQUE (document_id, version)
);

-- One row per in-browser editing session. Its first save creates
-- saved_version; later saves in the same session update that version.
CREATE TABLE IF NOT EXISTS edit_sessions (
    id             TEXT PRIMARY KEY,
    document_id    INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    base_version   INTEGER NOT NULL,
    saved_version  INTEGER,
    user           TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS download_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id     INTEGER NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    user           TEXT NOT NULL,
    format         TEXT NOT NULL DEFAULT 'pdf' CHECK (format IN ('pdf', 'xlsx')),
    downloaded_at  TEXT NOT NULL
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring databases created by earlier builds up to the current schema."""
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(download_log)")}
    if "format" not in columns:
        conn.execute(
            "ALTER TABLE download_log ADD COLUMN format TEXT NOT NULL DEFAULT 'pdf' "
            "CHECK (format IN ('pdf', 'xlsx'))"
        )
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(document_versions)")}
    if "edited_from" not in columns:
        conn.execute("ALTER TABLE document_versions ADD COLUMN edited_from INTEGER")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db(path: Path) -> None:
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


# --- documents --------------------------------------------------------------


def create_document(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    with conn:
        cur = conn.execute(
            "INSERT INTO documents (name, created_at) VALUES (?, ?)", (name, utcnow())
        )
    return get_document(conn, cur.lastrowid)  # type: ignore[return-value]


def get_document(conn: sqlite3.Connection, document_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    return dict(row) if row else None


def get_document_by_name(conn: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM documents WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def list_documents(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT d.*,
               (SELECT MAX(version) FROM document_versions v
                 WHERE v.document_id = d.id AND v.status = 'ready') AS latest_ready_version
          FROM documents d
         ORDER BY d.name
        """
    ).fetchall()
    docs = [dict(r) | {"versions": []} for r in rows]
    by_id = {d["id"]: d for d in docs}
    # One query for every document's versions, newest first, for the sidebar.
    for v in conn.execute(
        "SELECT document_id, version, status FROM document_versions ORDER BY document_id, version DESC"
    ):
        by_id[v["document_id"]]["versions"].append({"version": v["version"], "status": v["status"]})
    return docs


def delete_document(conn: sqlite3.Connection, document_id: int) -> None:
    """Delete a document; its versions and download log rows go with it (ON DELETE CASCADE)."""
    with conn:
        conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))


# --- versions ---------------------------------------------------------------


def _version(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    out["warnings"] = json.loads(out["warnings"])
    return out


def insert_version(
    conn: sqlite3.Connection,
    *,
    document_id: int,
    version: int,
    sha256: str,
    uploaded_by: str,
    warnings: list[dict[str, str]],
    edited_from: int | None = None,
) -> dict[str, Any]:
    """Insert a version row in 'processing' state.

    Raises sqlite3.IntegrityError if ``version`` is already taken for this document.
    """
    with conn:
        cur = conn.execute(
            """
            INSERT INTO document_versions
                (document_id, version, sha256, uploaded_by, uploaded_at, status, warnings, edited_from)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (document_id, version, sha256, uploaded_by, utcnow(), STATUS_PROCESSING,
             json.dumps(warnings), edited_from),
        )
    return get_version_by_id(conn, cur.lastrowid)  # type: ignore[return-value]


def max_version(conn: sqlite3.Connection, document_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM document_versions WHERE document_id = ?", (document_id,)
    ).fetchone()
    return int(row[0])


def update_version_content(
    conn: sqlite3.Connection, version_id: int, *, sha256: str, warnings: list[dict[str, str]]
) -> None:
    """A later save in the same edit session replaced this version's file."""
    with conn:
        conn.execute(
            "UPDATE document_versions SET sha256 = ?, warnings = ?, status = ?, error = NULL, "
            "uploaded_at = ? WHERE id = ?",
            (sha256, json.dumps(warnings), STATUS_PROCESSING, utcnow(), version_id),
        )


def delete_version(conn: sqlite3.Connection, version_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM document_versions WHERE id = ?", (version_id,))


def set_version_status(
    conn: sqlite3.Connection, version_id: int, status: str, error: str | None = None
) -> None:
    with conn:
        conn.execute(
            "UPDATE document_versions SET status = ?, error = ? WHERE id = ?",
            (status, error, version_id),
        )


def get_version_by_id(conn: sqlite3.Connection, version_id: int) -> dict[str, Any] | None:
    return _version(
        conn.execute("SELECT * FROM document_versions WHERE id = ?", (version_id,)).fetchone()
    )


def get_version(conn: sqlite3.Connection, document_id: int, version: int) -> dict[str, Any] | None:
    return _version(
        conn.execute(
            "SELECT * FROM document_versions WHERE document_id = ? AND version = ?",
            (document_id, version),
        ).fetchone()
    )


def latest_version(
    conn: sqlite3.Connection, document_id: int, *, statuses: tuple[str, ...]
) -> dict[str, Any] | None:
    placeholders = ",".join("?" for _ in statuses)
    return _version(
        conn.execute(
            f"""
            SELECT * FROM document_versions
             WHERE document_id = ? AND status IN ({placeholders})
             ORDER BY version DESC LIMIT 1
            """,
            (document_id, *statuses),
        ).fetchone()
    )


def list_versions(conn: sqlite3.Connection, document_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM document_versions WHERE document_id = ? ORDER BY version DESC",
        (document_id,),
    ).fetchall()
    return [_version(r) for r in rows]  # type: ignore[misc]


# --- download log -----------------------------------------------------------


def log_download(conn: sqlite3.Connection, *, version_id: int, user: str, format: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO download_log (version_id, user, format, downloaded_at) VALUES (?, ?, ?, ?)",
            (version_id, user, format, utcnow()),
        )


# --- edit sessions --------------------------------------------------------


def create_edit_session(
    conn: sqlite3.Connection, *, session_id: str, document_id: int, base_version: int, user: str
) -> dict[str, Any]:
    with conn:
        conn.execute(
            "INSERT INTO edit_sessions (id, document_id, base_version, user, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, document_id, base_version, user, utcnow()),
        )
    return get_edit_session(conn, session_id)  # type: ignore[return-value]


def get_edit_session(conn: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM edit_sessions WHERE id = ?", (session_id,)).fetchone()
    return dict(row) if row else None


def set_edit_session_saved_version(conn: sqlite3.Connection, session_id: str, version: int) -> None:
    with conn:
        conn.execute("UPDATE edit_sessions SET saved_version = ? WHERE id = ?", (version, session_id))
