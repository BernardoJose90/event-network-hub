"""Queries for the help desk (the tables are created by migrations/). All help desk SQL lives here.

Tickets are a permanent record: they are never deleted, and they survive the
deletion of their event (event_id becomes NULL; event_code is kept).
"""

from __future__ import annotations

import json
from typing import Any

from ..db import Connection, utcnow

COLUMNS = ("event_id", "event_code", "title", "description", "category", "severity", "status", "team", "assignee",
           "location", "stand", "partner", "reporter_name", "reporter_phone", "source", "external_id", "created_by")


# --- tickets -----------------------------------------------------------------------


def insert_ticket(conn: Connection, values: dict[str, Any]) -> int:
    now = utcnow()
    cols = [c for c in COLUMNS if c in values]
    sql = (f"INSERT INTO tickets ({', '.join(cols)}, created_at, updated_at) "
           f"VALUES ({', '.join('%s' for _ in cols)}, %s, %s) RETURNING id")
    return conn.execute(sql, [values[c] for c in cols] + [now, now]).fetchone()[0]


def get_ticket(conn: Connection, ticket_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM tickets WHERE id = %s", (ticket_id,)).fetchone()
    return dict(row) if row else None


def get_by_external_id(conn: Connection, external_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM tickets WHERE external_id = %s", (external_id,)).fetchone()
    return dict(row) if row else None


def update_ticket(conn: Connection, ticket_id: int, expected_revision: int, values: dict[str, Any]) -> bool:
    """Apply values if nobody changed the ticket since expected_revision."""
    sets = ["revision = revision + 1", "updated_at = %s"] + [f"{k} = %s" for k in values]
    cur = conn.execute(f"UPDATE tickets SET {', '.join(sets)} WHERE id = %s AND revision = %s",
                       [utcnow(), *values.values(), ticket_id, expected_revision])
    return cur.rowcount == 1


def touch(conn: Connection, ticket_id: int) -> None:
    """Bump the revision and updated time (comments, merges into this ticket)."""
    conn.execute("UPDATE tickets SET revision = revision + 1, updated_at = %s WHERE id = %s", (utcnow(), ticket_id))


def list_tickets(conn: Connection, *, event_id: int | None = None, statuses: tuple[str, ...] | None = None,
                 severity: str | None = None, team: str | None = None, category: str | None = None,
                 assignee: str | None = None, unassigned: bool = False, partner: str | None = None,
                 location: str | None = None, query: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
    sql, args = "SELECT * FROM tickets WHERE 1 = 1", []
    for column, value in (("event_id", event_id), ("severity", severity), ("team", team), ("category", category),
                          ("assignee", assignee)):
        if value is not None:
            sql += f" AND {column} = %s"
            args.append(value)
    if statuses:
        sql += f" AND status IN ({', '.join('%s' for _ in statuses)})"
        args += list(statuses)
    if unassigned:
        sql += " AND assignee IS NULL"
    for column, value in (("partner", partner), ("location", location)):
        if value:
            sql += f" AND {column} ILIKE %s ESCAPE '\\'"
            args.append(f"%{_like(value)}%")
    if query:
        like = f"%{_like(query)}%"
        sql += (" AND (title ILIKE %s ESCAPE '\\' OR description ILIKE %s ESCAPE '\\' OR location ILIKE %s ESCAPE '\\'"
                " OR stand ILIKE %s ESCAPE '\\' OR partner ILIKE %s ESCAPE '\\' OR reporter_name ILIKE %s ESCAPE '\\'"
                " OR CAST(id AS TEXT) = %s)")
        args += [like] * 6 + [query.lstrip("#")]
    sql += " ORDER BY id DESC LIMIT %s"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args)]


def _like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def merged_children(conn: Connection, ticket_id: int) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM tickets WHERE merged_into = %s ORDER BY id", (ticket_id,))]


def assignees(conn: Connection) -> list[str]:
    """Everyone who has owned or acknowledged a ticket, for the owner picker."""
    rows = conn.execute("SELECT assignee AS name FROM tickets WHERE assignee IS NOT NULL "
                        "UNION SELECT acknowledged_by FROM tickets WHERE acknowledged_by IS NOT NULL ORDER BY 1")
    return [r[0] for r in rows]


# --- activity --------------------------------------------------------------------------


def log(conn: Connection, ticket_id: int, kind: str, user: str, *, field: str | None = None,
        old: Any = None, new: Any = None, body: str | None = None, visibility: str = "internal") -> None:
    def text(v: Any) -> str | None:
        return None if v is None else v if isinstance(v, str) else json.dumps(v)

    conn.execute(
        'INSERT INTO ticket_activity (ticket_id, kind, field, old, new, body, visibility, "user", at) '
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (ticket_id, kind, field, text(old), text(new), body, visibility, user, utcnow()))


def activity(conn: Connection, ticket_id: int) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM ticket_activity WHERE ticket_id = %s ORDER BY id", (ticket_id,))]


# --- outbox -------------------------------------------------------------------------------


def enqueue(conn: Connection, ticket_id: int, payload: dict[str, Any]) -> None:
    now = utcnow()
    conn.execute("INSERT INTO ticket_outbox (ticket_id, payload, next_try_at, created_at) VALUES (%s, %s, %s, %s)",
                 (ticket_id, json.dumps(payload), now, now))


def due_outbox(conn: Connection, now: str, limit: int = 20) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM ticket_outbox WHERE status = 'pending' AND next_try_at <= %s ORDER BY id LIMIT %s",
        (now, limit))]


def mark_outbox(conn: Connection, outbox_id: int, *, status: str, attempts: int,
                next_try_at: str | None = None, error: str | None = None) -> None:
    with conn:
        conn.execute(
            "UPDATE ticket_outbox SET status = %s, attempts = %s, next_try_at = COALESCE(%s, next_try_at), "
            "last_error = %s, sent_at = CASE WHEN %s::text = 'sent' THEN %s ELSE sent_at END WHERE id = %s",
            (status, attempts, next_try_at, error, status, utcnow(), outbox_id))


def outbox_counts(conn: Connection) -> dict[str, int]:
    return {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM ticket_outbox GROUP BY status")}


# --- settings ----------------------------------------------------------------------------


def get_setting(conn: Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM helpdesk_settings WHERE key = %s", (key,)).fetchone()
    return row[0] if row else None


def set_setting(conn: Connection, key: str, value: str | None) -> None:
    with conn:
        conn.execute("INSERT INTO helpdesk_settings (key, value) VALUES (%s, %s) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
