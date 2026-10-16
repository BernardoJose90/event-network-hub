"""SQLite tables and queries for event records. All SQL for records lives here."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..db import utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS event_venues (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    sort         REAL NOT NULL,
    UNIQUE (document_id, name)
);

-- Wi-Fi groups (Core Networks, Lounges, ...) and Ethernet sections (INTERNAL WORKSPACES, ...).
CREATE TABLE IF NOT EXISTS event_groups (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL CHECK (kind IN ('wifi', 'ethernet')),
    name         TEXT NOT NULL,
    sort         REAL NOT NULL,
    UNIQUE (document_id, kind, name)
);

-- Managed lists (the old Setup sheet). flagged = added by an import, to review.
CREATE TABLE IF NOT EXISTS event_lists (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL CHECK (kind IN ('building', 'location', 'vlan', 'bandwidth')),
    value        TEXT NOT NULL,
    sort         REAL NOT NULL,
    flagged      INTEGER NOT NULL DEFAULT 0,
    UNIQUE (document_id, kind, value)
);

CREATE TABLE IF NOT EXISTS records (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    type         TEXT NOT NULL CHECK (type IN ('wifi', 'ethernet', 'partner', 'vlan_extension')),
    venue_id     INTEGER REFERENCES event_venues(id) ON DELETE CASCADE,
    group_id     INTEGER REFERENCES event_groups(id) ON DELETE CASCADE,
    sort         REAL NOT NULL,
    data         TEXT NOT NULL,
    revision     INTEGER NOT NULL DEFAULT 1,
    updated_by   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS records_by_type ON records (document_id, type, venue_id, group_id, sort);

-- Every change, written automatically. action: create, update, delete, move,
-- list, venue, group, import, publish, reveal, legacy (imported old log).
CREATE TABLE IF NOT EXISTS change_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    entity       TEXT NOT NULL,
    record_id    INTEGER,
    action       TEXT NOT NULL,
    field        TEXT,
    old          TEXT,
    new          TEXT,
    summary      TEXT NOT NULL,
    user         TEXT NOT NULL,
    at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS change_log_by_doc ON change_log (document_id, id);

CREATE TABLE IF NOT EXISTS published_snapshots (
    version_id   INTEGER PRIMARY KEY REFERENCES document_versions(id) ON DELETE CASCADE,
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    data         TEXT NOT NULL
);
"""


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


# --- venues, groups, lists -------------------------------------------------


def venues(conn: sqlite3.Connection, doc: int) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT id, name, sort FROM event_venues WHERE document_id = ? ORDER BY sort, id", (doc,))]


