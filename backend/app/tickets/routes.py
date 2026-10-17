"""Help desk API.

/api/tickets/...   for the ICT team. Reads: viewer or network-admin; changes: network-admin
                   (TEST ONLY role header, see auth.py). Every change is in the ticket's activity.
/api/bot/...       for the WhatsApp bot: Bearer BOT_API_TOKEN. See README "Help desk".
"""

from __future__ import annotations

import logging
import secrets
import sqlite3
from collections.abc import Callable, Iterator
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse

from .. import db
from ..auth import Principal, current_principal, require_admin, require_known_role
from . import report, service, store
from .models import (
    CATEGORIES, OPEN_STATUSES, SEVERITIES, SOURCES, STATUSES, TEAMS, Assign, BotTicket, Comment, LiveEvent, Merge,
    NewTicket, Revision, StatusChange, TicketPatch,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tickets")
bot_router = APIRouter(prefix="/api/bot")
settings_router = APIRouter(prefix="/api/helpdesk")
T = TypeVar("T")


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


def _agent(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
    require_admin(principal, "change tickets")
    return principal


Reader = Annotated[Principal, Depends(_reader)]
Agent = Annotated[Principal, Depends(_agent)]


def _run(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except service.TicketError as exc:
        raise HTTPException(exc.status, str(exc))


def _notify(request: Request) -> bool:
    return bool(request.app.state.settings.bot_callback_url)


def _event_name(conn: sqlite3.Connection, event_id: int | None) -> str:
    if event_id is None:
        return "All events"
    doc = db.get_document(conn, event_id)
    return doc["name"].split(" | ")[0] if doc else f"Event {event_id}"


# --- reference data, settings ------------------------------------------------------------


@router.get("/meta")
def meta(request: Request, conn: Conn, principal: Reader) -> dict[str, Any]:
    """Categories, the severity/SLA matrix, statuses, teams, known owners, events and the live event."""
    events = [{"id": d["id"], "code": d["name"].split(" | ")[0], "archived": bool(d["archived_at"])}
              for d in db.list_documents(conn)]
    live = store.get_setting(conn, "live_event_id")
    return {
        "categories": CATEGORIES,
        "severities": SEVERITIES,
        "statuses": STATUSES,
        "open_statuses": list(OPEN_STATUSES),
        "teams": TEAMS,
        "sources": SOURCES,
        "assignees": store.assignees(conn),
        "events": events,
        "live_event_id": int(live) if live else None,
        "bot": {"inbound": bool(request.app.state.settings.bot_api_token), "updates": _notify(request),
                "outbox": store.outbox_counts(conn)},
    }


@settings_router.put("/live-event")
def set_live_event(body: LiveEvent, conn: Conn, principal: Agent) -> dict[str, Any]:
    """The event that WhatsApp reports without an event code are filed under."""
    if body.event_id is not None:
        doc = db.get_document(conn, body.event_id)
        if doc is None or doc["kind"] != "event":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    store.set_setting(conn, "live_event_id", str(body.event_id) if body.event_id else None)
    log.info("%s set the live event to %s", principal.user, body.event_id)
    return {"live_event_id": body.event_id}


# --- reports (before /{ticket_id} so the paths don't clash) ----------------------------------


@router.get("/report")
def ticket_report(conn: Conn, principal: Reader, event_id: int | None = None,
                  informal: Annotated[int | None, Query(ge=0, le=100_000)] = None) -> dict[str, Any]:
    return report.kpis(conn, event_id, informal) | {"event": _event_name(conn, event_id)}


@router.get("/recurring")
def recurring(conn: Conn, principal: Reader) -> list[dict[str, Any]]:
    return report.recurring_issues(conn)


@router.get("/export")
def export(conn: Conn, principal: Reader, event_id: int | None = None,
           format: Annotated[str, Query(pattern="^(csv|xlsx)$")] = "xlsx") -> Response:
    name = _event_name(conn, event_id).replace(" ", "_")
    filename = f"ICT_tickets_{name}.{format}"
    if format == "csv":
        content, media = report.export_csv(conn, event_id), "text/csv; charset=utf-8"
    else:
        content = report.export_xlsx(conn, event_id, f"ICT tickets · {_event_name(conn, event_id)}")
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    log.info("%s exported tickets for %s as %s", principal.user, name, format)
    return Response(content, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# --- tickets ------------------------------------------------------------------------------


@router.get("")
def list_tickets(conn: Conn, principal: Reader, event_id: int | None = None,
                 status_filter: Annotated[str | None, Query(alias="status", max_length=20)] = "open",
                 severity: str | None = None, team: str | None = None, category: str | None = None,
                 assignee: Annotated[str | None, Query(max_length=200)] = None, unassigned: bool = False,
                 partner: Annotated[str | None, Query(max_length=200)] = None,
                 location: Annotated[str | None, Query(max_length=200)] = None,
                 q: Annotated[str | None, Query(max_length=200)] = None) -> list[dict[str, Any]]:
    """status: 'open' (default), 'done', 'all' or one status."""
    statuses = {"open": OPEN_STATUSES, "done": ("resolved", "closed"), "all": None}.get(
        status_filter or "all", (status_filter,))
    if statuses and any(s not in STATUSES for s in statuses):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown status '{status_filter}'")
    tickets = store.list_tickets(conn, event_id=event_id, statuses=statuses, severity=severity, team=team,
                                 category=category, assignee=assignee, unassigned=unassigned, partner=partner,
                                 location=location, query=q)
    return [service.view(t) for t in tickets]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_ticket(body: NewTicket, conn: Conn, principal: Agent) -> dict[str, Any]:
    """Manual route: an ICT team member raises a ticket."""
    fields = body.model_dump(exclude={"event_id", "assignee"})
    ticket = _run(lambda: service.create_ticket(conn, fields, user=principal.user, event_id=body.event_id,
                                                assignee=body.assignee))
    log.info("%s raised ticket #%s", principal.user, ticket["id"])
    return service.view(ticket)


@router.get("/{ticket_id}")
def get_ticket(ticket_id: int, conn: Conn, principal: Reader) -> dict[str, Any]:
    ticket = _run(lambda: service._get(conn, ticket_id))
    return service.view(ticket) | {
        "activity": store.activity(conn, ticket_id),
        "duplicates": [{"id": t["id"], "title": t["title"], "reporter_name": t["reporter_name"]}
                       for t in store.merged_children(conn, ticket_id)],
    }


@router.get("/{ticket_id}/dependencies")
def ticket_dependencies(ticket_id: int, conn: Conn, principal: Reader) -> dict[str, Any]:
    """What depends on the network where this ticket is (from the event's dependency register),
    with the severity the most critical match suggests."""
    from ..records import dependencies

    ticket = _run(lambda: service._get(conn, ticket_id))
    if ticket["event_id"] is None:
        return {"entries": [], "suggested_severity": None}
    entries = [dependencies.view(d) for d in
               dependencies.for_area(conn, ticket["event_id"], ticket["location"], ticket["stand"])]
    suggested = min((d["data"]["criticality"] for d in entries), default=None)
    return {"entries": entries, "suggested_severity": suggested}


@router.patch("/{ticket_id}")
def update_ticket(ticket_id: int, body: TicketPatch, conn: Conn, principal: Agent) -> dict[str, Any]:
    changes = body.changes.model_dump(exclude_unset=True)
    return service.view(_run(lambda: service.update_fields(conn, ticket_id, revision=body.revision, changes=changes,
                                                           user=principal.user)))


@router.post("/{ticket_id}/acknowledge")
def acknowledge(request: Request, ticket_id: int, body: Revision, conn: Conn, principal: Agent) -> dict[str, Any]:
    return service.view(_run(lambda: service.acknowledge(conn, ticket_id, revision=body.revision, user=principal.user,
                                                         notify=_notify(request))))


@router.post("/{ticket_id}/assign")
def assign(ticket_id: int, body: Assign, conn: Conn, principal: Agent) -> dict[str, Any]:
    return service.view(_run(lambda: service.assign(conn, ticket_id, revision=body.revision, assignee=body.assignee,
                                                    team=body.team, user=principal.user)))


@router.post("/{ticket_id}/status")
def change_status(request: Request, ticket_id: int, body: StatusChange, conn: Conn, principal: Agent
                  ) -> dict[str, Any]:
    return service.view(_run(lambda: service.change_status(
        conn, ticket_id, revision=body.revision, status=body.status, root_cause=body.root_cause,
        resolution=body.resolution, user=principal.user, notify=_notify(request))))


@router.post("/{ticket_id}/comments", status_code=status.HTTP_201_CREATED)
def add_comment(request: Request, ticket_id: int, body: Comment, conn: Conn, principal: Agent) -> dict[str, Any]:
    return service.view(_run(lambda: service.comment(conn, ticket_id, body=body.body, visibility=body.visibility,
                                                     user=principal.user, notify=_notify(request))))


@router.post("/{ticket_id}/merge")
def merge(request: Request, ticket_id: int, body: Merge, conn: Conn, principal: Agent) -> dict[str, Any]:
    return service.view(_run(lambda: service.merge(conn, ticket_id, revision=body.revision, into=body.into,
                                                   user=principal.user, notify=_notify(request))))


# --- WhatsApp bot ------------------------------------------------------------------------


def _bot(request: Request, authorization: Annotated[str | None, Header()] = None) -> None:
    token = request.app.state.settings.bot_api_token
    if not token:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The WhatsApp bot integration is not configured")
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not secrets.compare_digest(supplied.encode(), token.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid bot token",
                            headers={"WWW-Authenticate": "Bearer"})


Bot = Annotated[None, Depends(_bot)]


def _bot_view(request: Request, ticket: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticket": ticket["id"],
        "external_id": ticket["external_id"],
        "status": ticket["status"],
        "status_label": STATUSES[ticket["status"]],
        "merged_into": ticket["merged_into"],
        "url": f"{request.app.state.settings.app_public_url}/helpdesk?ticket={ticket['id']}",
    }


@bot_router.post("/tickets")
def bot_create(request: Request, body: BotTicket, conn: Conn, _: Bot) -> JSONResponse:
    """Create a ticket from a WhatsApp report. Retries with the same external_id return the
    existing ticket (200) instead of creating another one (201)."""
    ticket, created = service.create_from_bot(conn, body.model_dump(), notify=_notify(request))
    if created:
        log.info("WhatsApp bot created ticket #%s (report %s)", ticket["id"], body.external_id)
    return JSONResponse(_bot_view(request, ticket) | {"created": created},
                        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


@bot_router.get("/tickets/{external_id}")
def bot_status(request: Request, external_id: str, conn: Conn, _: Bot) -> dict[str, Any]:
    """Status of the ticket created from a report, for 'what's happening with my issue?'."""
    ticket = store.get_by_external_id(conn, external_id)
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No ticket for that report")
    return _bot_view(request, ticket)
