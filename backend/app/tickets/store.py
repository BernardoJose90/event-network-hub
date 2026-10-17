"""SQLite tables and queries for the help desk. All help desk SQL lives here.

Tickets are a permanent record: they are never deleted, and they survive the
deletion of their event (event_id becomes NULL; event_code is kept).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..db import utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id         INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    event_code       TEXT,
    title            TEXT NOT NULL,
    description      TEXT,
    category         TEXT NOT NULL CHECK (category IN
                       ('wifi', 'wired', 'hardware', 'partner_stand', 'venue', 'uncategorised')),
    severity         TEXT NOT NULL CHECK (severity IN ('P1', 'P2', 'P3', 'P4')),
    status           TEXT NOT NULL CHECK (status IN
                       ('new', 'acknowledged', 'in_progress', 'waiting', 'resolved', 'closed')),
    team             TEXT NOT NULL CHECK (team IN ('network', 'it_support')),
    assignee         TEXT,
    location         TEXT,
    stand            TEXT,
    partner          TEXT,
    reporter_name    TEXT,
    reporter_phone   TEXT,
    source           TEXT NOT NULL CHECK (source IN ('whatsapp', 'manual')),
    external_id      TEXT UNIQUE,  -- the bot's report id: makes bot retries idempotent
    merged_into      INTEGER REFERENCES tickets(id),
    root_cause       TEXT,
    resolution       TEXT,
    created_by       TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    acknowledged_at  TEXT,
    acknowledged_by  TEXT,
    resolved_at      TEXT,
    closed_at        TEXT,
    updated_at       TEXT NOT NULL,
    revision         INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS tickets_by_event ON tickets (event_id, status);
CREATE INDEX IF NOT EXISTS tickets_by_status ON tickets (status, severity);

-- The audit trail: every change and comment. visibility 'reporter' means it was
-- also sent to the reporter through the bot.
CREATE TABLE IF NOT EXISTS ticket_activity (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,  -- created, field, assign, status, acknowledge, comment, merge
    field       TEXT,
    old         TEXT,
    new         TEXT,
    body        TEXT,
    visibility  TEXT NOT NULL DEFAULT 'internal' CHECK (visibility IN ('internal', 'reporter')),
    user        TEXT NOT NULL,
    at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ticket_activity_by_ticket ON ticket_activity (ticket_id, id);

-- Updates for the WhatsApp bot, delivered (and retried) by the outbox worker.
CREATE TABLE IF NOT EXISTS ticket_outbox (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    payload     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed')),
    attempts    INTEGER NOT NULL DEFAULT 0,
    next_try_at TEXT NOT NULL,
    last_error  TEXT,
    created_at  TEXT NOT NULL,
    sent_at     TEXT
);
CREATE INDEX IF NOT EXISTS ticket_outbox_pending ON ticket_outbox (status, next_try_at);

CREATE TABLE IF NOT EXISTS helpdesk_settings (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
"""

COLUMNS = ("event_id", "event_code", "title", "description", "category", "severity", "status", "team", "assignee",
           "location", "stand", "partner", "reporter_name", "reporter_phone", "source", "external_id", "created_by")


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


# --- tickets -----------------------------------------------------------------------


def insert_ticket(conn: sqlite3.Connection, values: dict[str, Any]) -> int:
    now = utcnow()
    cols = [c for c in COLUMNS if c in values]
    sql = (f"INSERT INTO tickets ({', '.join(cols)}, created_at, updated_at) "
           f"VALUES ({', '.join('?' for _ in cols)}, ?, ?)")
    return conn.execute(sql, [values[c] for c in cols] + [now, now]).lastrowid


