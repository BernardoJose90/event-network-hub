"""ICT requests: links, deadline, submissions, review; approval creates the requirement row."""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import date, datetime, timezone
from typing import Any

from .. import db
from ..records import service as records, store as records_store
from .models import KINDS, REQUEST_TEAMS, STATUSES, TEAM_DEFAULTS

SCHEMA = """
CREATE TABLE IF NOT EXISTS request_links (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    team         TEXT NOT NULL,
    token        TEXT NOT NULL UNIQUE,
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    revoked_at   TEXT
);

CREATE TABLE IF NOT EXISTS request_settings (
    document_id   INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    deadline      TEXT,   -- YYYY-MM-DD: requests after this day are flagged late
    instructions  TEXT
);

CREATE TABLE IF NOT EXISTS ict_requests (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id        INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    link_id            INTEGER REFERENCES request_links(id) ON DELETE SET NULL,
    batch              TEXT NOT NULL,  -- lines submitted together
    team               TEXT NOT NULL,
    kind               TEXT NOT NULL CHECK (kind IN ('wired', 'wifi')),
    status             TEXT NOT NULL CHECK (status IN ('submitted', 'needs_info', 'approved', 'declined')),
    requester_name     TEXT NOT NULL,
    requester_contact  TEXT NOT NULL,
    data               TEXT NOT NULL,
    late               INTEGER NOT NULL DEFAULT 0,
    submitted_at       TEXT NOT NULL,
    reviewed_by        TEXT,
    reviewed_at        TEXT,
    review_note        TEXT,
    record_id          INTEGER,  -- the Ethernet/Wi-Fi row created on approval
    revision           INTEGER NOT NULL DEFAULT 1,
    updated_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ict_requests_by_doc ON ict_requests (document_id, status);
"""


class RequestError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["data"] = json.loads(out["data"])
    out["late"] = bool(out["late"])
    return out


def _event(conn: sqlite3.Connection, doc: int) -> dict[str, Any]:
    document = db.get_document(conn, doc)
    if document is None or document["kind"] != "event":
        raise RequestError(404, "Event not found")
    return document


# --- settings, links ---------------------------------------------------------------------


def settings(conn: sqlite3.Connection, doc: int) -> dict[str, Any]:
    row = conn.execute("SELECT deadline, instructions FROM request_settings WHERE document_id = ?", (doc,)).fetchone()
    return dict(row) if row else {"deadline": None, "instructions": None}


def save_settings(conn: sqlite3.Connection, doc: int, *, deadline: str | None, instructions: str | None,
                  user: str) -> dict[str, Any]:
    _event(conn, doc)
    with conn:
        conn.execute("INSERT INTO request_settings (document_id, deadline, instructions) VALUES (?, ?, ?) "
                     "ON CONFLICT(document_id) DO UPDATE SET deadline = excluded.deadline, "
                     "instructions = excluded.instructions", (doc, deadline, instructions))
        records_store.log(conn, doc=doc, entity="request", action="request", user=user, new=deadline,
                          summary=f"Set the ICT request deadline to {deadline or 'none'}")
    return settings(conn, doc)


def links(conn: sqlite3.Connection, doc: int) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM request_links WHERE document_id = ? ORDER BY revoked_at IS NOT NULL, team, id", (doc,))]


def create_link(conn: sqlite3.Connection, doc: int, team: str, *, user: str) -> dict[str, Any]:
    _event(conn, doc)
    token = secrets.token_urlsafe(24)  # unguessable: the link is the credential
    with conn:
        link_id = conn.execute("INSERT INTO request_links (document_id, team, token, created_by, created_at) "
                               "VALUES (?, ?, ?, ?, ?)", (doc, team, token, user, db.utcnow())).lastrowid
        records_store.log(conn, doc=doc, entity="request", action="request", user=user,
                          summary=f"Created an ICT request link for {REQUEST_TEAMS[team]}")
    return dict(conn.execute("SELECT * FROM request_links WHERE id = ?", (link_id,)).fetchone())


