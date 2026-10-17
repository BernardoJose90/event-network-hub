"""Notifications: one record per event worth telling someone about, delivered to the least
disruptive channel that still works.

- In-app (always): the notification centre in the top bar; the permanent record (NN/g: pushed
  alerts are easily dismissed, so anything needed later must stay somewhere).
- Browser push: urgent things only by default (new P1/P2 ticket, SLA warnings, assigned to you);
  each person chooses types in their preferences.
- Slack (incoming webhook): urgent help desk alerts to the ICT channel.
- Email (the organisation's SMTP provider): people without an app account (ICT requesters,
  team contacts, people following an event's published versions) and SLA escalations.

Audience: 'admins' (network admin role = the ICT team), 'all', 'user:<name>', or 'external'
(email only). Deliveries are queued per channel and sent by the worker (worker.py) with retries.
Repeats are suppressed with a dedup key (one SLA warning per ticket, one reminder per deadline).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from ..db import utcnow

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL,
    severity    TEXT NOT NULL CHECK (severity IN ('urgent', 'normal', 'low')),
    title       TEXT NOT NULL,
    body        TEXT,
    link        TEXT,
    audience    TEXT NOT NULL,
    event_id    INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    dedup_key   TEXT UNIQUE,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS notifications_by_audience ON notifications (audience, id);

CREATE TABLE IF NOT EXISTS notification_reads (
    notification_id  INTEGER NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    user             TEXT NOT NULL,
    read_at          TEXT NOT NULL,
    PRIMARY KEY (notification_id, user)
);

CREATE TABLE IF NOT EXISTS notification_prefs (
    user        TEXT PRIMARY KEY,
    push_types  TEXT NOT NULL  -- JSON list of PUSH_TYPES keys
);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user        TEXT NOT NULL,
    role        TEXT NOT NULL,
    endpoint    TEXT NOT NULL UNIQUE,
    p256dh      TEXT NOT NULL,
    auth        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_deliveries (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_id  INTEGER NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    channel          TEXT NOT NULL CHECK (channel IN ('push', 'slack', 'email')),
    target           TEXT NOT NULL,  -- subscription id, 'channel', or an email address
    status           TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed')),
    attempts         INTEGER NOT NULL DEFAULT 0,
    next_try_at      TEXT NOT NULL,
    last_error       TEXT,
    created_at       TEXT NOT NULL,
    sent_at          TEXT
);
CREATE INDEX IF NOT EXISTS notification_deliveries_due ON notification_deliveries (status, next_try_at);

-- People emailed when a version of the event is published (contractors, venue, ICT).
CREATE TABLE IF NOT EXISTS event_watchers (
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    email        TEXT NOT NULL,
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (document_id, email)
);
"""

# Browser push types a person can choose (in-app always shows everything for them).
PUSH_TYPES = {
    "ticket_urgent": "New P1 or P2 help desk tickets",
    "ticket_all": "Every new help desk ticket",
    "sla": "Help desk SLA warnings and breaches",
    "assigned": "Tickets assigned to me",
    "requests": "New ICT requests",
}
DEFAULT_PUSH_TYPES = ["ticket_urgent", "sla", "assigned"]

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class Channels:
    """Which outbound channels are configured (set from Settings at start-up)."""

    push: bool = False
    slack: bool = False
    email: bool = False
    escalation_emails: tuple[str, ...] = ()
    public_url: str = "http://localhost:5173"
    extra: dict[str, Any] = field(default_factory=dict)


CHANNELS = Channels()


def configure(channels: Channels) -> None:
    global CHANNELS
    CHANNELS = channels


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(request_links)")}
    if columns and "contact_email" not in columns:  # request link contacts get deadline reminders
        conn.execute("ALTER TABLE request_links ADD COLUMN contact_email TEXT")


def is_email(value: str | None) -> bool:
    return bool(value) and bool(EMAIL_RE.match(value.strip()))


