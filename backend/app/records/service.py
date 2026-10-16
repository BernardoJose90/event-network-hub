"""Rules for event records: list checks, revisions, ordering, change log, totals."""

from __future__ import annotations

import sqlite3
from typing import Any

from pydantic import ValidationError

from . import store
from .models import (
    BAND_OPTIONS, GROUP_KIND, LABELS, LIST_FIELDS, SECRET_FIELDS, TYPE_LABELS, VENUE_TYPES, validate_data,
)


class RecordError(Exception):
    """A user-facing problem; ``status`` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _describe(record_type: str, data: dict[str, Any]) -> str:
    key = {"wifi": "ssid", "ethernet": "purpose", "partner": "partner", "vlan_extension": "vlan"}[record_type]
    name = data.get(key) or data.get("ref_id") or "(unnamed)"
    return f"{TYPE_LABELS[record_type]} '{name}'"


def public(record: dict[str, Any]) -> dict[str, Any]:
    """A record as the API returns it: secrets removed, replaced by has_<field> flags."""
    data = dict(record["data"])
    flags = {}
    for field in SECRET_FIELDS.get(record["type"], ()):
        flags[f"has_{field}"] = bool(data.pop(field, None))
    return {k: record.get(k) for k in ("id", "type", "venue_id", "group_id", "sort", "revision", "updated_by",
                                       "updated_at", "archived_at", "archived_by")} | {"data": data} | flags


def _list_values(conn: sqlite3.Connection, doc: int) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for item in store.lists(conn, doc):
        out.setdefault(item["kind"], set()).add(item["value"])
    return out


def _check_lists(conn: sqlite3.Connection, doc: int, record_type: str, data: dict[str, Any],
                 fields: set[str] | None = None) -> None:
    allowed = _list_values(conn, doc)
    for field, kinds in LIST_FIELDS[record_type].items():
        if fields is not None and field not in fields:
            continue
        value = data.get(field)
        if value is None:
            continue
        if not any(value in allowed.get(k, ()) for k in kinds):
            names = " or ".join(kinds)
            raise RecordError(422, f"'{value}' is not in the {names} list. Add it under Setup first.")


def _is_template(conn: sqlite3.Connection, doc: int) -> bool:
    row = conn.execute("SELECT kind FROM documents WHERE id = ?", (doc,)).fetchone()
    return row is not None and row["kind"] == "template"


def _check_rules(conn: sqlite3.Connection, doc: int, record_type: str, data: dict[str, Any],
                 fields: set[str] | None = None) -> None:
    """Field rules beyond the schema, for new values only (fields=None: all fields).

    Band: one of BAND_OPTIONS. Templates: Ethernet points stay 0 and notes stay empty.
    """
    def changing(field: str) -> bool:
        return fields is None or field in fields

    if record_type == "wifi" and changing("band") and data.get("band") not in (None, *BAND_OPTIONS):
        raise RecordError(422, f"Band must be {' or '.join(BAND_OPTIONS)}")
    if record_type == "ethernet" and _is_template(conn, doc):
        if changing("ports") and data.get("ports") not in (None, 0):
            raise RecordError(422, "Templates always have 0 Ethernet points. Set the points on the event.")
        if changing("notes") and data.get("notes"):
            raise RecordError(422, "Notes stay empty in templates. Add notes on the event.")


def _validate(record_type: str, data: dict[str, Any]) -> dict[str, Any]:
    try:
        return validate_data(record_type, data)
    except ValidationError as exc:
        err = exc.errors()[0]
        loc = err.get("loc", ())
        field = str(loc[0]) if loc else "data"
        label = LABELS[record_type].get(field, field)
        message = err.get("msg", "invalid value")
        if len(loc) > 1 and str(loc[1]).startswith("constrained-"):  # number fields (int | float)
            message = "must be a number, 0 or more"
        raise RecordError(422, f"{label}: {message}")


def _place(conn: sqlite3.Connection, doc: int, record_type: str, venue_id: int | None, group_id: int | None) -> None:
    if record_type in VENUE_TYPES:
        if venue_id is None or not any(v["id"] == venue_id for v in store.venues(conn, doc)):
            raise RecordError(422, "Choose a venue for this record")
        if group_id is None or not any(g["id"] == group_id for g in store.groups(conn, doc, GROUP_KIND[record_type])):
            raise RecordError(422, "Choose a section for this record")
    elif venue_id is not None or group_id is not None:
        raise RecordError(422, f"{TYPE_LABELS[record_type]}s don't belong to a venue or section")


def _sort_before(conn: sqlite3.Connection, doc: int, record_type: str, venue_id: int | None,
                 group_id: int | None, before_id: int | None) -> float:
    """Sort key placing a record before ``before_id`` (or at the end) among its siblings."""
    siblings = [r for r in store.records(conn, doc, record_type)
                if r["venue_id"] == venue_id and r["group_id"] == group_id]
    if before_id is None:
        return (siblings[-1]["sort"] + 1) if siblings else 1.0
    for i, r in enumerate(siblings):
        if r["id"] == before_id:
            prev = siblings[i - 1]["sort"] if i > 0 else r["sort"] - 1
            return (prev + r["sort"]) / 2
    raise RecordError(422, "The record to insert before is not in this section")


def create_record(conn: sqlite3.Connection, doc: int, record_type: str, *, venue_id: int | None,
                  group_id: int | None, before_id: int | None, data: dict[str, Any], user: str) -> dict[str, Any]:
    data = _validate(record_type, data)
    _place(conn, doc, record_type, venue_id, group_id)
    _check_lists(conn, doc, record_type, data)
    _check_rules(conn, doc, record_type, data)
    if record_type == "ethernet" and _is_template(conn, doc):
        data["ports"] = 0
    with conn:
        sort = _sort_before(conn, doc, record_type, venue_id, group_id, before_id)
        record_id = store.insert_record(conn, doc=doc, record_type=record_type, venue_id=venue_id,
                                        group_id=group_id, sort=sort, data=data, user=user)
        store.log(conn, doc=doc, entity=record_type, record_id=record_id, action="create", user=user,
                  summary=f"Added {_describe(record_type, data)}")
    return store.get_record(conn, doc, record_id)  # type: ignore[return-value]


def _existing(conn: sqlite3.Connection, doc: int, record_id: int, record_type: str) -> dict[str, Any]:
    record = store.get_record(conn, doc, record_id)
    if record is None or record["type"] != record_type:
        raise RecordError(404, "Record not found")
    return record


def _conflict(record: dict[str, Any]) -> RecordError:
    return RecordError(409, f"{record['updated_by']} changed this record at {record['updated_at']}. "
                            "Reload to see the latest values.")


def _active(record: dict[str, Any]) -> None:
    if record.get("archived_at"):
        raise RecordError(409, "This record is archived. Restore it before changing it.")


def update_record(conn: sqlite3.Connection, doc: int, record_type: str, record_id: int, *, revision: int,
                  changes: dict[str, Any], user: str) -> dict[str, Any]:
    record = _existing(conn, doc, record_id, record_type)
    if record["revision"] != revision:
        raise _conflict(record)
    _active(record)
    merged = dict(record["data"])
    for field, value in changes.items():
        merged[field] = value
    data = _validate(record_type, merged)
    changed = {f for f in set(record["data"]) | set(data) if record["data"].get(f) != data.get(f)}
    if not changed:
        return record
    _check_lists(conn, doc, record_type, data, changed)
    _check_rules(conn, doc, record_type, data, changed & set(changes))  # only what the user sent
    secrets = SECRET_FIELDS.get(record_type, frozenset())
    with conn:
        if not store.update_record(conn, record_id=record_id, expected_revision=revision, user=user, data=data):
            raise _conflict(store.get_record(conn, doc, record_id) or record)
        venue_names = {str(v["id"]): v["name"] for v in store.venues(conn, doc)}
        for field in sorted(changed):
            if field == "venues":  # VLAN extension ticks: one readable entry per venue
                before, after = record["data"].get("venues", {}), data.get("venues", {})
                for venue_id in sorted(set(before) | set(after)):
                    was, now = bool(before.get(venue_id)), bool(after.get(venue_id))
                    if was == now:
                        continue
                    name = venue_names.get(venue_id, "a removed venue")
                    store.log(conn, doc=doc, entity=record_type, record_id=record_id, action="update", user=user,
                              field=f"venue:{name}", old="yes" if was else "no", new="yes" if now else "no",
                              summary=f"{'Ticked' if now else 'Unticked'} {name} for {_describe(record_type, data)}")
                continue
            label = LABELS[record_type].get(field, field)
            hidden = field in secrets
            store.log(conn, doc=doc, entity=record_type, record_id=record_id, action="update", user=user,
                      field=field, old=None if hidden else record["data"].get(field),
                      new=None if hidden else data.get(field),
                      summary=f"Changed {label} of {_describe(record_type, data)}")
    return store.get_record(conn, doc, record_id)  # type: ignore[return-value]


def set_archived(conn: sqlite3.Connection, doc: int, record_type: str, record_id: int, *, revision: int,
                 archived: bool, user: str) -> dict[str, Any]:
    """Archive (hide from lists, totals and publishing) or restore a record."""
    record = _existing(conn, doc, record_id, record_type)
    if record["revision"] != revision:
        raise _conflict(record)
    if bool(record.get("archived_at")) == archived:
        return record
    if not archived:  # its values must still be valid (e.g. a VLAN removed from the list meanwhile)
        _check_lists(conn, doc, record_type, record["data"])
    with conn:
        if not store.set_archived(conn, record_id=record_id, expected_revision=revision, user=user, archived=archived):
            raise _conflict(store.get_record(conn, doc, record_id) or record)
        store.log(conn, doc=doc, entity=record_type, record_id=record_id, user=user,
                  action="archive" if archived else "restore",
                  summary=f"{'Archived' if archived else 'Restored'} {_describe(record_type, record['data'])}")
    return store.get_record(conn, doc, record_id)  # type: ignore[return-value]


def delete_record(conn: sqlite3.Connection, doc: int, record_type: str, record_id: int, *, revision: int,
                  user: str) -> None:
    """Permanently delete an archived record (active records are archived instead)."""
    record = _existing(conn, doc, record_id, record_type)
    if not record.get("archived_at"):
        raise RecordError(409, "Archive the record before deleting it permanently.")
    with conn:
        if not store.delete_record(conn, record_id, revision):
            raise _conflict(store.get_record(conn, doc, record_id) or record)
        shown = {k: v for k, v in record["data"].items() if k not in SECRET_FIELDS.get(record_type, ())}
        store.log(conn, doc=doc, entity=record_type, record_id=record_id, action="delete", user=user,
                  old=shown, summary=f"Permanently deleted {_describe(record_type, record['data'])}")


def move_record(conn: sqlite3.Connection, doc: int, record_type: str, record_id: int, *, revision: int,
                before_id: int | None, group_id: int | None, user: str) -> dict[str, Any]:
    record = _existing(conn, doc, record_id, record_type)
    if record["revision"] != revision:
        raise _conflict(record)
    _active(record)
    target_group = group_id if group_id is not None else record["group_id"]
    _place(conn, doc, record_type, record["venue_id"], target_group)
    if before_id == record_id:
        return record
    with conn:
        sort = _sort_before(conn, doc, record_type, record["venue_id"], target_group, before_id)
        if not store.update_record(conn, record_id=record_id, expected_revision=revision, user=user,
                                   group_id=target_group, sort=sort):
            raise _conflict(store.get_record(conn, doc, record_id) or record)
        store.log(conn, doc=doc, entity=record_type, record_id=record_id, action="move", user=user,
                  summary=f"Moved {_describe(record_type, record['data'])}")
    return store.get_record(conn, doc, record_id)  # type: ignore[return-value]


def reveal_secret(conn: sqlite3.Connection, doc: int, record_id: int, *, user: str) -> str | None:
    record = _existing(conn, doc, record_id, "wifi")
    with conn:
        store.log(conn, doc=doc, entity="wifi", record_id=record_id, action="reveal", user=user,
                  summary=f"Revealed the password of {_describe('wifi', record['data'])}")
    return record["data"].get("password")


# --- lists, venues, groups ---------------------------------------------------


def _list_usage(conn: sqlite3.Connection, doc: int, kind: str, value: str) -> list[tuple[dict, str]]:
    used = []
    for record in store.records(conn, doc, archived=None):  # archived records keep their values too
        for field, kinds in LIST_FIELDS[record["type"]].items():
            if kind in kinds and record["data"].get(field) == value:
                used.append((record, field))
    return used


def add_list_value(conn: sqlite3.Connection, doc: int, kind: str, value: str, *, user: str) -> None:
    value = value.strip()
    if not value:
        raise RecordError(422, "The value must not be blank")
    if any(i["kind"] == kind and i["value"] == value for i in store.lists(conn, doc)):
        raise RecordError(409, f"'{value}' is already in the list")
    with conn:
        store.insert_list_value(conn, doc, kind, value)
        store.log(conn, doc=doc, entity="list", action="list", user=user, new=value,
                  summary=f"Added '{value}' to the {kind} list")


def _list_item(conn: sqlite3.Connection, doc: int, item_id: int) -> dict[str, Any]:
    item = next((i for i in store.lists(conn, doc) if i["id"] == item_id), None)
    if item is None:
        raise RecordError(404, "List value not found")
    return item


def rename_list_value(conn: sqlite3.Connection, doc: int, item_id: int, value: str, *, user: str) -> None:
    """Rename a list value and every record field that uses it."""
    item, value = _list_item(conn, doc, item_id), value.strip()
    if not value:
        raise RecordError(422, "The value must not be blank")
    if value == item["value"]:
        return
    if any(i["kind"] == item["kind"] and i["value"] == value for i in store.lists(conn, doc)):
        raise RecordError(409, f"'{value}' is already in the list")
    with conn:
        conn.execute("UPDATE event_lists SET value = ?, flagged = 0 WHERE id = ?", (value, item_id))
        for record, field in _list_usage(conn, doc, item["kind"], item["value"]):
            data = dict(record["data"]) | {field: value}
            store.update_record(conn, record_id=record["id"], expected_revision=record["revision"], user=user,
                                data=data)
        store.log(conn, doc=doc, entity="list", action="list", user=user, old=item["value"], new=value,
                  summary=f"Renamed '{item['value']}' to '{value}' in the {item['kind']} list and its records")


def delete_list_value(conn: sqlite3.Connection, doc: int, item_id: int, *, user: str) -> None:
    item = _list_item(conn, doc, item_id)
    used = _list_usage(conn, doc, item["kind"], item["value"])
    if used:
        raise RecordError(409, f"'{item['value']}' is used by {len(used)} record(s). Change them first.")
    with conn:
        conn.execute("DELETE FROM event_lists WHERE id = ?", (item_id,))
        store.log(conn, doc=doc, entity="list", action="list", user=user, old=item["value"],
                  summary=f"Removed '{item['value']}' from the {item['kind']} list")


def accept_list_value(conn: sqlite3.Connection, doc: int, item_id: int, *, user: str) -> None:
    """Clear the 'added by import, please review' flag."""
    _list_item(conn, doc, item_id)
    with conn:
        conn.execute("UPDATE event_lists SET flagged = 0 WHERE id = ?", (item_id,))


def add_named(conn: sqlite3.Connection, doc: int, what: str, name: str, *, user: str, kind: str | None = None) -> int:
    name = name.strip()
    existing = store.venues(conn, doc) if what == "venue" else store.groups(conn, doc, kind)
    if any(x["name"] == name for x in existing):
        raise RecordError(409, f"'{name}' already exists")
    with conn:
        new_id = store.insert_venue(conn, doc, name) if what == "venue" else store.insert_group(conn, doc, kind, name)
        store.log(conn, doc=doc, entity=what, action=what, user=user, new=name, summary=f"Added {what} '{name}'")
    return new_id


def rename_named(conn: sqlite3.Connection, doc: int, what: str, item_id: int, name: str, *, user: str) -> None:
    table = "event_venues" if what == "venue" else "event_groups"
    items = store.venues(conn, doc) if what == "venue" else store.groups(conn, doc)
    item = next((x for x in items if x["id"] == item_id), None)
    if item is None:
        raise RecordError(404, f"{what.capitalize()} not found")
    name = name.strip()
    if any(x["name"] == name and x["id"] != item_id and x.get("kind") == item.get("kind") for x in items):
        raise RecordError(409, f"'{name}' already exists")
    with conn:
        conn.execute(f"UPDATE {table} SET name = ? WHERE id = ?", (name, item_id))
        store.log(conn, doc=doc, entity=what, action=what, user=user, old=item["name"], new=name,
                  summary=f"Renamed {what} '{item['name']}' to '{name}'")


def delete_named(conn: sqlite3.Connection, doc: int, what: str, item_id: int, *, user: str) -> None:
    table = "event_venues" if what == "venue" else "event_groups"
    column = "venue_id" if what == "venue" else "group_id"
    items = store.venues(conn, doc) if what == "venue" else store.groups(conn, doc)
    item = next((x for x in items if x["id"] == item_id), None)
    if item is None:
        raise RecordError(404, f"{what.capitalize()} not found")
    count = conn.execute(f"SELECT COUNT(*) FROM records WHERE {column} = ?", (item_id,)).fetchone()[0]
    if count:
        raise RecordError(409, f"'{item['name']}' still has {count} record(s), including archived ones. "
                               "Move or permanently delete them first.")
    with conn:
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (item_id,))
        store.log(conn, doc=doc, entity=what, action=what, user=user, old=item["name"],
                  summary=f"Removed {what} '{item['name']}'")


# --- totals ------------------------------------------------------------------


def totals(conn: sqlite3.Connection, doc: int) -> dict[str, Any]:
    """Ethernet points per venue and section (always computed, never stored)."""
    sections = store.groups(conn, doc, "ethernet")
    by_venue: dict[int, dict[int, float]] = {}
    for r in store.records(conn, doc, "ethernet"):
        ports = r["data"].get("ports") or 0
        by_venue.setdefault(r["venue_id"], {}).setdefault(r["group_id"], 0)
        by_venue[r["venue_id"]][r["group_id"]] += ports

    def tidy(x: float) -> int | float:
        return int(x) if float(x).is_integer() else x

    out = []
    for venue in store.venues(conn, doc):
        per = by_venue.get(venue["id"], {})
        out.append({
            "venue_id": venue["id"], "venue": venue["name"],
            "sections": [{"group_id": s["id"], "section": s["name"], "ports": tidy(per.get(s["id"], 0))}
                         for s in sections],
            "total": tidy(sum(per.values())),
        })
    return {"ethernet": out}


# --- import ------------------------------------------------------------------


def apply_import(conn: sqlite3.Connection, doc: int, result, *, user: str, source: str,
                 replace: bool = False) -> dict[str, Any]:
    """Write an ImportResult as the event's records (one transaction)."""
    from .importer import values_missing_from_lists

    if store.has_records(conn, doc) and not replace:
        raise RecordError(409, "This event already has requirements. Confirm to replace them with the import.")
    if not result.records:
        raise RecordError(422, "No requirements were found in this workbook")
    missing = values_missing_from_lists(result)
    with conn:
        store.clear_event(conn, doc)
        venue_ids = {name: store.insert_venue(conn, doc, name) for name in result.venues}
        group_ids = {(kind, name): store.insert_group(conn, doc, kind, name)
                     for kind, names in result.groups.items() for name in names}
        for kind, values in result.lists.items():
            for value in values:
                store.insert_list_value(conn, doc, kind, value)
        for kind, values in missing.items():
            for value in values:
                store.insert_list_value(conn, doc, kind, value, flagged=True)

        sort = 0.0
        skipped = 0
        for rec in result.records:
            try:
                data = validate_data(rec.type, rec.data)
            except ValidationError:
                skipped += 1
                continue
            if rec.type == "vlan_extension":
                data["venues"] = {str(venue_ids[v]): on for v, on in rec.data.get("venues", {}).items() if v in venue_ids}
            sort += 1
            store.insert_record(
                conn, doc=doc, record_type=rec.type,
                venue_id=venue_ids.get(rec.venue) if rec.venue else None,
                group_id=group_ids.get((GROUP_KIND.get(rec.type), rec.group)) if rec.group else None,
                sort=sort, data=data, user=user,
            )
        for line in result.legacy_log:
            store.log(conn, doc=doc, entity="legacy", action="legacy", user=user, summary=line)
        warnings = list(result.warnings)
        if skipped:
            warnings.append(f"{skipped} row(s) had invalid values and were not imported")
        for kind, values in missing.items():
            warnings.append(f"{len(values)} {kind} value(s) used by records were missing from the lists; "
                            "added and flagged for review")
        store.log(conn, doc=doc, entity="event", action="import", user=user,
                  summary=f"Imported requirements from {source}")
    counts: dict[str, int] = {}
    for rec in store.records(conn, doc):
        counts[rec["type"]] = counts.get(rec["type"], 0) + 1
    return {"counts": counts, "warnings": warnings}