def get_ticket(conn: sqlite3.Connection, ticket_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return dict(row) if row else None


def get_by_external_id(conn: sqlite3.Connection, external_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM tickets WHERE external_id = ?", (external_id,)).fetchone()
    return dict(row) if row else None


def update_ticket(conn: sqlite3.Connection, ticket_id: int, expected_revision: int, values: dict[str, Any]) -> bool:
    """Apply values if nobody changed the ticket since expected_revision."""
    sets = ["revision = revision + 1", "updated_at = ?"] + [f"{k} = ?" for k in values]
    cur = conn.execute(f"UPDATE tickets SET {', '.join(sets)} WHERE id = ? AND revision = ?",
                       [utcnow(), *values.values(), ticket_id, expected_revision])
    return cur.rowcount == 1


def touch(conn: sqlite3.Connection, ticket_id: int) -> None:
    """Bump the revision and updated time (comments, merges into this ticket)."""
    conn.execute("UPDATE tickets SET revision = revision + 1, updated_at = ? WHERE id = ?", (utcnow(), ticket_id))


def list_tickets(conn: sqlite3.Connection, *, event_id: int | None = None, statuses: tuple[str, ...] | None = None,
                 severity: str | None = None, team: str | None = None, category: str | None = None,
                 assignee: str | None = None, unassigned: bool = False, partner: str | None = None,
                 location: str | None = None, query: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
    sql, args = "SELECT * FROM tickets WHERE 1 = 1", []
    for column, value in (("event_id", event_id), ("severity", severity), ("team", team), ("category", category),
                          ("assignee", assignee)):
        if value is not None:
            sql += f" AND {column} = ?"
            args.append(value)
    if statuses:
        sql += f" AND status IN ({', '.join('?' for _ in statuses)})"
        args += list(statuses)
    if unassigned:
        sql += " AND assignee IS NULL"
    for column, value in (("partner", partner), ("location", location)):
        if value:
            sql += f" AND {column} LIKE ? ESCAPE '\\'"
            args.append(f"%{_like(value)}%")
    if query:
        like = f"%{_like(query)}%"
        sql += (" AND (title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' OR location LIKE ? ESCAPE '\\'"
                " OR stand LIKE ? ESCAPE '\\' OR partner LIKE ? ESCAPE '\\' OR reporter_name LIKE ? ESCAPE '\\'"
                " OR CAST(id AS TEXT) = ?)")
        args += [like] * 6 + [query.lstrip("#")]
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args)]


def _like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def merged_children(conn: sqlite3.Connection, ticket_id: int) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM tickets WHERE merged_into = ? ORDER BY id", (ticket_id,))]


def assignees(conn: sqlite3.Connection) -> list[str]:
    """Everyone who has owned or acknowledged a ticket, for the owner picker."""
    rows = conn.execute("SELECT assignee AS name FROM tickets WHERE assignee IS NOT NULL "
                        "UNION SELECT acknowledged_by FROM tickets WHERE acknowledged_by IS NOT NULL ORDER BY 1")
    return [r[0] for r in rows]


# --- activity --------------------------------------------------------------------------


def log(conn: sqlite3.Connection, ticket_id: int, kind: str, user: str, *, field: str | None = None,
        old: Any = None, new: Any = None, body: str | None = None, visibility: str = "internal") -> None:
    def text(v: Any) -> str | None:
        return None if v is None else v if isinstance(v, str) else json.dumps(v)

    conn.execute(
        "INSERT INTO ticket_activity (ticket_id, kind, field, old, new, body, visibility, user, at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ticket_id, kind, field, text(old), text(new), body, visibility, user, utcnow()))


def activity(conn: sqlite3.Connection, ticket_id: int) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM ticket_activity WHERE ticket_id = ? ORDER BY id", (ticket_id,))]


# --- outbox -------------------------------------------------------------------------------


def enqueue(conn: sqlite3.Connection, ticket_id: int, payload: dict[str, Any]) -> None:
    now = utcnow()
    conn.execute("INSERT INTO ticket_outbox (ticket_id, payload, next_try_at, created_at) VALUES (?, ?, ?, ?)",
                 (ticket_id, json.dumps(payload), now, now))


def due_outbox(conn: sqlite3.Connection, now: str, limit: int = 20) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM ticket_outbox WHERE status = 'pending' AND next_try_at <= ? ORDER BY id LIMIT ?",
        (now, limit))]


def mark_outbox(conn: sqlite3.Connection, outbox_id: int, *, status: str, attempts: int,
                next_try_at: str | None = None, error: str | None = None) -> None:
    with conn:
        conn.execute(
            "UPDATE ticket_outbox SET status = ?, attempts = ?, next_try_at = COALESCE(?, next_try_at), "
            "last_error = ?, sent_at = CASE WHEN ? = 'sent' THEN ? ELSE sent_at END WHERE id = ?",
            (status, attempts, next_try_at, error, status, utcnow(), outbox_id))


def outbox_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM ticket_outbox GROUP BY status")}


# --- settings ----------------------------------------------------------------------------


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM helpdesk_settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    with conn:
        conn.execute("INSERT INTO helpdesk_settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
