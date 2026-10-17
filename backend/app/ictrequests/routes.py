"""ICT request API.

/api/request-links/{token}/...           public form: the unguessable link is the credential
                                           (one per team and event, revocable).
/api/events/{document_id}/ict-requests    ICT review: reads for viewer or network-admin,
                                           changes for network-admin (TEST ONLY role header).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Iterator
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status

from .. import db
from ..auth import Principal, current_principal, require_admin, require_known_role
from ..records.routes import _not_archived
from . import service
from .models import KINDS, REQUEST_TEAMS, STATUSES, Approval, LineUpdate, NewLink, Review, Settings, Submission

log = logging.getLogger(__name__)

public_router = APIRouter(prefix="/api/request-links/{token}")
admin_router = APIRouter(prefix="/api/events/{document_id}", dependencies=[Depends(_not_archived)])
T = TypeVar("T")
Token = Annotated[str, Path(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]


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


def _editor(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
    require_admin(principal, "review ICT requests")
    return principal


Reader = Annotated[Principal, Depends(_reader)]
Editor = Annotated[Principal, Depends(_editor)]


def _run(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except service.RequestError as exc:
        raise HTTPException(exc.status, str(exc))


# --- public form --------------------------------------------------------------------------


@public_router.get("")
def form(token: Token, conn: Conn) -> dict[str, Any]:
    link, event = _run(lambda: service.link_by_token(conn, token))
    settings = service.settings(conn, event["id"])
    return {
        "event": event["name"].split(" | ")[0],
        "team": {"key": link["team"], "label": REQUEST_TEAMS[link["team"]]},
        "deadline": settings["deadline"],
        "instructions": settings["instructions"],
        "late": service.is_late(conn, event["id"]),
        "statuses": STATUSES,
        "requests": [service.public_view(r) for r in service.list_for(conn, event["id"], link_id=link["id"])],
    } | service.reference(conn, event["id"], link["team"])


@public_router.post("/requests", status_code=status.HTTP_201_CREATED)
def submit(token: Token, body: Submission, conn: Conn) -> dict[str, Any]:
    result = _run(lambda: service.submit(conn, token, body.model_dump()))
    log.info("ICT request link submitted %s line(s) (batch %s, late=%s)", len(result["requests"]), result["batch"],
             result["late"])
    return result | {"requests": [service.public_view(r) for r in result["requests"]]}


@public_router.put("/requests/{request_id}")
def resubmit(token: Token, request_id: int, body: LineUpdate, conn: Conn) -> dict[str, Any]:
    req = _run(lambda: service.resubmit(conn, token, request_id, revision=body.revision,
                                        line=body.line.model_dump()))
    return service.public_view(req)


# --- ICT review ------------------------------------------------------------------------------


@admin_router.get("/ict-requests")
def overview(request: Request, document_id: int, conn: Conn, principal: Reader) -> dict[str, Any]:
    document = db.get_document(conn, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    base = request.app.state.settings.app_public_url
    return {
        "settings": service.settings(conn, document_id),
        "late_now": service.is_late(conn, document_id),
        "teams": REQUEST_TEAMS,
        "kinds": KINDS,
        "statuses": STATUSES,
        "links": [link | {"url": f"{base}/request/{link['token']}"} for link in service.links(conn, document_id)],
        "requests": service.list_for(conn, document_id),
        "summary": service.summary(conn, document_id),
    }


@admin_router.put("/ict-requests/settings")
def save_settings(document_id: int, body: Settings, conn: Conn, principal: Editor) -> dict[str, Any]:
    return _run(lambda: service.save_settings(conn, document_id, deadline=body.deadline,
                                              instructions=body.instructions, user=principal.user))


@admin_router.post("/ict-requests/links", status_code=status.HTTP_201_CREATED)
def create_link(request: Request, document_id: int, body: NewLink, conn: Conn, principal: Editor) -> dict[str, Any]:
    link = _run(lambda: service.create_link(conn, document_id, body.team, user=principal.user))
    log.info("%s created an ICT request link for %s on document %s", principal.user, body.team, document_id)
    return link | {"url": f"{request.app.state.settings.app_public_url}/request/{link['token']}"}


@admin_router.post("/ict-requests/links/{link_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
def revoke_link(document_id: int, link_id: int, conn: Conn, principal: Editor) -> None:
    _run(lambda: service.revoke_link(conn, document_id, link_id, user=principal.user))


@admin_router.post("/ict-requests/{request_id}/approve")
def approve(document_id: int, request_id: int, body: Approval, conn: Conn, principal: Editor) -> dict[str, Any]:
    return _run(lambda: service.approve(
        conn, document_id, request_id, revision=body.revision, venue_id=body.venue_id, group_id=body.group_id,
        vlan=body.vlan, location=body.location, ports=body.ports, name=body.name, note=body.note,
        user=principal.user))


@admin_router.post("/ict-requests/{request_id}/needs-info")
def needs_info(document_id: int, request_id: int, body: Review, conn: Conn, principal: Editor) -> dict[str, Any]:
    return _run(lambda: service.set_review(conn, document_id, request_id, revision=body.revision,
                                           status="needs_info", note=body.note, user=principal.user))


@admin_router.post("/ict-requests/{request_id}/decline")
def decline(document_id: int, request_id: int, body: Review, conn: Conn, principal: Editor) -> dict[str, Any]:
    return _run(lambda: service.set_review(conn, document_id, request_id, revision=body.revision,
                                           status="declined", note=body.note, user=principal.user))
