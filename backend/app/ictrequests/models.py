"""ICT requests: standard request models (ITIL request catalogue) for internal teams.

Two request types — a wired connection and a private Wi-Fi network — submitted per
workspace through a shareable link per team and event. "Not sure" is a valid answer
(GOV.UK: don't make people guess); unknowns show up as gaps for ICT to follow up.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

REQUEST_TEAMS = {
    "av": "AV",
    "photo_video": "Photographers & Videographers",
    "engineering": "Engineering",
    "cctv": "CCTV",
}

KINDS = {"wired": "Wired connection", "wifi": "Private Wi-Fi network"}

STATUSES = {"submitted": "Submitted", "needs_info": "Needs more information", "approved": "Approved",
            "declined": "Declined"}

# Team templates: what each team usually connects, and its usual power-over-Ethernet answer.
TEAM_DEFAULTS = {
    "av": {"devices": ["Video encoder", "Audio mixer", "Streaming PC", "Show control laptop", "Video wall controller"],
           "poe": "no"},
    "photo_video": {"devices": ["Upload laptop", "Camera transmitter", "Editing workstation", "Media server"],
                    "poe": "no"},
    "engineering": {"devices": ["Laptop", "IP phone", "Printer", "Building system controller"], "poe": "not_sure"},
    "cctv": {"devices": ["IP camera", "NVR / recorder", "Access control panel"], "poe": "yes"},
}

Text = Annotated[str | None, Field(default=None, max_length=200)]
Long = Annotated[str | None, Field(default=None, max_length=2000)]
Team = Literal["av", "photo_video", "engineering", "cctv"]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def _strip(cls, v):
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v


class RequestLine(_Body):
    """One workspace's need. Wired fields: device_type, ports, poe, bandwidth.
    Wi-Fi fields: network_name, devices, coverage, password."""

    kind: Literal["wired", "wifi"]
    area: Annotated[str, Field(min_length=1, max_length=200)]
    room: Text = None
    purpose: Annotated[str, Field(min_length=1, max_length=200)]
    needed_by: Annotated[str | None, Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")] = None
    notes: Long = None
    # wired
    device_type: Text = None
    ports: Annotated[int | None, Field(default=None, ge=1, le=500)] = None  # None = not sure yet
    poe: Literal["yes", "no", "not_sure"] | None = None
    bandwidth: Text = None  # a value from the event's bandwidth list, or None = not sure
    # Wi-Fi
    network_name: Text = None
    devices: Annotated[int | None, Field(default=None, ge=1, le=5000)] = None
    coverage: Text = None
    password: Literal["yes", "no"] | None = None


class Submission(_Body):
    requester_name: Annotated[str, Field(min_length=1, max_length=200)]
    requester_contact: Annotated[str, Field(min_length=3, max_length=200)]  # email or phone
    lines: Annotated[list[RequestLine], Field(min_length=1, max_length=30)]


class LineUpdate(_Body):
    revision: int = Field(ge=1)
    line: RequestLine


class NewLink(_Body):
    team: Team


class Settings(_Body):
    deadline: Annotated[str | None, Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")] = None
    instructions: Long = None


class Review(_Body):
    revision: int = Field(ge=1)
    note: Annotated[str, Field(min_length=1, max_length=2000)]


class Approval(_Body):
    revision: int = Field(ge=1)
    venue_id: int
    group_id: int  # Ethernet section or Wi-Fi group
    vlan: Text = None
    location: Text = None  # override the requested area (must be in the event's lists)
    ports: Annotated[int | None, Field(default=None, ge=1, le=500)] = None  # required if the request said "not sure"
    name: Text = None  # Ethernet purpose / Wi-Fi SSID override
    note: Long = None
