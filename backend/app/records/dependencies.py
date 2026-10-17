"""Event network dependency register.

For each area or space: what depends on the network there, the team or process
behind it, how critical it is, who is accountable (RACI: exactly one Accountable),
which network elements it uses (VLANs, Wi-Fi networks, Ethernet items) and what
to do if it fails. Entries are copied with templates and new events, and carry a
"last reviewed" date so stale knowledge is visible (service configuration
management: the register is only useful while it matches reality).
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..db import utcnow
from . import store

SCHEMA = """
CREATE TABLE IF NOT EXISTS event_dependencies (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id       INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    sort              REAL NOT NULL,
    data              TEXT NOT NULL,
    revision          INTEGER NOT NULL DEFAULT 1,
    updated_by        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    last_reviewed_by  TEXT,
    last_reviewed_at  TEXT
);
CREATE INDEX IF NOT EXISTS event_dependencies_by_doc ON event_dependencies (document_id, sort);
"""

# Entries not reviewed for this long are flagged as needing review.
REVIEW_AFTER_DAYS = 180

# Suggestions for the dependent team/process (free text is allowed).
TEAM_SUGGESTIONS = ("AV", "Photographers & Videographers", "Engineering", "CCTV / Security", "Registration",
                    "Broadcast & streaming", "Production", "Catering & payments", "Partners", "Media",
                    "Speakers & stages", "Event app")

Text = Annotated[str | None, Field(default=None, max_length=200)]
Long = Annotated[str | None, Field(default=None, max_length=4000)]

LABELS = {
    "area": "Area / space", "service": "What depends on the network", "team": "Team or process",
    "criticality": "Criticality", "accountable": "Accountable", "responsible": "Responsible (ICT)",
    "consulted": "Consulted", "informed": "Informed", "phase": "Needed during", "vlans": "VLANs",
    "records": "Network items", "runbook": "If it fails", "contact": "On-site contact", "notes": "Notes",
}


class DependencyData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    area: Annotated[str, Field(min_length=1, max_length=200)]
    service: Annotated[str, Field(min_length=1, max_length=200)]
    team: Text = None
    criticality: Literal["P1", "P2", "P3", "P4"] = "P3"
    accountable: Text = None  # exactly one owner (RACI "A")
    responsible: Literal["network", "it_support"] | None = None  # the ICT team that keeps it working
    consulted: Text = None
    informed: Text = None
    phase: list[Literal["build", "live", "breakdown"]] = Field(default_factory=lambda: ["live"])
    vlans: list[Annotated[str, Field(max_length=200)]] = Field(default_factory=list, max_length=20)
    records: list[int] = Field(default_factory=list, max_length=50)  # Wi-Fi network / Ethernet item ids
    runbook: Long = None
    contact: Text = None
    notes: Long = None

    @field_validator("area", "service", "team", "accountable", "consulted", "informed", "runbook", "contact",
                     "notes", mode="before")
    @classmethod
    def _strip(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v

    @field_validator("vlans", "phase", mode="after")
    @classmethod
    def _unique(cls, v: list) -> list:
        return list(dict.fromkeys(v))


class DependencyError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["data"] = json.loads(out["data"])
    return out


def needs_review(dep: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if not dep["last_reviewed_at"]:
        return True
    return datetime.fromisoformat(dep["last_reviewed_at"]) < now - timedelta(days=REVIEW_AFTER_DAYS)


def view(dep: dict[str, Any]) -> dict[str, Any]:
    return dep | {"needs_review": needs_review(dep), "missing_owner": not dep["data"].get("accountable")}


def list_for(conn: sqlite3.Connection, doc: int) -> list[dict[str, Any]]:
    return [_row(r) for r in conn.execute(
        "SELECT * FROM event_dependencies WHERE document_id = ? ORDER BY sort, id", (doc,))]


def get(conn: sqlite3.Connection, doc: int, dep_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM event_dependencies WHERE document_id = ? AND id = ?", (doc, dep_id)).fetchone()
    if row is None:
        raise DependencyError(404, "Dependency not found")
    return _row(row)


def _validate(conn: sqlite3.Connection, doc: int, data: dict[str, Any]) -> dict[str, Any]:
    try:
        clean = DependencyData.model_validate(data).model_dump(exclude_none=True)
    except ValidationError as exc:
        err = exc.errors()[0]
        field = str(err["loc"][0]) if err.get("loc") else "data"
        raise DependencyError(422, f"{LABELS.get(field, field)}: {err.get('msg', 'invalid value')}")
    vlans = {i["value"] for i in store.lists(conn, doc) if i["kind"] == "vlan"}
    unknown = [v for v in clean.get("vlans", []) if v not in vlans]
    if unknown:
        raise DependencyError(422, f"VLAN '{unknown[0]}' is not in this event's VLAN list. Add it under Setup first.")
    if clean.get("records"):
        ids = {r["id"] for r in store.records(conn, doc) if r["type"] in ("wifi", "ethernet")}
        missing = [i for i in clean["records"] if i not in ids]
        if missing:
            raise DependencyError(422, "Linked network items must be Wi-Fi networks or Ethernet items of this event")
    return clean


def _describe(data: dict[str, Any]) -> str:
    return f"dependency '{data['service']}' in {data['area']}"


def create(conn: sqlite3.Connection, doc: int, data: dict[str, Any], *, user: str) -> dict[str, Any]:
    clean = _validate(conn, doc, data)
    now = utcnow()
    with conn:
        sort = conn.execute("SELECT COALESCE(MAX(sort), 0) + 1 FROM event_dependencies WHERE document_id = ?",
                            (doc,)).fetchone()[0]
        dep_id = conn.execute(
            "INSERT INTO event_dependencies (document_id, sort, data, updated_by, updated_at, last_reviewed_by, "
            "last_reviewed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (doc, sort, json.dumps(clean), user, now, user, now)).lastrowid
        store.log(conn, doc=doc, entity="dependency", record_id=dep_id, action="create", user=user,
                  summary=f"Added {_describe(clean)}")
    return get(conn, doc, dep_id)


def update(conn: sqlite3.Connection, doc: int, dep_id: int, *, revision: int, data: dict[str, Any],
           user: str) -> dict[str, Any]:
    """Replace an entry's data. Saving counts as reviewing it."""
    dep = get(conn, doc, dep_id)
    if dep["revision"] != revision:
        raise DependencyError(409, f"{dep['updated_by']} changed this entry at {dep['updated_at']}. Reload first.")
    clean = _validate(conn, doc, data)
    changed = sorted(f for f in set(dep["data"]) | set(clean) if dep["data"].get(f) != clean.get(f))
    now = utcnow()
    with conn:
        cur = conn.execute(
            "UPDATE event_dependencies SET data = ?, revision = revision + 1, updated_by = ?, updated_at = ?, "
            "last_reviewed_by = ?, last_reviewed_at = ? WHERE id = ? AND revision = ?",
            (json.dumps(clean), user, now, user, now, dep_id, revision))
        if cur.rowcount != 1:
            raise DependencyError(409, "This entry was changed by someone else. Reload first.")
        for field in changed:
            store.log(conn, doc=doc, entity="dependency", record_id=dep_id, action="update", user=user, field=field,
                      old=dep["data"].get(field), new=clean.get(field),
                      summary=f"Changed {LABELS.get(field, field)} of {_describe(clean)}")
    return get(conn, doc, dep_id)