# --- new events and templates ---------------------------------------------------

# The sections every event workbook has used, for events started empty.
DEFAULT_GROUPS = {
    "wifi": ("Core Networks", "Lounges", "Security Zones", "Partners"),
    "ethernet": ("INTERNAL WORKSPACES", "PARTNER MASTERCLASSES", "PARTNER MEETING ROOMS", "Stages",
                 "Registration Areas", "LOUNGES", "STANDARD PARTNER STANDS", "STARTUP STANDS", "Other Areas",
                 "Media Areas"),
}


def _template_data(record: dict[str, Any]) -> dict[str, Any] | None:
    """A record as kept in a template: no partner orders, Ethernet points 0 and notes empty,
    no Wi-Fi passwords."""
    if record["type"] == "partner":
        return None
    data = dict(record["data"])
    if record["type"] == "ethernet":
        data["ports"] = 0
        data.pop("notes", None)
    for field in SECRET_FIELDS.get(record["type"], ()):
        data.pop(field, None)
    return data


def _copy_event(conn: sqlite3.Connection, source: int, target: int, *, user: str, as_template: bool) -> int:
    """Copy venues, groups, lists and active records from source into target (inside a transaction)."""
    venue_ids = {v["id"]: store.insert_venue(conn, target, v["name"]) for v in store.venues(conn, source)}
    group_ids = {g["id"]: store.insert_group(conn, target, g["kind"], g["name"]) for g in store.groups(conn, source)}
    for item in store.lists(conn, source):
        store.insert_list_value(conn, target, item["kind"], item["value"], flagged=item["flagged"])
    copied = 0
    for r in store.records(conn, source):  # active records only
        data = _template_data(r) if as_template else dict(r["data"])
        if data is None:
            continue
        if r["type"] == "vlan_extension":
            data["venues"] = {str(venue_ids[int(v)]): on for v, on in data.get("venues", {}).items()
                              if int(v) in venue_ids}
        store.insert_record(conn, doc=target, record_type=r["type"], venue_id=venue_ids.get(r["venue_id"]),
                            group_id=group_ids.get(r["group_id"]), sort=r["sort"], data=data, user=user)
        copied += 1
    return copied


