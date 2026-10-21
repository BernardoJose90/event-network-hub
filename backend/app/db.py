"""PostgreSQL persistence: connections, the schema version check, and the document queries.

The schema is created and changed only by the Alembic migrations in migrations/ (run once per
release, before the new version starts); the app checks the version at start-up and never
creates tables itself.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg_pool import ConnectionPool

log = logging.getLogger(__name__)

STATUS_PROCESSING = "processing"
STATUS_READY = "ready"
STATUS_FAILED = "failed"

# The Alembic revision this code expects (tests check it is the newest one in migrations/).
# Revisions are numbered 0001, 0002, ... so the database being behind or ahead can be told apart.
SCHEMA_VERSION = "0001"

IntegrityError = psycopg.IntegrityError


class Row(tuple):
    """A result row, read like sqlite3.Row: by position, by column name, or with dict(row)."""

    def __new__(cls, values: Any, index: dict[str, int]) -> Row:
        row = super().__new__(cls, values)
        row._index = index
        return row

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, str):
            return tuple.__getitem__(self, self._index[key])
        return tuple.__getitem__(self, key)

    def keys(self) -> list[str]:
        return list(self._index)


def _row_factory(cursor: psycopg.Cursor) -> Any:
    index = {column.name: i for i, column in enumerate(cursor.description or ())}
    return lambda values: Row(values, index)


class Connection:
    """A pooled connection that keeps the sqlite3 habits the code is written in.

    ``with conn:`` commits on success and rolls back on error, without closing; nested blocks
    commit at the inner exit, as sqlite3 does. close() returns the connection to the pool.
    """

    def __init__(self, pool: ConnectionPool, raw: psycopg.Connection) -> None:
        self._pool = pool
        self._raw: psycopg.Connection | None = raw

    def execute(self, sql: str, params: Any = None) -> psycopg.Cursor:
        return self._raw.execute(sql, params)

    def executemany(self, sql: str, params_seq: Any) -> psycopg.Cursor:
        cur = self._raw.cursor()
        cur.executemany(sql, params_seq)
        return cur

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def __enter__(self) -> Connection:
        return self

    def __exit__(self, exc_type: Any, *_: Any) -> None:
        if exc_type is None:
            self._raw.commit()
        else:
            self._raw.rollback()

    def close(self) -> None:
        if self._raw is None:
            return
        raw, self._raw = self._raw, None
        if not raw.closed:
            raw.rollback()  # anything not committed is discarded, as sqlite3 does on close
        self._pool.putconn(raw)


_pools: dict[str, ConnectionPool] = {}
_pools_lock = threading.Lock()


def _pool(database_url: str) -> ConnectionPool:
    with _pools_lock:
        pool = _pools.get(database_url)
        if pool is None:
            pool = ConnectionPool(
                database_url, min_size=1, max_size=int(os.environ.get("DB_POOL_MAX_SIZE", "10")),
                kwargs={"row_factory": _row_factory, "application_name": "event-network-hub"},
                check=ConnectionPool.check_connection, name="db", open=True)
            _pools[database_url] = pool
        return pool


def connect(database_url: str, *, timeout: float = 30) -> Connection:
    """A connection from the pool for ``database_url``; close() gives it back."""
    pool = _pool(database_url)
    return Connection(pool, pool.getconn(timeout=timeout))


def close_pools() -> None:
    with _pools_lock:
        for pool in _pools.values():
            pool.close()
        _pools.clear()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- schema version ----------------------------------------------------------


class SchemaNotReady(RuntimeError):
    """The database hasn't been migrated to the schema this release needs."""


def schema_version(conn: Connection) -> str | None:
    """The Alembic revision the database is at (None if it was never migrated)."""
    try:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        return None
    conn.rollback()  # leave no read transaction open
    return row[0] if row else None


def check_schema(conn: Connection) -> str:
    """Raise SchemaNotReady unless the database is at SCHEMA_VERSION or newer; returns its version.

    Newer is accepted (migrations are written to be backwards compatible), so pods of the
    previous release keep running while a release rolls out, and a rollback can redeploy them.
    """
    current = schema_version(conn)
    if current is None:
        raise SchemaNotReady("The database has no schema yet. Run the migrations: alembic upgrade head")
    if int(current) < int(SCHEMA_VERSION):
        raise SchemaNotReady(f"The database schema is at {current}, but this release needs {SCHEMA_VERSION}. "
                             "Run the migrations: alembic upgrade head")
    if current != SCHEMA_VERSION:
        log.warning("database schema %s is newer than this release's %s", current, SCHEMA_VERSION)
    return current


# --- documents --------------------------------------------------------------


def create_document(conn: Connection, name: str) -> dict[str, Any]:
    with conn:
        document_id = insert_document(conn, name)
    return get_document(conn, document_id)  # type: ignore[return-value]


def insert_document(conn: Connection, name: str, kind: str = "event") -> int:
    """Insert a document without committing, for callers that add more in the same transaction.

    kind 'template': an event template, listed only under "Start from", never published.
    """
    return conn.execute(
        "INSERT INTO documents (name, created_at, kind) VALUES (%s, %s, %s) RETURNING id", (name, utcnow(), kind)
    ).fetchone()[0]