def revoke_link(conn: sqlite3.Connection, doc: int, link_id: int, *, user: str) -> None:
    row = conn.execute("SELECT * FROM request_links WHERE id = ? AND document_id = ?", (link_id, doc)).fetchone()
    if row is None:
        raise RequestError(404, "Link not found")
    with conn:
        conn.execute("UPDATE request_links SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                     (db.utcnow(), link_id))
        records_store.log(conn, doc=doc, entity="request", action="request", user=user,
                          summary=f"Turned off the ICT request link for {REQUEST_TEAMS.get(row['team'], row['team'])}")


def link_by_token(conn: sqlite3.Connection, token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """(link, event) for an active link, or RequestError 404/410."""
    row = conn.execute("SELECT * FROM request_links WHERE token = ?", (token,)).fetchone()
    if row is None or row["revoked_at"]:
        raise RequestError(404, "This request link isn't valid. Ask the ICT team for a new one.")
    document = db.get_document(conn, row["document_id"])
    if document is None or document["archived_at"]:
        raise RequestError(410, "Requests for this event are closed.")
    return dict(row), document


def is_late(conn: sqlite3.Connection, doc: int, today: date | None = None) -> bool:
    deadline = settings(conn, doc)["deadline"]
    today = today or datetime.now(timezone.utc).date()
    return bool(deadline) and today > date.fromisoformat(deadline)


# --- submissions -----------------------------------------------------------------------------


def _check_line(conn: sqlite3.Connection, doc: int, line: dict[str, Any]) -> dict[str, Any]:
    data = {k: v for k, v in line.items() if v is not None and k != "kind"}
    wired_only = ("device_type", "ports", "poe", "bandwidth")
    wifi_only = ("network_name", "devices", "coverage", "password")
    for field in (wifi_only if line["kind"] == "wired" else wired_only):
        data.pop(field, None)
    if line.get("bandwidth"):
        allowed = {i["value"] for i in records_store.lists(conn, doc) if i["kind"] == "bandwidth"}
        if allowed and line["bandwidth"] not in allowed:
            raise RequestError(422, f"Bandwidth '{line['bandwidth']}' isn't one of the options")
    return data


def submit(conn: sqlite3.Connection, token: str, submission: dict[str, Any]) -> dict[str, Any]:
    link, document = link_by_token(conn, token)
    doc = document["id"]
    late = is_late(conn, doc)
    batch = secrets.token_hex(6)
    now = db.utcnow()
    lines = [(line["kind"], _check_line(conn, doc, line)) for line in submission["lines"]]
    ids = []
    with conn:
        for kind, data in lines:
            ids.append(conn.execute(
                "INSERT INTO ict_requests (document_id, link_id, batch, team, kind, status, requester_name, "
                "requester_contact, data, late, submitted_at, updated_at) VALUES (?, ?, ?, ?, ?, 'submitted', ?, ?, ?, ?, ?, ?)",
                (doc, link["id"], batch, link["team"], kind, submission["requester_name"],
                 submission["requester_contact"], json.dumps(data), int(late), now, now)).lastrowid)
        records_store.log(conn, doc=doc, entity="request", action="request", user=submission["requester_name"],
                          summary=f"{REQUEST_TEAMS[link['team']]} submitted {len(ids)} ICT request(s)"
                                  + (" after the deadline" if late else ""))
    return {"batch": batch, "late": late, "requests": [get(conn, doc, i) for i in ids]}


def get(conn: sqlite3.Connection, doc: int, request_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM ict_requests WHERE id = ? AND document_id = ?", (request_id, doc)).fetchone()
    if row is None:
        raise RequestError(404, "Request not found")
    return _row(row)


def list_for(conn: sqlite3.Connection, doc: int, *, link_id: int | None = None) -> list[dict[str, Any]]:
    sql, args = "SELECT * FROM ict_requests WHERE document_id = ?", [doc]
    if link_id is not None:
        sql += " AND link_id = ?"
        args.append(link_id)
    return [_row(r) for r in conn.execute(sql + " ORDER BY id DESC", args)]


def public_view(req: dict[str, Any]) -> dict[str, Any]:
    """What a requester sees: no reviewer names or internal ids beyond the request number."""
    return {k: req[k] for k in ("id", "kind", "status", "data", "late", "submitted_at", "review_note", "revision",
                                "requester_name")} | {"status_label": STATUSES[req["status"]]}


def resubmit(conn: sqlite3.Connection, token: str, request_id: int, *, revision: int, line: dict[str, Any]
             ) -> dict[str, Any]:
    """The requester answers a 'needs more information' request (or corrects a submitted one)."""
    link, document = link_by_token(conn, token)
    req = get(conn, document["id"], request_id)
    if req["link_id"] != link["id"]:
        raise RequestError(404, "Request not found")
    if req["status"] not in ("submitted", "needs_info"):
        raise RequestError(409, "This request has already been reviewed.")
    if req["revision"] != revision:
        raise RequestError(409, "This request changed since you opened it. Reload the page.")
    data = _check_line(conn, document["id"], line)
    with conn:
        conn.execute("UPDATE ict_requests SET kind = ?, data = ?, status = 'submitted', revision = revision + 1, "
                     "updated_at = ? WHERE id = ?", (line["kind"], json.dumps(data), db.utcnow(), request_id))
        records_store.log(conn, doc=document["id"], entity="request", action="request", user=req["requester_name"],
                          summary=f"Updated ICT request #{request_id}")
    return get(conn, document["id"], request_id)


# --- review -----------------------------------------------------------------------------------


def _open_for_review(conn: sqlite3.Connection, doc: int, request_id: int, revision: int) -> dict[str, Any]:
    db.ensure_not_archived(conn, doc)
    req = get(conn, doc, request_id)
    if req["revision"] != revision:
        raise RequestError(409, "This request changed since you opened it. Reload first.")
    if req["status"] in ("approved", "declined"):
        raise RequestError(409, f"This request is already {req['status']}.")
    return req


def set_review(conn: sqlite3.Connection, doc: int, request_id: int, *, revision: int, status: str, note: str,
               user: str) -> dict[str, Any]:
    req = _open_for_review(conn, doc, request_id, revision)
    with conn:
        conn.execute("UPDATE ict_requests SET status = ?, review_note = ?, reviewed_by = ?, reviewed_at = ?, "
                     "revision = revision + 1, updated_at = ? WHERE id = ?",
                     (status, note, user, db.utcnow(), db.utcnow(), request_id))
        records_store.log(conn, doc=doc, entity="request", action="request", user=user,
                          summary=f"{'Declined' if status == 'declined' else 'Asked for more information on'} "
                                  f"ICT request #{request_id} from {REQUEST_TEAMS.get(req['team'], req['team'])}")
    return get(conn, doc, request_id)


def approve(conn: sqlite3.Connection, doc: int, request_id: int, *, revision: int, venue_id: int, group_id: int,
            vlan: str | None, location: str | None, ports: int | None, name: str | None, note: str | None,
            user: str) -> dict[str, Any]:
    """Create the Ethernet item or Wi-Fi network from the request, then mark it approved."""
    req = _open_for_review(conn, doc, request_id, revision)
    d = req["data"]
    team = REQUEST_TEAMS.get(req["team"], req["team"])
    source = f"ICT request #{request_id} ({team}, {req['requester_name']})"
    if req["kind"] == "wired":
        ports = ports or d.get("ports")
        if not ports:
            raise RequestError(422, "The request didn't say how many ports. Enter the number of Ethernet points.")
        details = [x for x in (d.get("device_type") and f"device: {d['device_type']}",
                               d.get("poe") and f"PoE: {d['poe'].replace('_', ' ')}",
                               d.get("needed_by") and f"needed by {d['needed_by']}", d.get("notes")) if x]
        record_type, data = "ethernet", {
            "purpose": name or f"{team}: {d['purpose']}",
            "location": location or d["area"],
            "vlan": vlan,
            "room": d.get("room"),
            "bandwidth_ns": d.get("bandwidth"),
            "ports": ports,
            "notes": f"{source}" + (f" — {'; '.join(details)}" if details else ""),
        }
    else:
        record_type, data = "wifi", {
            "ssid": name or d.get("network_name") or f"{team} {d['area']}",
            "coverage": d.get("coverage") or d["area"],
            "password_required": d.get("password") or "yes",
            "vlan": vlan,
            "purpose": f"{team}: {d['purpose']} — {source}",
        }
    try:
        record = records.create_record(conn, doc, record_type, venue_id=venue_id, group_id=group_id, before_id=None,
                                       data={k: v for k, v in data.items() if v is not None}, user=user)
    except records.RecordError as exc:
        raise RequestError(exc.status, str(exc))
    with conn:
        conn.execute("UPDATE ict_requests SET status = 'approved', record_id = ?, reviewed_by = ?, reviewed_at = ?, "
                     "review_note = ?, revision = revision + 1, updated_at = ? WHERE id = ?",
                     (record["id"], user, db.utcnow(), note, db.utcnow(), request_id))
        records_store.log(conn, doc=doc, entity="request", action="request", user=user, record_id=record["id"],
                          summary=f"Approved ICT request #{request_id} from {team}: added "
                                  f"{'Ethernet requirement' if record_type == 'ethernet' else 'Wi-Fi network'} "
                                  f"'{data.get('purpose') or data.get('ssid')}'")
    return get(conn, doc, request_id)


# --- summary ---------------------------------------------------------------------------------


def summary(conn: sqlite3.Connection, doc: int) -> dict[str, Any]:
    reqs = list_for(conn, doc)
    by_team = {}
    for key, label in REQUEST_TEAMS.items():
        mine = [r for r in reqs if r["team"] == key]
        wired = [r for r in mine if r["kind"] == "wired"]
        by_team[key] = {
            "label": label,
            "requests": len(mine),
            "open": sum(r["status"] in ("submitted", "needs_info") for r in mine),
            "ports_requested": sum(r["data"].get("ports") or 0 for r in wired),
            "ports_unknown": sum(r["data"].get("ports") is None for r in wired),
            "late": sum(r["late"] for r in mine),
        }
    return {
        "total": len(reqs),
        "by_status": {s: sum(r["status"] == s for r in reqs) for s in STATUSES},
        "late": sum(r["late"] for r in reqs),
        "unknowns": sum(any(r["data"].get(f) in (None, "not_sure") for f in
                            (("ports", "poe", "bandwidth") if r["kind"] == "wired" else ("devices",)))
                        for r in reqs),
        "by_team": by_team,
    }


def reference(conn: sqlite3.Connection, doc: int, team: str) -> dict[str, Any]:
    """What the public form needs: the event's areas and bandwidths, and the team's template."""
    lists = records_store.lists(conn, doc)
    return {
        "areas": sorted({i["value"] for i in lists if i["kind"] in ("location", "building")}, key=str.lower),
        "bandwidths": [i["value"] for i in lists if i["kind"] == "bandwidth"],
        "kinds": KINDS,
        "template": TEAM_DEFAULTS[team],
    }