def mark_reviewed(conn: sqlite3.Connection, doc: int, dep_id: int, *, revision: int, user: str) -> dict[str, Any]:
    dep = get(conn, doc, dep_id)
    if dep["revision"] != revision:
        raise DependencyError(409, "This entry was changed by someone else. Reload first.")
    with conn:
        conn.execute("UPDATE event_dependencies SET revision = revision + 1, last_reviewed_by = ?, "
                     "last_reviewed_at = ? WHERE id = ?", (user, utcnow(), dep_id))
        store.log(conn, doc=doc, entity="dependency", record_id=dep_id, action="review", user=user,
                  summary=f"Confirmed {_describe(dep['data'])} is still correct")
    return get(conn, doc, dep_id)


def delete(conn: sqlite3.Connection, doc: int, dep_id: int, *, revision: int, user: str) -> None:
    dep = get(conn, doc, dep_id)
    with conn:
        if conn.execute("DELETE FROM event_dependencies WHERE id = ? AND revision = ?",
                        (dep_id, revision)).rowcount != 1:
            raise DependencyError(409, "This entry was changed by someone else. Reload first.")
        store.log(conn, doc=doc, entity="dependency", record_id=dep_id, action="delete", user=user,
                  old=dep["data"], summary=f"Removed {_describe(dep['data'])}")


def copy(conn: sqlite3.Connection, source: int, target: int, record_ids: dict[int, int], *, user: str) -> int:
    """Copy the register into another event/template (inside the caller's transaction).
    Links to network items follow the copied items; links to items left out are dropped."""
    now = utcnow()
    copied = 0
    for dep in list_for(conn, source):
        data = dict(dep["data"])
        data["records"] = [record_ids[i] for i in data.get("records", []) if i in record_ids]
        conn.execute(
            "INSERT INTO event_dependencies (document_id, sort, data, updated_by, updated_at, last_reviewed_by, "
            "last_reviewed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (target, dep["sort"], json.dumps(data), user, now, dep["last_reviewed_by"], dep["last_reviewed_at"]))
        copied += 1
    return copied


def using_vlan(conn: sqlite3.Connection, doc: int, vlan: str) -> list[dict[str, Any]]:
    return [d for d in list_for(conn, doc) if vlan in d["data"].get("vlans", [])]


def rename_vlan(conn: sqlite3.Connection, doc: int, old: str, new: str) -> None:
    """Follow a VLAN rename in the Setup list (inside the caller's transaction)."""
    for dep in using_vlan(conn, doc, old):
        data = dict(dep["data"]) | {"vlans": [new if v == old else v for v in dep["data"]["vlans"]]}
        conn.execute("UPDATE event_dependencies SET data = ?, revision = revision + 1 WHERE id = ?",
                     (json.dumps(data), dep["id"]))


def _norm(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def for_area(conn: sqlite3.Connection, doc: int, *areas: str | None) -> list[dict[str, Any]]:
    """Entries whose area matches any of the given areas (a ticket's location or stand)."""
    wanted = {_norm(a) for a in areas if _norm(a)}
    if not wanted:
        return []
    out = []
    for dep in list_for(conn, doc):
        area = _norm(dep["data"]["area"])
        if area in wanted or any(w and (w in area or area in w) for w in wanted):
            out.append(dep)
    return out
