"""Notifications API: the in-app notification centre, live updates, preferences, browser push
subscriptions and publish followers. TEST ONLY identity (X-Role / X-User; the live stream takes
them as query parameters because EventSource can't send headers).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .. import db
from ..auth import KNOWN_ROLES, Principal, current_principal, require_admin, require_known_role
from . import service

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/notifications")
watchers_router = APIRouter(prefix="/api/events/{document_id}/watchers")


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


# --- notification centre ---------------------------------------------------------------------


@router.get("")
def notifications(conn: Conn, principal: Reader, unread_only: bool = False,
                  limit: Annotated[int, Query(ge=1, le=200)] = 50) -> dict[str, Any]:
    return service.list_for(conn, principal.user, principal.role, limit=limit, unread_only=unread_only)


class ReadBody(BaseModel):
    ids: list[int] | None = Field(default=None, max_length=500)  # None = mark everything read


@router.post("/read")
def mark_read(body: ReadBody, conn: Conn, principal: Reader) -> dict[str, Any]:
    marked = service.mark_read(conn, principal.user, principal.role, body.ids)
    return {"marked": marked} | service.list_for(conn, principal.user, principal.role, limit=1)


@router.get("/stream")
async def stream(request: Request, user: Annotated[str, Query(max_length=100)] = "",
                 role: Annotated[str, Query(max_length=40)] = "",
                 limit_seconds: Annotated[int, Query(ge=1, le=3600)] = 3600) -> StreamingResponse:
    """Server-sent events: an 'update' with the unread count and newest items whenever they change,
    and a comment every 15 s to keep proxies from closing the connection."""
    user = user.strip()[:100] or "anonymous"
    if role not in KNOWN_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Choose a role first")
    db_path = request.app.state.settings.db_path

    def snapshot() -> dict[str, Any]:
        conn = db.connect(db_path)
        try:
            return service.list_for(conn, user, role, limit=10)
        finally:
            conn.close()

    async def events():
        last = None
        started = time.monotonic()
        beat = time.monotonic()
        yield "retry: 5000\n\n"
        while time.monotonic() - started < limit_seconds:
            if await request.is_disconnected():
                break
            data = await run_in_threadpool(snapshot)
            key = (data["latest_id"], data["unread"])
            if key != last:
                last = key
                yield f"event: update\ndata: {json.dumps(data)}\n\n"
            elif time.monotonic() - beat > 15:
                beat = time.monotonic()
                yield ": keep-alive\n\n"
            await asyncio.sleep(3)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --- preferences and browser push -----------------------------------------------------------------


# Alert types that belong to the help desk; hidden while it's paused (choices are kept).
HELPDESK_PUSH_TYPES = {"ticket_urgent", "ticket_all", "sla", "assigned"}


def _hidden_types(request: Request) -> set[str]:
    return set() if request.app.state.settings.helpdesk_enabled else HELPDESK_PUSH_TYPES


@router.get("/settings")
def settings(request: Request, conn: Conn, principal: Reader) -> dict[str, Any]:
    chosen = service.push_types_for(conn, principal.user)
    hidden = _hidden_types(request)
    return {
        "push_available": service.CHANNELS.push,
        "vapid_public_key": request.app.state.vapid_public_key if service.CHANNELS.push else None,
        "push_types": [{"key": k, "label": v, "on": k in chosen} for k, v in service.PUSH_TYPES.items()
                       if k not in hidden],
        "devices": service.subscriptions_for(conn, principal.user),
        "channels": {"slack": service.CHANNELS.slack, "email": service.CHANNELS.email},
    }


class Prefs(BaseModel):
    push_types: list[str] = Field(max_length=20)


@router.put("/settings")
def save_settings(request: Request, body: Prefs, conn: Conn, principal: Reader) -> dict[str, Any]:
    hidden = _hidden_types(request)
    kept = [k for k in service.push_types_for(conn, principal.user) if k in hidden]  # not shown, so keep them
    saved = service.save_push_types(conn, principal.user, [k for k in body.push_types if k not in hidden] + kept)
    return {"push_types": [k for k in saved if k not in hidden]}


class Keys(BaseModel):
    p256dh: str = Field(min_length=10, max_length=200)
    auth: str = Field(min_length=10, max_length=100)


class Subscription(BaseModel):
    endpoint: str = Field(min_length=10, max_length=1000, pattern=r"^https://")
    keys: Keys


@router.post("/push/subscribe", status_code=status.HTTP_201_CREATED)
def subscribe(body: Subscription, conn: Conn, principal: Reader) -> dict[str, Any]:
    if not service.CHANNELS.push:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Browser alerts are not set up on this server")
    service.subscribe(conn, principal.user, principal.role or "viewer", body.endpoint, body.keys.p256dh, body.keys.auth)
    log.info("%s turned on browser alerts on a device", principal.user)
    return {"devices": service.subscriptions_for(conn, principal.user)}


class Endpoint(BaseModel):
    endpoint: str = Field(min_length=10, max_length=1000)


@router.post("/push/unsubscribe")
def unsubscribe(body: Endpoint, conn: Conn, principal: Reader) -> dict[str, Any]:
    service.unsubscribe(conn, body.endpoint)
    return {"devices": service.subscriptions_for(conn, principal.user)}


@router.post("/test", status_code=status.HTTP_201_CREATED)
def test(conn: Conn, principal: Reader) -> dict[str, Any]:
    """Send a test notification to me (in-app, and to my devices if alerts are on)."""
    with conn:
        service.notify(conn, type="test", severity="normal", audience=f"user:{principal.user}",
                       title="Test alert from Event Network Hub",
                       body="If you can see this, notifications reach you on this device.",
                       link="/helpdesk", push_type="__test__")
    return {"devices": service.subscriptions_for(conn, principal.user)}


# --- publish followers --------------------------------------------------------------------------


class WatcherBody(BaseModel):
    email: str = Field(min_length=3, max_length=200)


def _event(conn: sqlite3.Connection, document_id: int) -> None:
    document = db.get_document(conn, document_id)
    if document is None or document["kind"] != "event":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")


@watchers_router.get("")
def list_watchers(document_id: int, conn: Conn, principal: Reader) -> dict[str, Any]:
    _event(conn, document_id)
    return {"emails": service.watchers(conn, document_id), "email_available": service.CHANNELS.email}


@watchers_router.post("", status_code=status.HTTP_201_CREATED)
def add_watcher(document_id: int, body: WatcherBody, conn: Conn,
                principal: Annotated[Principal, Depends(current_principal)]) -> dict[str, Any]:
    require_admin(principal, "change who is emailed")
    _event(conn, document_id)
    if not service.is_email(body.email):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter an email address, like name@example.com")
    return {"emails": service.add_watcher(conn, document_id, body.email, principal.user)}


@watchers_router.delete("/{email}")
def remove_watcher(document_id: int, email: str, conn: Conn,
                   principal: Annotated[Principal, Depends(current_principal)]) -> dict[str, Any]:
    require_admin(principal, "change who is emailed")
    _event(conn, document_id)
    return {"emails": service.remove_watcher(conn, document_id, email)}
