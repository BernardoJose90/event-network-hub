"""What viewers see: the latest published version, in the same shape as the live requirements.

Viewers comment on what was released, so the overview and record lists they get come from the
snapshot saved at publish. Network admins keep working on the live draft. Records keep their real
IDs (comments attach to them); venues and sections are matched to the live ones by name, and ones
that no longer exist get negative IDs. Before anything is published, viewers see the draft.
"""

from __future__ import annotations

from typing import Any

from .. import db
from . import service, store


def latest(conn: db.Connection, doc: int) -> dict[str, Any] | None:
    """The latest published version as {version, published_at, venues, groups, lists, records, totals,
    counts}, or None if nothing has been published."""
    found = store.previous_snapshot(conn, doc)
    if found is None:
        return None
    number, snap = found
    version = db.get_version(conn, doc, number)
    synthetic = iter(range(-1, -1_000_000, -1))

    live_venues = {v["name"]: v["id"] for v in store.venues(conn, doc)}
    venues = [{"id": live_venues.get(name) or next(synthetic), "name": name, "sort": i}
              for i, name in enumerate(snap.get("venues", []))]
    venue_id = {v["name"]: v["id"] for v in venues}

    live_groups = {(g["kind"], g["name"]): g["id"] for g in store.groups(conn, doc)}
    groups = [{"id": live_groups.get((kind, name)) or next(synthetic), "kind": kind, "name": name, "sort": i}
              for kind in ("ethernet", "wifi") for i, name in enumerate(snap.get("groups", {}).get(kind, []))]
    group_id = {(g["kind"], g["name"]): g["id"] for g in groups}

    lists = [{"id": next(synthetic), "kind": kind, "value": value, "sort": i, "flagged": False}
             for kind, values in snap.get("lists", {}).items() for i, value in enumerate(values)]

    records, counts = [], {}
    for i, r in enumerate(snap.get("records", [])):
        if r["type"] not in ("wifi", "ethernet", "partner"):
            continue  # retired types
        kind = {"wifi": "wifi", "ethernet": "ethernet"}.get(r["type"])
        records.append({
            "id": r["id"], "type": r["type"], "venue_id": venue_id.get(r.get("venue")),
            "group_id": group_id.get((kind, r.get("group"))) if kind else None, "sort": i, "revision": 0,
            "updated_by": None, "updated_at": None, "data": r["data"],
        })
        counts[r["type"]] = counts.get(r["type"], 0) + 1

    ethernet_groups = [g for g in groups if g["kind"] == "ethernet"]
    totals = []
    for v in venues:
        per = {g["id"]: 0 for g in ethernet_groups}
        for r in records:
            if r["type"] == "ethernet" and r["venue_id"] == v["id"] and r["group_id"] in per:
                per[r["group_id"]] += r["data"].get("ports") or 0
        tidy = lambda x: int(x) if float(x).is_integer() else x  # noqa: E731
        totals.append({"venue_id": v["id"], "venue": v["name"],
                       "sections": [{"group_id": g["id"], "section": g["name"], "ports": tidy(per[g["id"]])}
                                    for g in ethernet_groups],
                       "total": tidy(sum(per.values()))})

    return {"version": number, "published_at": version["uploaded_at"] if version else None,
            "venues": venues, "groups": groups, "lists": lists, "records": records,
            "totals": {"ethernet": totals}, "counts": counts}


def records(view: dict[str, Any], record_type: str) -> list[dict[str, Any]]:
    """Published records of one type as the API returns them (passwords replaced by has_password)."""
    return [service.public(r) for r in view["records"] if r["type"] == record_type]


def wifi(view: dict[str, Any], record_id: int) -> dict[str, Any] | None:
    """A published Wi-Fi network (with its password), or None."""
    return next((r for r in view["records"] if r["id"] == record_id and r["type"] == "wifi"), None)
