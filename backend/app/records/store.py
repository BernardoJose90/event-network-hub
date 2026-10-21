"""Queries for event records (the tables are created by migrations/). All SQL for records lives here."""

from __future__ import annotations

import json
from typing import Any

from ..db import Connection, Row, utcnow


# --- venues, groups, lists -------------------------------------------------


def venues(conn: Connection, doc: int) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT id, name, sort FROM event_venues WHERE document_id = %s ORDER BY sort, id", (doc,))]


def groups(conn: Connection, doc: int, kind: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT id, kind, name, sort FROM event_groups WHERE document_id = %s"
    args: list[Any] = [doc]
    if kind:
        sql += " AND kind = %s"
        args.append(kind)
    return [dict(r) for r in conn.execute(sql + " ORDER BY kind, sort, id", args)]


def lists(conn: Connection, doc: int) -> list[dict[str, Any]]:
    return [dict(r) | {"flagged": bool(r["flagged"])} for r in conn.execute(
        "SELECT id, kind, value, sort, flagged FROM event_lists WHERE document_id = %s ORDER BY kind, sort, id",
        (doc,))]


def next_sort(conn: Connection, table: str, where: str, args: tuple) -> float:
    assert table in ("event_venues", "event_groups", "event_lists", "records")
    row = conn.execute(f"SELECT COALESCE(MAX(sort), 0) + 1 FROM {table} WHERE {where}", args).fetchone()
    return float(row[0])


def insert_venue(conn: Connection, doc: int, name: str) -> int:
    sort = next_sort(conn, "event_venues", "document_id = %s", (doc,))
    return conn.execute("INSERT INTO event_venues (document_id, name, sort) VALUES (%s, %s, %s) RETURNING id",
                        (doc, name, sort)).fetchone()[0]


def insert_group(conn: Connection, doc: int, kind: str, name: str) -> int:
    sort = next_sort(conn, "event_groups", "document_id = %s AND kind = %s", (doc, kind))
    return conn.execute("INSERT INTO event_groups (document_id, kind, name, sort) VALUES (%s, %s, %s, %s) RETURNING id",
                        (doc, kind, name, sort)).fetchone()[0]


def insert_list_value(conn: Connection, doc: int, kind: str, value: str, flagged: bool = False) -> int:
    sort = next_sort(conn, "event_lists", "document_id = %s AND kind = %s", (doc, kind))
    return conn.execute(
        "INSERT INTO event_lists (document_id, kind, value, sort, flagged) VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (doc, kind, value, sort, int(flagged))).fetchone()[0]


# --- records -----------------------------------------------------------------


def _record(row: Row) -> dict[str, Any]:
    out = dict(row)
    out["data"] = json.loads(out["data"])
    return out


def records(conn: Connection, doc: int, record_type: str | None = None, *,
            archived: bool | None = False) -> list[dict[str, Any]]:
    """Records in display order. archived: False = active only, True = archived only, None = all."""
    sql = """
        SELECT r.* FROM records r
          LEFT JOIN event_venues v ON v.id = r.venue_id
          LEFT JOIN event_groups g ON g.id = r.group_id
         WHERE r.document_id = %s"""
    args: list[Any] = [doc]
    if archived is not None:
        sql += " AND r.archived_at IS NOT NULL" if archived else " AND r.archived_at IS NULL"
    if record_type:
        sql += " AND r.type = %s"
        args.append(record_type)
    sql += " ORDER BY r.type, v.sort, g.sort, r.sort, r.id"
    return [_record(r) for r in conn.execute(sql, args)]


def get_record(conn: Connection, doc: int, record_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM records WHERE document_id = %s AND id = %s", (doc, record_id)).fetchone()
    return _record(row) if row else None


def insert_record(conn: Connection, *, doc: int, record_type: str, venue_id: int | None,
                  group_id: int | None, sort: float, data: dict[str, Any], user: str) -> int:
    return conn.execute(
        "INSERT INTO records (document_id, type, venue_id, group_id, sort, data, updated_by, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (doc, record_type, venue_id, group_id, sort, json.dumps(data), user, utcnow())).fetchone()[0]


def update_record(conn: Connection, *, record_id: int, expected_revision: int, user: str,
                  data: dict[str, Any] | None = None, group_id: int | None = None,
                  sort: float | None = None) -> bool:
    """Apply changes if nobody else changed the record since expected_revision."""
    sets, args = ["revision = revision + 1", "updated_by = %s", "updated_at = %s"], [user, utcnow()]
    if data is not None:
        sets.append("data = %s")
        args.append(json.dumps(data))
    if group_id is not None:
        sets.append("group_id = %s")
        args.append(group_id)
    if sort is not None:
        sets.append("sort = %s")
        args.append(sort)
    cur = conn.execute(f"UPDATE records SET {', '.join(sets)} WHERE id = %s AND revision = %s",
                       (*args, record_id, expected_revision))
    return cur.rowcount == 1


def delete_record(conn: Connection, record_id: int, expected_revision: int) -> bool:
    return conn.execute("DELETE FROM records WHERE id = %s AND revision = %s",
                        (record_id, expected_revision)).rowcount == 1


def has_records(conn: Connection, doc: int) -> bool:
    return conn.execute("SELECT 1 FROM records WHERE document_id = %s LIMIT 1", (doc,)).fetchone() is not None


def clear_event(conn: Connection, doc: int) -> None:
    """Remove all records, lists, venues and groups (before re-importing). History is kept."""
    for table in ("records", "event_lists", "event_groups", "event_venues"):
        conn.execute(f"DELETE FROM {table} WHERE document_id = %s", (doc,))


# --- change log, snapshots -----------------------------------------------------


def log(conn: Connection, *, doc: int, entity: str, action: str, summary: str, user: str,
        record_id: int | None = None, field: str | None = None, old: Any = None, new: Any = None) -> None:
    def text(v: Any) -> str | None:
        if v is None:
            return None
        return v if isinstance(v, str) else json.dumps(v)

    conn.execute(
        'INSERT INTO change_log (document_id, entity, record_id, action, field, old, new, summary, "user", at) '
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (doc, entity, record_id, action, field, text(old), text(new), summary[:1000], user, utcnow()))


def history(conn: Connection, doc: int, *, entity: str | None = None, user: str | None = None,
            after_id: int | None = None, limit: int = 500) -> list[dict[str, Any]]:
    sql, args = "SELECT * FROM change_log WHERE document_id = %s", [doc]
    if entity:
        sql += " AND entity = %s"
        args.append(entity)
    if user:
        sql += ' AND "user" = %s'
        args.append(user)
    if after_id is not None:
        sql += " AND id > %s"
        args.append(after_id)
    sql += " ORDER BY id DESC LIMIT %s"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args)]


def last_publish_log_id(conn: Connection, doc: int) -> int | None:
    row = conn.execute("SELECT MAX(id) FROM change_log WHERE document_id = %s AND action = 'publish'",
                       (doc,)).fetchone()
    return row[0]


def snapshot_for(conn: Connection, doc: int, version: int) -> dict[str, Any] | None:
    """The snapshot saved when ``version`` was published (None for uploaded/edited versions)."""
    row = conn.execute(
        "SELECT s.data FROM published_snapshots s JOIN document_versions v ON v.id = s.version_id "
        "WHERE s.document_id = %s AND v.version = %s", (doc, version)).fetchone()
    return json.loads(row[0]) if row else None


def previous_snapshot(conn: Connection, doc: int, before: int | None = None) -> tuple[int, dict] | None:
    """(version, snapshot) of the latest published version, or the latest one before ``before``."""
    sql = ("SELECT v.version, s.data FROM published_snapshots s JOIN document_versions v ON v.id = s.version_id "
           "WHERE s.document_id = %s AND v.status = 'ready'")
    args: list[Any] = [doc]
    if before is not None:
        sql += " AND v.version < %s"
        args.append(before)
    row = conn.execute(sql + " ORDER BY v.version DESC LIMIT 1", args).fetchone()
    return (row[0], json.loads(row[1])) if row else None


def published_versions(conn: Connection, doc: int) -> list[int]:
    return [r[0] for r in conn.execute(
        "SELECT v.version FROM published_snapshots s JOIN document_versions v ON v.id = s.version_id "
        "WHERE s.document_id = %s AND v.status = 'ready' ORDER BY v.version", (doc,))]


def save_snapshot(conn: Connection, *, version_id: int, doc: int, data: dict[str, Any]) -> None:
    conn.execute("INSERT INTO published_snapshots (version_id, document_id, data) VALUES (%s, %s, %s) "
                 "ON CONFLICT (version_id) DO UPDATE SET document_id = excluded.document_id, data = excluded.data",
                 (version_id, doc, json.dumps(data)))


def workbook_snapshot(conn: Connection, version_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT data FROM workbook_snapshots WHERE version_id = %s", (version_id,)).fetchone()
    return json.loads(row[0]) if row else None


def save_workbook_snapshot(conn: Connection, version_id: int, data: dict[str, Any]) -> None:
    with conn:
        conn.execute("INSERT INTO workbook_snapshots (version_id, data) VALUES (%s, %s) "
                     "ON CONFLICT (version_id) DO UPDATE SET data = excluded.data", (version_id, json.dumps(data)))
