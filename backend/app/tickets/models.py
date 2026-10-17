"""Help desk domain: categories, severity/SLA matrix, statuses, teams, request bodies.

The severity matrix and SLA targets are the default agreed for the pilot; the ICT
team adjusts them here (one place) after the first events.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Category key -> label. "uncategorised" is for bot reports not yet triaged.
CATEGORIES = {
    "wifi": "Wi-Fi",
    "wired": "Wired connectivity",
    "hardware": "Network hardware",
    "partner_stand": "Partner stand connectivity",
    "venue": "Venue-wide",
    "uncategorised": "Not yet categorised",
}

TEAMS = {"network": "Network team", "it_support": "IT Support"}

# Which team a new ticket goes to, by category (reassign at any time).
DEFAULT_TEAM = {
    "wifi": "network",
    "wired": "network",
    "hardware": "it_support",
    "partner_stand": "network",
    "venue": "network",
    "uncategorised": "network",
}

# Default severity matrix. ack_minutes / resolve_minutes are the SLA targets,
# measured from the ticket's creation.
SEVERITIES = {
    "P1": {"label": "P1 Critical", "ack_minutes": 5, "resolve_minutes": 15,
           "meaning": "Venue-wide or keynote/stage/registration outage; many people affected"},
    "P2": {"label": "P2 High", "ack_minutes": 5, "resolve_minutes": 30,
           "meaning": "A partner stand or critical area is down"},
    "P3": {"label": "P3 Medium", "ack_minutes": 5, "resolve_minutes": 120,
           "meaning": "Degraded service: slow or intermittent in one area or stand"},
    "P4": {"label": "P4 Low", "ack_minutes": 5, "resolve_minutes": 480,
           "meaning": "A single user or device, or a request"},
}

STATUSES = {
    "new": "New",
    "acknowledged": "Acknowledged",
    "in_progress": "In progress",
    "waiting": "Waiting",
    "resolved": "Resolved",
    "closed": "Closed",
}
OPEN_STATUSES = ("new", "acknowledged", "in_progress", "waiting")
DONE_STATUSES = ("resolved", "closed")

SOURCES = {"whatsapp": "WhatsApp bot", "manual": "Raised by ICT"}

Category = Literal["wifi", "wired", "hardware", "partner_stand", "venue", "uncategorised"]
Severity = Literal["P1", "P2", "P3", "P4"]
Status = Literal["new", "acknowledged", "in_progress", "waiting", "resolved", "closed"]
Team = Literal["network", "it_support"]

Short = Annotated[str | None, Field(default=None, max_length=200)]
Long = Annotated[str | None, Field(default=None, max_length=4000)]

# Fields an ICT user can edit directly (status, assignment and merge have their own actions).
EDITABLE_FIELDS = ("title", "description", "category", "severity", "team", "location", "stand", "partner",
                   "reporter_name", "reporter_phone")
FIELD_LABELS = {
    "title": "Title", "description": "Description", "category": "Category", "severity": "Severity",
    "team": "Team", "location": "Location", "stand": "Stand", "partner": "Partner",
    "reporter_name": "Reporter", "reporter_phone": "Reporter phone", "assignee": "Owner", "status": "Status",
}


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def _strip(cls, v):
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v


class TicketFields(_Body):
    title: Short = None
    description: Long = None
    category: Category | None = None
    severity: Severity | None = None
    team: Team | None = None
    location: Short = None
    stand: Short = None
    partner: Short = None
    reporter_name: Short = None
    reporter_phone: Annotated[str | None, Field(default=None, max_length=40)] = None


class NewTicket(TicketFields):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    category: Category
    severity: Severity = "P3"
    event_id: int | None = None
    assignee: Short = None


class TicketPatch(_Body):
    revision: int = Field(ge=1)
    changes: TicketFields


class Revision(_Body):
    revision: int = Field(ge=1)


class Assign(_Body):
    revision: int = Field(ge=1)
    assignee: Short = None  # None unassigns
    team: Team | None = None


class StatusChange(_Body):
    revision: int = Field(ge=1)
    status: Status
    root_cause: Long = None
    resolution: Long = None


class Comment(_Body):
    body: Annotated[str, Field(min_length=1, max_length=4000)]
    # internal: ICT only; reporter: also sent to the reporter through the bot
    visibility: Literal["internal", "reporter"] = "internal"


class Merge(_Body):
    revision: int = Field(ge=1)
    into: int = Field(ge=1)


class LiveEvent(_Body):
    event_id: int | None = None


class BotTicket(_Body):
    """What the WhatsApp bot sends. Only external_id and message are required."""

    external_id: Annotated[str, Field(min_length=1, max_length=200)]  # the bot's message/report id
    message: Annotated[str, Field(min_length=1, max_length=4000)]
    event_code: Annotated[str | None, Field(default=None, max_length=20)] = None
    category: Category | None = None
    severity: Severity | None = None
    location: Short = None
    stand: Short = None
    partner: Short = None
    reporter_name: Short = None
    reporter_phone: Annotated[str | None, Field(default=None, max_length=40)] = None