def absolute(link: str | None) -> str | None:
    if not link:
        return None
    return link if link.startswith("http") else f"{CHANNELS.public_url}{link}"


# --- create ----------------------------------------------------------------------------------


def notify(conn: sqlite3.Connection, *, type: str, title: str, body: str | None = None, link: str | None = None,
           severity: str = "normal", audience: str = "admins", event_id: int | None = None,
           dedup_key: str | None = None, push_type: str | None = None, slack: bool = False,
           emails: list[str] | tuple[str, ...] = ()) -> int | None:
    """Record a notification and queue its deliveries (inside the caller's transaction or its own).

    push_type: the preference key that lets this be pushed to browsers (None = never pushed).
    Returns the notification id, or None if dedup_key was already used.
    """
    now = utcnow()
    try:
        notification_id = conn.execute(
            "INSERT INTO notifications (type, severity, title, body, link, audience, event_id, dedup_key, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (type, severity, title[:300], (body or "")[:2000] or None, link, audience, event_id, dedup_key, now)).lastrowid
    except sqlite3.IntegrityError:
        return None  # already notified (dedup)

    def queue(channel: str, target: str) -> None:
        conn.execute("INSERT INTO notification_deliveries (notification_id, channel, target, next_try_at, created_at) "
                     "VALUES (?, ?, ?, ?, ?)", (notification_id, channel, target, now, now))

    if CHANNELS.push and push_type:
        for sub in _push_targets(conn, audience, push_type):
            queue("push", str(sub))
    if CHANNELS.slack and slack:
        queue("slack", "channel")
    if CHANNELS.email:
        for email in dict.fromkeys(e.strip().lower() for e in emails if is_email(e)):
            queue("email", email)
    return notification_id


def _push_targets(conn: sqlite3.Connection, audience: str, push_type: str) -> list[int]:
    if audience == "admins":
        subs = conn.execute("SELECT id, user FROM push_subscriptions WHERE role = 'network-admin'").fetchall()
    elif audience == "all":
        subs = conn.execute("SELECT id, user FROM push_subscriptions").fetchall()
    elif audience.startswith("user:"):
        subs = conn.execute("SELECT id, user FROM push_subscriptions WHERE user = ?", (audience[5:],)).fetchall()
    else:
        return []
    return [s["id"] for s in subs if push_type == "__test__" or push_type in push_types_for(conn, s["user"])]


# --- read --------------------------------------------------------------------------------------


def _visible(user: str, role: str | None) -> tuple[str, list[Any]]:
    clause = "(n.audience = 'all' OR n.audience = ?"
    args: list[Any] = [f"user:{user}"]
    if role == "network-admin":
        clause += " OR n.audience = 'admins'"
    return clause + ")", args


def list_for(conn: sqlite3.Connection, user: str, role: str | None, *, limit: int = 50,
             unread_only: bool = False) -> dict[str, Any]:
    clause, args = _visible(user, role)
    rows = conn.execute(
        f"SELECT n.*, r.read_at FROM notifications n "
        f"LEFT JOIN notification_reads r ON r.notification_id = n.id AND r.user = ? "
        f"WHERE {clause}{' AND r.read_at IS NULL' if unread_only else ''} ORDER BY n.id DESC LIMIT ?",
        [user, *args, limit]).fetchall()
    unread = conn.execute(
        f"SELECT COUNT(*) FROM notifications n LEFT JOIN notification_reads r ON r.notification_id = n.id AND r.user = ? "
        f"WHERE {clause} AND r.read_at IS NULL", [user, *args]).fetchone()[0]
    latest = conn.execute(f"SELECT COALESCE(MAX(n.id), 0) FROM notifications n WHERE {clause}", args).fetchone()[0]
    items = [{k: r[k] for k in ("id", "type", "severity", "title", "body", "link", "created_at", "read_at")}
             for r in rows]
    return {"items": items, "unread": unread, "latest_id": latest}


def mark_read(conn: sqlite3.Connection, user: str, role: str | None, ids: list[int] | None) -> int:
    """Mark the given notifications (or all visible ones) read for this person."""
    clause, args = _visible(user, role)
    sql = f"SELECT n.id FROM notifications n WHERE {clause}"
    if ids is not None:
        if not ids:
            return 0
        sql += f" AND n.id IN ({','.join('?' for _ in ids)})"
        args += ids
    now = utcnow()
    with conn:
        targets = [r[0] for r in conn.execute(sql, args)]
        conn.executemany("INSERT OR IGNORE INTO notification_reads (notification_id, user, read_at) VALUES (?, ?, ?)",
                         [(i, user, now) for i in targets])
    return len(targets)


# --- preferences and push subscriptions ------------------------------------------------------


def push_types_for(conn: sqlite3.Connection, user: str) -> list[str]:
    row = conn.execute("SELECT push_types FROM notification_prefs WHERE user = ?", (user,)).fetchone()
    return json.loads(row[0]) if row else list(DEFAULT_PUSH_TYPES)


def save_push_types(conn: sqlite3.Connection, user: str, types: list[str]) -> list[str]:
    clean = [t for t in PUSH_TYPES if t in set(types)]
    with conn:
        conn.execute("INSERT INTO notification_prefs (user, push_types) VALUES (?, ?) "
                     "ON CONFLICT(user) DO UPDATE SET push_types = excluded.push_types", (user, json.dumps(clean)))
    return clean


def subscribe(conn: sqlite3.Connection, user: str, role: str, endpoint: str, p256dh: str, auth: str) -> int:
    with conn:
        conn.execute("INSERT INTO push_subscriptions (user, role, endpoint, p256dh, auth, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(endpoint) DO UPDATE SET user = excluded.user, "
                     "role = excluded.role, p256dh = excluded.p256dh, auth = excluded.auth",
                     (user, role, endpoint, p256dh, auth, utcnow()))
    return conn.execute("SELECT id FROM push_subscriptions WHERE endpoint = ?", (endpoint,)).fetchone()[0]


def unsubscribe(conn: sqlite3.Connection, endpoint: str) -> None:
    with conn:
        conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))


