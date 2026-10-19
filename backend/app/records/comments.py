"""Comments on an event's requirements, from viewers and network admins.

A comment is about one item (Wi-Fi network, Ethernet requirement, partner order) or the event in
general, and is stamped with the latest published version ("raised on V3"). Anyone with a role
can comment and reply; network admins resolve and reopen. Open comments carry over to later
versions. The item's name and place are copied onto the comment, so it still reads correctly
after the item is changed or deleted. Comments are never edits: the requirements don't change.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from .. import db
from ..auth import Principal, current_principal, require_admin, require_known_role
from ..db import utcnow
from ..notifications import service as notifications
from . import published, store
from .routes import _not_archived

SCHEMA = """
CREATE TABLE IF NOT EXISTS comments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    parent_id    INTEGER REFERENCES comments(id) ON DELETE CASCADE,  -- set on replies
    record_id    INTEGER,          -- the item; no foreign key, so comments outlive deleted items
    record_type  TEXT,
    item_label   TEXT,             -- e.g. "Stage 2 - Press", copied when the comment is made
    item_where   TEXT,             -- e.g. "Main venue · STAGES"
    version      INTEGER,          -- latest published version when it was made (None = before V1)
    body         TEXT NOT NULL,
    user         TEXT NOT NULL,
    role         TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    resolved_at  TEXT,
    resolved_by  TEXT
);
CREATE INDEX IF NOT EXISTS comments_by_doc ON comments (document_id, parent_id);
"""

TYPE_TITLES = {"wifi": "Wi-Fi network", "ethernet": "Ethernet requirement", "partner": "Partner order"}
NAME_FIELD = {"wifi": "ssid", "ethernet": "purpose", "partner": "partner"}


class CommentError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status = status_code


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


# --- service -------------------------------------------------------------------------------


def _item(conn: sqlite3.Connection, doc: int, record_id: int) -> dict[str, Any]:
    """The item's type, name and place: from the live requirements, or the latest published version
    (what viewers see) if it has since been deleted."""
    record = store.get_record(conn, doc, record_id)
    if record is not None and record["archived_at"] is None:
        venue = next((v["name"] for v in store.venues(conn, doc) if v["id"] == record["venue_id"]), None)
        group = next((g["name"] for g in store.groups(conn, doc) if g["id"] == record["group_id"]), None)
    else:
        view = published.latest(conn, doc)
        record = next((r for r in (view or {}).get("records", []) if r["id"] == record_id), None)
        if record is None:
            raise CommentError(404, "That item no longer exists")
        venue = next((v["name"] for v in view["venues"] if v["id"] == record["venue_id"]), None)
        group = next((g["name"] for g in view["groups"] if g["id"] == record["group_id"]), None)
    data = record["data"]
    name = data.get(NAME_FIELD[record["type"]]) or data.get("location") or "(unnamed)"
    return {"record_type": record["type"], "item_label": str(name)[:200],
            "item_where": " · ".join(x for x in (venue, group) if x) or None}


def _row(r: sqlite3.Row) -> dict[str, Any]:
    return dict(r)


def _get(conn: sqlite3.Connection, doc: int, comment_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM comments WHERE id = ? AND document_id = ?", (comment_id, doc)).fetchone()
    if row is None:
        raise CommentError(404, "Comment not found")
    return dict(row)


def _check_author(principal: Principal) -> None:
    if principal.user == "anonymous":
        raise CommentError(422, "Enter your name in the top bar first, so people know who commented")


def _event(conn: sqlite3.Connection, doc: int) -> dict[str, Any]:
    document = db.get_document(conn, doc)
    if document is None:
        raise CommentError(404, "Document not found")
    if document["kind"] == "template":
        raise CommentError(409, "Templates don't take comments")
    return document


def threads(conn: sqlite3.Connection, doc: int, *, state: str = "all", record_id: int | None = None
            ) -> list[dict[str, Any]]:
    """Top-level comments (newest first) with their replies (oldest first)."""
    sql, args = "SELECT * FROM comments WHERE document_id = ? AND parent_id IS NULL", [doc]
    if state == "open":
        sql += " AND resolved_at IS NULL"
    elif state == "resolved":
        sql += " AND resolved_at IS NOT NULL"
    if record_id is not None:
        sql += " AND record_id = ?"
        args.append(record_id)
    tops = [_row(r) for r in conn.execute(sql + " ORDER BY id DESC", args)]
    replies: dict[int, list[dict[str, Any]]] = {}
    if tops:
        ids = [t["id"] for t in tops]
        for r in conn.execute(f"SELECT * FROM comments WHERE parent_id IN ({','.join('?' * len(ids))}) ORDER BY id",
                              ids):
            replies.setdefault(r["parent_id"], []).append(_row(r))
    return [t | {"replies": replies.get(t["id"], [])} for t in tops]


def open_counts(conn: sqlite3.Connection, doc: int) -> dict[str, Any]:
    rows = conn.execute("SELECT record_id, COUNT(*) FROM comments WHERE document_id = ? AND parent_id IS NULL "
                        "AND resolved_at IS NULL GROUP BY record_id", (doc,)).fetchall()
    return {"open": sum(n for _, n in rows),
            "by_record": {str(record_id): n for record_id, n in rows if record_id is not None}}


def _latest_version(conn: sqlite3.Connection, doc: int) -> int | None:
    versions = store.published_versions(conn, doc)
    return versions[-1] if versions else None


def _label(c: dict[str, Any]) -> str:
    return f"{TYPE_TITLES[c['record_type']]} '{c['item_label']}'" if c["record_type"] else "the event"


def add(conn: sqlite3.Connection, doc: int, *, body: str, record_id: int | None, principal: Principal
        ) -> dict[str, Any]:
    document = _event(conn, doc)
    _check_author(principal)
    body = body.strip()
    if not body:
        raise CommentError(422, "Write a comment first")
    item = _item(conn, doc, record_id) if record_id is not None else {}
    version = _latest_version(conn, doc)
    code = document["name"].split(" | ")[0]
    with conn:
        comment_id = conn.execute(
            "INSERT INTO comments (document_id, record_id, record_type, item_label, item_where, version, body, user, "
            "role, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (doc, record_id, item.get("record_type"), item.get("item_label"), item.get("item_where"), version,
             body, principal.user, principal.role, utcnow())).lastrowid
        comment = _get(conn, doc, comment_id)
        store.log(conn, doc=doc, entity="comment", action="comment", user=principal.user, record_id=record_id,
                  summary=f"Commented on {_label(comment)}{f' (V{version})' if version else ''}: {body}")
        notifications.notify(conn, type="comment", event_id=doc, push_type="comments",
                             title=f"New comment on {code}: {_label(comment)}",
                             body=f"{principal.user}: {body}", link=f"/event/{doc}?view=comments")
    return comment | {"replies": []}


def reply(conn: sqlite3.Connection, doc: int, comment_id: int, *, body: str, principal: Principal
          ) -> dict[str, Any]:
    document = _event(conn, doc)
    _check_author(principal)
    parent = _get(conn, doc, comment_id)
    if parent["parent_id"] is not None:
        raise CommentError(422, "Reply to the first comment in the thread")
    body = body.strip()
    if not body:
        raise CommentError(422, "Write a reply first")
    code = document["name"].split(" | ")[0]
    with conn:
        reply_id = conn.execute(
            "INSERT INTO comments (document_id, parent_id, record_id, record_type, item_label, item_where, version, "
            "body, user, role, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (doc, comment_id, parent["record_id"], parent["record_type"], parent["item_label"],
             parent["item_where"], _latest_version(conn, doc), body, principal.user, principal.role,
             utcnow())).lastrowid
        store.log(conn, doc=doc, entity="comment", action="reply", user=principal.user,
                  record_id=parent["record_id"], summary=f"Replied about {_label(parent)}: {body}")
        # Tell the person who started the thread; if they're the one replying, tell the admins.
        audience = f"user:{parent['user']}" if parent["user"] != principal.user else "admins"
        notifications.notify(conn, type="comment_reply", event_id=doc, audience=audience,
                             push_type="comments" if audience == "admins" else None,
                             title=f"{principal.user} replied about {_label(parent)} ({code})", body=body,
                             link=f"/event/{doc}?view=comments")
    return _get(conn, doc, reply_id)


def set_resolved(conn: sqlite3.Connection, doc: int, comment_id: int, *, resolved: bool, principal: Principal
                 ) -> dict[str, Any]:
    document = _event(conn, doc)
    comment = _get(conn, doc, comment_id)
    if comment["parent_id"] is not None:
        raise CommentError(422, "Resolve the thread, not a reply")
    if bool(comment["resolved_at"]) == resolved:
        raise CommentError(409, "This comment is already resolved" if resolved else "This comment is already open")
    code = document["name"].split(" | ")[0]
    with conn:
        conn.execute("UPDATE comments SET resolved_at = ?, resolved_by = ? WHERE id = ?",
                     (utcnow() if resolved else None, principal.user if resolved else None, comment_id))
        store.log(conn, doc=doc, entity="comment", action="resolve" if resolved else "reopen", user=principal.user,
                  record_id=comment["record_id"],
                  summary=f"{'Resolved' if resolved else 'Reopened'} the comment on {_label(comment)}")
        if resolved and comment["user"] != principal.user:
            notifications.notify(conn, type="comment_resolved", event_id=doc, audience=f"user:{comment['user']}",
                                 title=f"Your comment on {_label(comment)} ({code}) was resolved",
                                 body=f"Resolved by {principal.user}.", link=f"/event/{doc}?view=comments")
    return _get(conn, doc, comment_id)


# --- API ---------------------------------------------------------------------------------------

router = APIRouter(prefix="/api/events/{document_id}/comments", dependencies=[Depends(_not_archived)])


def _conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = db.connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(_conn)]


def _reader(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
    require_known_role(principal)
    return principal


Reader = Annotated[Principal, Depends(_reader)]


def _run(fn):
    try:
        return fn()
    except CommentError as exc:
        raise HTTPException(exc.status, str(exc))


class NewComment(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    record_id: int | None = Field(default=None, ge=1)


class Reply(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


@router.get("")
def list_comments(document_id: int, conn: Conn, principal: Reader,
                  state: Annotated[str, Query(pattern="^(open|resolved|all)$")] = "all",
                  record_id: Annotated[int | None, Query(ge=1)] = None) -> dict[str, Any]:
    if db.get_document(conn, document_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return {"threads": threads(conn, document_id, state=state, record_id=record_id),
            "latest_version": _latest_version(conn, document_id)} | open_counts(conn, document_id)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_comment(document_id: int, body: NewComment, conn: Conn, principal: Reader) -> dict[str, Any]:
    return _run(lambda: add(conn, document_id, body=body.body, record_id=body.record_id, principal=principal))


@router.post("/{comment_id}/replies", status_code=status.HTTP_201_CREATED)
def create_reply(document_id: int, comment_id: int, body: Reply, conn: Conn, principal: Reader) -> dict[str, Any]:
    return _run(lambda: reply(conn, document_id, comment_id, body=body.body, principal=principal))


@router.post("/{comment_id}/resolve")
def resolve(document_id: int, comment_id: int, conn: Conn, principal: Reader) -> dict[str, Any]:
    require_admin(principal, "resolve comments")
    return _run(lambda: set_resolved(conn, document_id, comment_id, resolved=True, principal=principal))


@router.post("/{comment_id}/reopen")
def reopen(document_id: int, comment_id: int, conn: Conn, principal: Reader) -> dict[str, Any]:
    require_admin(principal, "reopen comments")
    return _run(lambda: set_resolved(conn, document_id, comment_id, resolved=False, principal=principal))