def _source(conn: sqlite3.Connection, doc: int, kind: str) -> dict[str, Any]:
    from ..db import get_document

    source = get_document(conn, doc)
    if source is None or source["kind"] != kind:
        raise RecordError(404, f"{'Template' if kind == 'template' else 'Event'} not found")
    return source


def _unused(conn: sqlite3.Connection, name: str) -> None:
    from ..db import get_document_by_name

    if get_document_by_name(conn, name) is not None:
        raise RecordError(409, f"'{name}' already exists")


def create_event(conn: sqlite3.Connection, name: str, *, user: str, template_id: int | None = None) -> int:
    """Create an event document without a workbook, in one transaction.

    Empty: one default venue and the usual Wi-Fi groups and Ethernet sections.
    template_id: everything in that template. Returns the new document id.
    """
    from ..db import insert_document
    from .importer import DEFAULT_VENUE

    _unused(conn, name)
    template = _source(conn, template_id, "template") if template_id is not None else None
    with conn:
        doc = insert_document(conn, name)
        if template is None:
            store.insert_venue(conn, doc, DEFAULT_VENUE)
            for kind, names in DEFAULT_GROUPS.items():
                for group in names:
                    store.insert_group(conn, doc, kind, group)
            summary = "Created the event (empty)"
        else:
            copied = _copy_event(conn, template["id"], doc, user=user, as_template=False)
            summary = f"Created the event from template {template['name']} ({copied} requirement(s))"
        store.log(conn, doc=doc, entity="event", action="create", user=user, summary=summary)
    return doc


def save_template(conn: sqlite3.Connection, name: str, *, user: str, event_id: int) -> int:
    """Save an event as a template: setup, Wi-Fi, Ethernet (0 points) and VLAN extensions;
    no partner orders and no Wi-Fi passwords. Returns the template's document id."""
    from ..db import insert_document

    event = _source(conn, event_id, "event")
    if not store.venues(conn, event_id):
        raise RecordError(422, f"'{event['name']}' has no requirements to save as a template")
    _unused(conn, name)
    with conn:
        doc = insert_document(conn, name, kind="template")
        copied = _copy_event(conn, event_id, doc, user=user, as_template=True)
        store.log(conn, doc=doc, entity="event", action="create", user=user,
                  summary=f"Created the template from {event['name']} ({copied} requirement(s); Ethernet points "
                          "set to 0, Ethernet notes, partner orders and Wi-Fi passwords left out)")
    return doc