def subscriptions_for(conn: sqlite3.Connection, user: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM push_subscriptions WHERE user = ?", (user,)).fetchone()[0]


# --- event watchers (publish announcements) ---------------------------------------------------


def watchers(conn: sqlite3.Connection, doc: int) -> list[str]:
    return [r[0] for r in conn.execute("SELECT email FROM event_watchers WHERE document_id = ? ORDER BY email", (doc,))]


def add_watcher(conn: sqlite3.Connection, doc: int, email: str, user: str) -> list[str]:
    with conn:
        conn.execute("INSERT OR IGNORE INTO event_watchers (document_id, email, created_by, created_at) VALUES (?, ?, ?, ?)",
                     (doc, email.strip().lower(), user, utcnow()))
    return watchers(conn, doc)


def remove_watcher(conn: sqlite3.Connection, doc: int, email: str) -> list[str]:
    with conn:
        conn.execute("DELETE FROM event_watchers WHERE document_id = ? AND email = ?", (doc, email.strip().lower()))
    return watchers(conn, doc)


# --- what happens in the app -> who is told ------------------------------------------------------


def ticket_created(conn: sqlite3.Connection, ticket: dict[str, Any]) -> None:
    urgent = ticket["severity"] in ("P1", "P2")
    where = ticket.get("stand") or ticket.get("location")
    notify(conn, type="ticket_new", severity="urgent" if urgent else "normal",
           title=f"{ticket['severity']} ticket #{ticket['id']}: {ticket['title']}",
           body=" · ".join(x for x in (where, ticket.get("partner"), ticket.get("event_code"),
                                       "via WhatsApp" if ticket["source"] == "whatsapp" else None) if x) or None,
           link=f"/helpdesk?view=tickets&ticket={ticket['id']}", event_id=ticket["event_id"],
           push_type="ticket_urgent" if urgent else "ticket_all", slack=urgent,
           dedup_key=f"ticket-new:{ticket['id']}")
    if urgent:  # people who asked for every ticket also get the urgent ones
        _also_push(conn, ticket, "ticket_all")


def _also_push(conn: sqlite3.Connection, ticket: dict[str, Any], push_type: str) -> None:
    """Queue pushes for subscribers who chose ``push_type`` but not the type already used."""
    if not CHANNELS.push:
        return
    row = conn.execute("SELECT id FROM notifications WHERE dedup_key = ?", (f"ticket-new:{ticket['id']}",)).fetchone()
    if not row:
        return
    already = {r[0] for r in conn.execute("SELECT target FROM notification_deliveries WHERE notification_id = ? "
                                          "AND channel = 'push'", (row[0],))}
    now = utcnow()
    for sub in _push_targets(conn, "admins", push_type):
        if str(sub) not in already:
            conn.execute("INSERT INTO notification_deliveries (notification_id, channel, target, next_try_at, created_at) "
                         "VALUES (?, 'push', ?, ?, ?)", (row[0], str(sub), now, now))


def ticket_assigned(conn: sqlite3.Connection, ticket: dict[str, Any], assignee: str, by: str) -> None:
    if not assignee or assignee == by:
        return
    notify(conn, type="ticket_assigned", title=f"Ticket #{ticket['id']} assigned to you",
           body=f"{ticket['severity']} · {ticket['title']} (by {by})", audience=f"user:{assignee}",
           link=f"/helpdesk?view=tickets&ticket={ticket['id']}", event_id=ticket["event_id"], push_type="assigned")


def request_submitted(conn: sqlite3.Connection, doc: int, team: str, count: int, requester: str, late: bool) -> None:
    notify(conn, type="request_new",
           title=f"{count} new ICT request{'s' if count != 1 else ''} from {team}",
           body=f"Submitted by {requester}" + (" after the deadline" if late else ""),
           link=f"/event/{doc}?view=requests", event_id=doc, push_type="requests")


def request_reviewed(conn: sqlite3.Connection, doc: int, req: dict[str, Any], event_code: str, status: str,
                     note: str | None) -> None:
    """Email the requester (they have no app account) when their request is reviewed."""
    label = {"approved": "approved", "needs_info": "needs more information", "declined": "declined"}[status]
    contact = req["requester_contact"]
    notify(conn, type="request_reviewed", audience="external", event_id=doc,
           title=f"Your ICT request #{req['id']} for {event_code} was {label}",
           body=(f"Request: {req['data'].get('purpose')} ({req['data'].get('area')}).\n"
                 + (f"Note from ICT: {note}\n" if note else "")
                 + ("Answer the question on your team's request link." if status == "needs_info" else "")).strip(),
           emails=[contact] if is_email(contact) else [])


def request_link_shared(conn: sqlite3.Connection, doc: int, event_code: str, team: str, url: str, email: str,
                        deadline: str | None) -> None:
    notify(conn, type="request_link", audience="external", event_id=doc,
           title=f"Request network connections for {event_code} ({team})",
           body=(f"Use this link to request wired connections or private Wi-Fi for each workspace your team uses:\n{url}\n"
                 + (f"Please send requests by {deadline}." if deadline else "")).strip(),
           link=url, emails=[email])


def version_published(conn: sqlite3.Connection, doc: int, event_code: str, version: int, user: str,
                      summary: str, note: str | None) -> None:
    notify(conn, type="version_published", severity="low", event_id=doc,
           title=f"{event_code} V{version} published",
           body="\n".join(x for x in (note and f"Note: {note}", summary, f"Published by {user}") if x),
           link=f"/?doc={doc}", emails=watchers(conn, doc))
