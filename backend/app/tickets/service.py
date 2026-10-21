"""Help desk rules: lifecycle, SLA timers, assignment, merge, reporter updates."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .. import db
from . import store
from .models import CATEGORIES, DEFAULT_TEAM, DONE_STATUSES, FIELD_LABELS, OPEN_STATUSES, SEVERITIES, STATUSES, TEAMS


class TicketError(Exception):
    """A user-facing problem; ``status`` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _minutes(start: str | None, end: str | None) -> float | None:
    a, b = parse_time(start), parse_time(end)
    return round((b - a).total_seconds() / 60, 1) if a and b else None


def sla(ticket: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """SLA state from creation time and the severity's targets.

    ack/resolve: 'met' or 'breached' once done; 'pending' or 'breached' while open.
    """
    now = now or datetime.now(timezone.utc)
    target = SEVERITIES[ticket["severity"]]
    created = parse_time(ticket["created_at"])
    ack_due = created + timedelta(minutes=target["ack_minutes"])
    resolve_due = created + timedelta(minutes=target["resolve_minutes"])
    acked = parse_time(ticket["acknowledged_at"])
    done = parse_time(ticket["resolved_at"] or ticket["closed_at"])

    def state(when: datetime | None, due: datetime) -> str:
        if when is not None:
            return "met" if when <= due else "breached"
        return "breached" if now > due else "pending"

    return {
        "ack_due": ack_due.isoformat(timespec="seconds"),
        "resolve_due": resolve_due.isoformat(timespec="seconds"),
        "ack": state(acked, ack_due),
        "resolve": "n/a" if ticket["merged_into"] else state(done, resolve_due),
        "minutes_to_ack": _minutes(ticket["created_at"], ticket["acknowledged_at"]),
        "minutes_to_resolve": _minutes(ticket["created_at"], ticket["resolved_at"] or ticket["closed_at"]),
    }


def view(ticket: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """A ticket as the API returns it, with its SLA state."""
    return ticket | {"sla": sla(ticket, now)}


def _get(conn: db.Connection, ticket_id: int) -> dict[str, Any]:
    ticket = store.get_ticket(conn, ticket_id)
    if ticket is None:
        raise TicketError(404, f"Ticket #{ticket_id} not found")
    return ticket


def _check_revision(ticket: dict[str, Any], revision: int) -> None:
    if ticket["revision"] != revision:
        raise TicketError(409, f"Ticket #{ticket['id']} was changed by someone else at {ticket['updated_at']}. "
                               "Reload to see the latest version.")


def _write(conn: db.Connection, ticket: dict[str, Any], revision: int, values: dict[str, Any]) -> None:
    if not store.update_ticket(conn, ticket["id"], revision, values):
        raise TicketError(409, f"Ticket #{ticket['id']} was changed by someone else. Reload to see the latest version.")


def _not_merged(ticket: dict[str, Any]) -> None:
    if ticket["merged_into"]:
        raise TicketError(409, f"Ticket #{ticket['id']} was merged into #{ticket['merged_into']}. Work on that ticket.")


def _notify(conn: db.Connection, ticket: dict[str, Any], message: str, *, notify: bool) -> None:
    """Queue an update for the reporter (WhatsApp tickets only, when the bot callback is configured).

    Reporters of duplicates merged into this ticket get the same update.
    """
    if not notify:
        return
    current = store.get_ticket(conn, ticket["id"]) or ticket
    for recipient in [current, *store.merged_children(conn, current["id"])]:
        if recipient["source"] != "whatsapp":
            continue
        store.enqueue(conn, recipient["id"], {
            "type": "ticket.update",
            "ticket": current["id"],
            "reported_as": recipient["id"],
            "external_id": recipient["external_id"],
            "reporter_phone": recipient["reporter_phone"],
            "status": current["status"],
            "status_label": STATUSES[current["status"]],
            "message": message,
        })


# --- events -----------------------------------------------------------------------------


def resolve_event(conn: db.Connection, event_id: int | None = None, event_code: str | None = None
                  ) -> tuple[int | None, str | None]:
    """(event_id, event_code) for a ticket: the given event, an event by code, else the live event."""
    if event_id is not None:
        doc = db.get_document(conn, event_id)
        if doc is None or doc["kind"] != "event":
            raise TicketError(404, "Event not found")
        return doc["id"], doc["name"].split(" | ")[0]
    if event_code:
        row = conn.execute("SELECT id, name FROM documents WHERE kind = 'event' AND upper(name) LIKE %s",
                           (f"{event_code.upper()} |%",)).fetchone()
        if row:
            return row["id"], row["name"].split(" | ")[0]
        return None, event_code.upper()
    live = store.get_setting(conn, "live_event_id")
    if live:
        doc = db.get_document(conn, int(live))
        if doc is not None:
            return doc["id"], doc["name"].split(" | ")[0]
    return None, None


# --- create ----------------------------------------------------------------------------


def create_ticket(conn: db.Connection, fields: dict[str, Any], *, user: str, source: str = "manual",
                  external_id: str | None = None, event_id: int | None = None, event_code: str | None = None,
                  assignee: str | None = None) -> dict[str, Any]:
    category = fields.get("category") or "uncategorised"
    eid, code = resolve_event(conn, event_id, event_code)
    values = {k: v for k, v in fields.items() if v is not None} | {
        "category": category,
        "severity": fields.get("severity") or "P3",
        "team": fields.get("team") or DEFAULT_TEAM[category],
        "status": "new",
        "source": source,
        "external_id": external_id,
        "event_id": eid,
        "event_code": code,
        "created_by": user,
        "assignee": assignee,
    }
    with conn:
        ticket_id = store.insert_ticket(conn, values)
        where = values.get("stand") or values.get("location")
        store.log(conn, ticket_id, "created", user,
                  body=f"{SEVERITIES[values['severity']]['label']} · {CATEGORIES[category]}"
                       + (f" · {where}" if where else "") + f" · via {'WhatsApp' if source == 'whatsapp' else 'ICT'}")
        if assignee:
            store.log(conn, ticket_id, "assign", user, field="assignee", new=assignee)
    ticket = store.get_ticket(conn, ticket_id)
    from ..notifications import service as notifications

    with conn:
        notifications.ticket_created(conn, ticket)
        if assignee:
            notifications.ticket_assigned(conn, ticket, assignee, user)
    return ticket  # type: ignore[return-value]


def create_from_bot(conn: db.Connection, report: dict[str, Any], *, notify: bool) -> tuple[dict[str, Any], bool]:
    """Create a ticket from a WhatsApp report. Idempotent on external_id: (ticket, created)."""
    existing = store.get_by_external_id(conn, report["external_id"])
    if existing is not None:
        return existing, False
    message = report["message"]
    first_line = message.splitlines()[0].strip()
    title = first_line if len(first_line) <= 120 else first_line[:117].rstrip() + "…"
    fields = {k: report.get(k) for k in ("category", "severity", "location", "stand", "partner", "reporter_name",
                                         "reporter_phone")}
    if fields["partner"] and not fields["category"]:
        fields["category"] = "partner_stand"
    try:
        ticket = create_ticket(conn, fields | {"title": title, "description": message}, user="whatsapp-bot",
                               source="whatsapp", external_id=report["external_id"],
                               event_code=report.get("event_code"))
    except db.IntegrityError:  # the same report arrived twice at the same moment
        return store.get_by_external_id(conn, report["external_id"]), False  # type: ignore[return-value]
    with conn:
        _notify(conn, ticket, f"Thanks, we've logged this as ticket #{ticket['id']}. The ICT team has been alerted.",
                notify=notify)
    return ticket, True


# --- change -------------------------------------------------------------------------------


def update_fields(conn: db.Connection, ticket_id: int, *, revision: int, changes: dict[str, Any],
                  user: str) -> dict[str, Any]:
    ticket = _get(conn, ticket_id)
    _check_revision(ticket, revision)
    _not_merged(ticket)
    changed = {k: v for k, v in changes.items() if k in FIELD_LABELS and ticket.get(k) != v}
    if "title" in changed and not changed["title"]:
        raise TicketError(422, "A ticket needs a title")
    if not changed:
        return ticket
    for required in ("category", "severity", "team"):
        if required in changed and changed[required] is None:
            raise TicketError(422, f"{FIELD_LABELS[required]} can't be empty")
    with conn:
        _write(conn, ticket, revision, changed)
        for field, value in changed.items():
            store.log(conn, ticket_id, "field", user, field=field, old=ticket.get(field), new=value)
    return store.get_ticket(conn, ticket_id)  # type: ignore[return-value]


def acknowledge(conn: db.Connection, ticket_id: int, *, revision: int, user: str, notify: bool
                ) -> dict[str, Any]:
    """An ICT owner takes the ticket: stamps the acknowledge time, assigns it if unowned."""
    ticket = _get(conn, ticket_id)
    _check_revision(ticket, revision)
    _not_merged(ticket)
    if ticket["status"] in DONE_STATUSES:
        raise TicketError(409, "This ticket is already resolved. Reopen it first.")
    values: dict[str, Any] = {}
    if ticket["status"] == "new":
        values["status"] = "acknowledged"
    if not ticket["acknowledged_at"]:
        values |= {"acknowledged_at": db.utcnow(), "acknowledged_by": user}
    if not ticket["assignee"]:
        values["assignee"] = user
    if not values:
        return ticket
    with conn:
        _write(conn, ticket, revision, values)
        store.log(conn, ticket_id, "acknowledge", user, old=ticket["status"], new=values.get("status", ticket["status"]))
        if "assignee" in values:
            store.log(conn, ticket_id, "assign", user, field="assignee", old=None, new=user)
        _notify(conn, ticket, f"Ticket #{ticket_id}: {user} from the ICT team is on it.", notify=notify)
    return store.get_ticket(conn, ticket_id)  # type: ignore[return-value]


def assign(conn: db.Connection, ticket_id: int, *, revision: int, assignee: str | None, team: str | None,
           user: str) -> dict[str, Any]:
    ticket = _get(conn, ticket_id)
    _check_revision(ticket, revision)
    _not_merged(ticket)
    values: dict[str, Any] = {}
    if assignee != ticket["assignee"]:
        values["assignee"] = assignee
    if team and team != ticket["team"]:
        values["team"] = team
    if not values:
        return ticket
    with conn:
        _write(conn, ticket, revision, values)
        if "assignee" in values:
            store.log(conn, ticket_id, "assign", user, field="assignee", old=ticket["assignee"], new=assignee)
        if "team" in values:
            store.log(conn, ticket_id, "field", user, field="team", old=ticket["team"], new=team)
        if values.get("assignee"):
            from ..notifications import service as notifications

            notifications.ticket_assigned(conn, store.get_ticket(conn, ticket_id), assignee, user)
    return store.get_ticket(conn, ticket_id)  # type: ignore[return-value]


def change_status(conn: db.Connection, ticket_id: int, *, revision: int, status: str, user: str,
                  root_cause: str | None = None, resolution: str | None = None, notify: bool) -> dict[str, Any]:
    """Move a ticket through its lifecycle. Resolving or closing needs a root cause and a resolution."""
    ticket = _get(conn, ticket_id)
    _check_revision(ticket, revision)
    _not_merged(ticket)
    if status == ticket["status"]:
        return ticket
    values: dict[str, Any] = {"status": status}
    now = db.utcnow()
    if status in DONE_STATUSES:
        root_cause = root_cause or ticket["root_cause"]
        resolution = resolution or ticket["resolution"]
        if not root_cause or not resolution:
            raise TicketError(422, "Add the root cause and what fixed it before resolving or closing the ticket.")
        values |= {"root_cause": root_cause, "resolution": resolution}
        if not ticket["resolved_at"]:
            values["resolved_at"] = now
        if status == "closed":
            values["closed_at"] = now
    else:  # (re)opened or progressed
        if status == "new" and ticket["status"] != "new":
            raise TicketError(422, "A ticket can't go back to New. Use In progress or Waiting.")
        if ticket["status"] in DONE_STATUSES:
            values |= {"resolved_at": None, "closed_at": None}
    if status != "new" and not ticket["acknowledged_at"]:  # moving it on counts as acknowledging
        values |= {"acknowledged_at": now, "acknowledged_by": user}
    with conn:
        _write(conn, ticket, revision, values)
        store.log(conn, ticket_id, "status", user, field="status", old=ticket["status"], new=status,
                  body=(f"Root cause: {values['root_cause']}\nResolution: {values['resolution']}"
                        if status in DONE_STATUSES else None))
        message = {
            "acknowledged": f"Ticket #{ticket_id} has been picked up by the ICT team.",
            "in_progress": f"Ticket #{ticket_id}: the ICT team is working on it.",
            "waiting": f"Ticket #{ticket_id}: the ICT team is waiting on something to continue. We'll update you.",
            "resolved": f"Ticket #{ticket_id} is resolved: {values.get('resolution')}. Reply if it's still not working.",
            "closed": f"Ticket #{ticket_id} is closed.",
        }.get(status)
        if ticket["status"] in DONE_STATUSES and status in OPEN_STATUSES:
            message = f"Ticket #{ticket_id} has been reopened."
        if message:
            _notify(conn, ticket, message, notify=notify)
    return store.get_ticket(conn, ticket_id)  # type: ignore[return-value]


def comment(conn: db.Connection, ticket_id: int, *, body: str, visibility: str, user: str, notify: bool
            ) -> dict[str, Any]:
    ticket = _get(conn, ticket_id)
    if visibility == "reporter" and ticket["source"] != "whatsapp":
        raise TicketError(422, "Only tickets reported through WhatsApp can send updates to the reporter.")
    with conn:
        store.touch(conn, ticket_id)
        store.log(conn, ticket_id, "comment", user, body=body, visibility=visibility)
        if visibility == "reporter":
            _notify(conn, ticket, f"Ticket #{ticket_id}: {body}", notify=notify)
    return store.get_ticket(conn, ticket_id)  # type: ignore[return-value]


def merge(conn: db.Connection, ticket_id: int, *, revision: int, into: int, user: str, notify: bool
          ) -> dict[str, Any]:
    """Close ticket_id as a duplicate of ``into``; its reporter is told to follow the other ticket."""
    ticket = _get(conn, ticket_id)
    _check_revision(ticket, revision)
    _not_merged(ticket)
    if into == ticket_id:
        raise TicketError(422, "A ticket can't be merged into itself")
    target = _get(conn, into)
    if target["merged_into"]:
        raise TicketError(422, f"#{into} was itself merged into #{target['merged_into']}. Merge into that ticket.")
    now = db.utcnow()
    with conn:
        _write(conn, ticket, revision, {
            "merged_into": into, "status": "closed", "root_cause": "Duplicate report",
            "resolution": f"Duplicate of #{into}", "closed_at": now, "resolved_at": ticket["resolved_at"] or now,
        })
        store.touch(conn, into)
        store.log(conn, ticket_id, "merge", user, field="merged_into", new=into, body=f"Merged into #{into}")
        store.log(conn, into, "merge", user, field="duplicate", new=ticket_id,
                  body=f"#{ticket_id} merged into this ticket" + (
                      f" (reported by {ticket['reporter_name']})" if ticket["reporter_name"] else ""))
        _notify(conn, ticket, f"Ticket #{ticket_id} is a duplicate of #{into}, which the ICT team is already "
                              "handling. You'll get updates there.", notify=notify)
    return store.get_ticket(conn, ticket_id)  # type: ignore[return-value]


def team_label(team: str) -> str:
    return TEAMS.get(team, team)