def get_document(conn: Connection, document_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM documents WHERE id = %s", (document_id,)).fetchone()
    return dict(row) if row else None


def get_document_by_name(conn: Connection, name: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM documents WHERE name = %s", (name,)).fetchone()
    return dict(row) if row else None


def list_documents(conn: Connection, kind: str = "event") -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT d.*,
               (SELECT MAX(version) FROM document_versions v
                 WHERE v.document_id = d.id AND v.status = 'ready') AS latest_ready_version
          FROM documents d
         WHERE d.kind = %s
         ORDER BY d.name
        """,
        (kind,),
    ).fetchall()
    docs = [dict(r) | {"versions": []} for r in rows]
    by_id = {d["id"]: d for d in docs}
    # One query for every document's versions, newest first, for the sidebar.
    for v in conn.execute(
        "SELECT document_id, version, status FROM document_versions ORDER BY document_id, version DESC"
    ):
        if v["document_id"] in by_id:
            by_id[v["document_id"]]["versions"].append({"version": v["version"], "status": v["status"]})
    return docs


class EventArchived(Exception):
    """The event is archived, so it can't be changed. ``str(exc)`` is user-facing."""

    def __init__(self, name: str) -> None:
        super().__init__(f"'{name}' is archived and read-only. Restore it to make changes.")


def ensure_not_archived(conn: Connection, document_id: int) -> None:
    """Raise EventArchived if the document is archived (missing documents are the caller's concern)."""
    row = conn.execute("SELECT name, archived_at FROM documents WHERE id = %s", (document_id,)).fetchone()
    if row is not None and row["archived_at"]:
        raise EventArchived(row["name"])


def set_document_archived(conn: Connection, document_id: int, *, user: str | None) -> None:
    """Archive (user given) or restore (user None) a document."""
    with conn:
        conn.execute(
            "UPDATE documents SET archived_at = %s, archived_by = %s WHERE id = %s",
            (utcnow() if user else None, user, document_id),
        )


def delete_document(conn: Connection, document_id: int) -> None:
    """Delete a document; its versions and download log rows go with it (ON DELETE CASCADE)."""
    with conn:
        conn.execute("DELETE FROM documents WHERE id = %s", (document_id,))


# --- versions ---------------------------------------------------------------


def _version(row: Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def insert_version(
    conn: Connection,
    *,
    document_id: int,
    version: int,
    sha256: str,
    uploaded_by: str,
    edited_from: int | None = None,
    source: str = "upload",
) -> dict[str, Any]:
    """Insert a version row in 'processing' state.

    Raises IntegrityError if ``version`` is already taken for this document.
    """
    with conn:
        cur = conn.execute(
            """
            INSERT INTO document_versions
                (document_id, version, sha256, uploaded_by, uploaded_at, status, edited_from, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (document_id, version, sha256, uploaded_by, utcnow(), STATUS_PROCESSING, edited_from, source),
        )
        version_id = cur.fetchone()[0]
    return get_version_by_id(conn, version_id)  # type: ignore[return-value]


def max_version(conn: Connection, document_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM document_versions WHERE document_id = %s", (document_id,)
    ).fetchone()
    return int(row[0])


def delete_version(conn: Connection, version_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM document_versions WHERE id = %s", (version_id,))


def set_version_status(
    conn: Connection, version_id: int, status: str, error: str | None = None
) -> None:
    with conn:
        conn.execute(
            "UPDATE document_versions SET status = %s, error = %s WHERE id = %s",
            (status, error, version_id),
        )


def get_version_by_id(conn: Connection, version_id: int) -> dict[str, Any] | None:
    return _version(
        conn.execute("SELECT * FROM document_versions WHERE id = %s", (version_id,)).fetchone()
    )


def get_version(conn: Connection, document_id: int, version: int) -> dict[str, Any] | None:
    return _version(
        conn.execute(
            "SELECT * FROM document_versions WHERE document_id = %s AND version = %s",
            (document_id, version),
        ).fetchone()
    )


def latest_version(
    conn: Connection, document_id: int, *, statuses: tuple[str, ...]
) -> dict[str, Any] | None:
    placeholders = ",".join("%s" for _ in statuses)
    return _version(
        conn.execute(
            f"""
            SELECT * FROM document_versions
             WHERE document_id = %s AND status IN ({placeholders})
             ORDER BY version DESC LIMIT 1
            """,
            (document_id, *statuses),
        ).fetchone()
    )


def list_versions(conn: Connection, document_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM document_versions WHERE document_id = %s ORDER BY version DESC",
        (document_id,),
    ).fetchall()
    return [_version(r) for r in rows]  # type: ignore[misc]


# --- download log -----------------------------------------------------------


def log_download(conn: Connection, *, version_id: int, user: str, format: str) -> None:
    with conn:
        conn.execute(
            'INSERT INTO download_log (version_id, "user", format, downloaded_at) VALUES (%s, %s, %s, %s)',
            (version_id, user, format, utcnow()),
        )