def groups(conn: sqlite3.Connection, doc: int, kind: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT id, kind, name, sort FROM event_groups WHERE document_id = ?"
    args: list[Any] = [doc]
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    return [dict(r) for r in conn.execute(sql + " ORDER BY kind, sort, id", args)]


def lists(conn: sqlite3.Connection, doc: int) -> list[dict[str, Any]]:
    return [dict(r) | {"flagged": bool(r["flagged"])} for r in conn.execute(
        "SELECT id, kind, value, sort, flagged FROM event_lists WHERE document_id = ? ORDER BY kind, sort, id",
        (doc,))]


def next_sort(conn: sqlite3.Connection, table: str, where: str, args: tuple) -> float:
    assert table in ("event_venues", "event_groups", "event_lists", "records")
    row = conn.execute(f"SELECT COALESCE(MAX(sort), 0) + 1 FROM {table} WHERE {where}", args).fetchone()
    return float(row[0])


def insert_venue(conn: sqlite3.Connection, doc: int, name: str) -> int:
    sort = next_sort(conn, "event_venues", "document_id = ?", (doc,))
    return conn.execute("INSERT INTO event_venues (document_id, name, sort) VALUES (?, ?, ?)",
                        (doc, name, sort)).lastrowid


def insert_group(conn: sqlite3.Connection, doc: int, kind: str, name: str) -> int:
    sort = next_sort(conn, "event_groups", "document_id = ? AND kind = ?", (doc, kind))
    return conn.execute("INSERT INTO event_groups (document_id, kind, name, sort) VALUES (?, ?, ?, ?)",
                        (doc, kind, name, sort)).lastrowid


def insert_list_value(conn: sqlite3.Connection, doc: int, kind: str, value: str, flagged: bool = False) -> int:
    sort = next_sort(conn, "event_lists", "document_id = ? AND kind = ?", (doc, kind))
    return conn.execute(
        "INSERT INTO event_lists (document_id, kind, value, sort, flagged) VALUES (?, ?, ?, ?, ?)",
        (doc, kind, value, sort, int(flagged))).lastrowid


# --- records -----------------------------------------------------------------


def _record(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["data"] = json.loads(out["data"])
    return out


def records(conn: sqlite3.Connection, doc: int, record_type: str | None = None) -> list[dict[str, Any]]:
    sql = """
        SELECT r.* FROM records r
          LEFT JOIN event_venues v ON v.id = r.venue_id
          LEFT JOIN event_groups g ON g.id = r.group_id
         WHERE r.document_id = ?"""
    args: list[Any] = [doc]
    if record_type:
        sql += " AND r.type = ?"
        args.append(record_type)
    sql += " ORDER BY r.type, v.sort, g.sort, r.sort, r.id"
    return [_record(r) for r in conn.execute(sql, args)]


def get_record(conn: sqlite3.Connection, doc: int, record_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM records WHERE document_id = ? AND id = ?", (doc, record_id)).fetchone()
    return _record(row) if row else None


def insert_record(conn: sqlite3.Connection, *, doc: int, record_type: str, venue_id: int | None,
                  group_id: int | None, sort: float, data: dict[str, Any], user: str) -> int:
    return conn.execute(
        "INSERT INTO records (document_id, type, venue_id, group_id, sort, data, updated_by, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (doc, record_type, venue_id, group_id, sort, json.dumps(data), user, utcnow())).lastrowid


def update_record(conn: sqlite3.Connection, *, record_id: int, expected_revision: int, user: str,
                  data: dict[str, Any] | None = None, group_id: int | None = None,
                  sort: float | None = None) -> bool:
    """Apply changes if nobody else changed the record since expected_revision."""
    sets, args = ["revision = revision + 1", "updated_by = ?", "updated_at = ?"], [user, utcnow()]
    if data is not None:
        sets.append("data = ?")
        args.append(json.dumps(data))
    if group_id is not None:
        sets.append("group_id = ?")
        args.append(group_id)
    if sort is not None:
        sets.append("sort = ?")
        args.append(sort)
    cur = conn.execute(f"UPDATE records SET {', '.join(sets)} WHERE id = ? AND revision = ?",
                       (*args, record_id, expected_revision))
    return cur.rowcount == 1


def delete_record(conn: sqlite3.Connection, record_id: int, expected_revision: int) -> bool:
    return conn.execute("DELETE FROM records WHERE id = ? AND revision = ?",
                        (record_id, expected_revision)).rowcount == 1


def has_records(conn: sqlite3.Connection, doc: int) -> bool:
    return conn.execute("SELECT 1 FROM records WHERE document_id = ? LIMIT 1", (doc,)).fetchone() is not None


def clear_event(conn: sqlite3.Connection, doc: int) -> None:
    """Remove all records, lists, venues and groups (before re-importing). History is kept."""
    for table in ("records", "event_lists", "event_groups", "event_venues"):
        conn.execute(f"DELETE FROM {table} WHERE document_id = ?", (doc,))


# --- change log, snapshots -----------------------------------------------------


def log(conn: sqlite3.Connection, *, doc: int, entity: str, action: str, summary: str, user: str,
        record_id: int | None = None, field: str | None = None, old: Any = None, new: Any = None) -> None:
    def text(v: Any) -> str | None:
        if v is None:
            return None
        return v if isinstance(v, str) else json.dumps(v)

    conn.execute(
        "INSERT INTO change_log (document_id, entity, record_id, action, field, old, new, summary, user, at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (doc, entity, record_id, action, field, text(old), text(new), summary[:1000], user, utcnow()))


def history(conn: sqlite3.Connection, doc: int, *, entity: str | None = None, user: str | None = None,
            after_id: int | None = None, limit: int = 500) -> list[dict[str, Any]]:
    sql, args = "SELECT * FROM change_log WHERE document_id = ?", [doc]
    if entity:
        sql += " AND entity = ?"
        args.append(entity)
    if user:
        sql += " AND user = ?"
        args.append(user)
    if after_id is not None:
        sql += " AND id > ?"
        args.append(after_id)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args)]


def last_publish_log_id(conn: sqlite3.Connection, doc: int) -> int | None:
    row = conn.execute("SELECT MAX(id) FROM change_log WHERE document_id = ? AND action = 'publish'",
                       (doc,)).fetchone()
    return row[0]


def save_snapshot(conn: sqlite3.Connection, *, version_id: int, doc: int, data: dict[str, Any]) -> None:
    conn.execute("INSERT OR REPLACE INTO published_snapshots (version_id, document_id, data) VALUES (?, ?, ?)",
                 (version_id, doc, json.dumps(data)))
