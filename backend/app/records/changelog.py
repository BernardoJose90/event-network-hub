"""Item-level changelog between two published versions (or a version and the current draft).

Compares snapshots, so it shows the net result: five edits to one item become one
"changed" line, and a change that was undone doesn't appear. Wi-Fi password values
never appear, only that the password changed.
"""

from __future__ import annotations

from typing import Any

from .models import LABELS, SECRET_FIELDS

# Fields stored as codes (yes/no/tbc) that read better as words.
CHOICE_FIELDS = {"wifi": ("password_required", "broadcast"), "partner": ("purchased",)}

TYPES = [("wifi", "Wi-Fi networks"), ("ethernet", "Ethernet requirements"), ("partner", "Partner orders")]

NAME_FIELD = {"wifi": "ssid", "ethernet": "purpose", "partner": "partner"}

# Not compared: IDs are assigned automatically and never change.
IGNORED = {"ref_id"}


# Versions read from a workbook have no record IDs, so items are matched on what identifies
# them instead. Changing one of these fields shows as removed + added.
def _natural_key(r: dict[str, Any]) -> tuple:
    d = r["data"]
    if r["type"] == "wifi":
        return (r.get("venue"), d.get("ssid"))
    if r["type"] == "ethernet":
        return (r.get("venue"), r.get("group"), d.get("purpose"), d.get("location"), d.get("room"))
    return (d.get("partner"), d.get("stand"))


def _has_ids(snap: dict[str, Any] | None) -> bool:
    return all(r.get("id") is not None for r in (snap or {}).get("records", []))


def _items(snap: dict[str, Any] | None, by_content: bool = False) -> dict[tuple[str, Any], dict[str, Any]]:
    out: dict[tuple[str, Any], dict[str, Any]] = {}
    if not snap:
        return out
    seen: dict[tuple, int] = {}
    for r in snap.get("records", []):
        if r["type"] not in NAME_FIELD:
            continue  # retired types (VLAN extensions)
        if by_content:
            key = (r["type"], *_natural_key(r))
            seen[key] = seen.get(key, 0) + 1
            key = (*key, seen[key])  # the nth item with the same identity
        else:
            key = (r["type"], r.get("id"))
        out[key] = {
            "type": r["type"], "data": r["data"], "venue": r.get("venue"), "group": r.get("group"),
        }
    return out


def _name(item: dict[str, Any]) -> str:
    data = item["data"]
    return data.get(NAME_FIELD[item["type"]]) or data.get("ref_id") or data.get("location") or "(unnamed)"


def _where(item: dict[str, Any]) -> str:
    return " · ".join(x for x in (item["venue"], item["group"]) if x)


def _label(record_type: str, field: str) -> str:
    return LABELS.get(record_type, {}).get(field, field)


def _show(record_type: str, field: str, value: Any) -> str:
    if value is None or value == "" or value == []:
        return "—"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if field in CHOICE_FIELDS.get(record_type, ()) and isinstance(value, str):
        return {"yes": "Yes", "no": "No", "tbc": "TBC", "open network": "Open network"}.get(value, value)
    return str(value)


def _summary(item: dict[str, Any]) -> str:
    d = item["data"]
    t = item["type"]
    if t == "ethernet":
        bits = [f"{d['ports']} point{'s' if d.get('ports') != 1 else ''}" if d.get("ports") is not None else None,
                d.get("location"), d.get("vlan")]
    elif t == "wifi":
        bits = [d.get("vlan"), d.get("band"), d.get("coverage")]
    else:
        bits = [d.get("stand") and f"stand {d['stand']}", d.get("solution"),
                d.get("total_ports") is not None and f"{d['total_ports']} ports"]
    return " · ".join(str(b) for b in bits if b)


def _field_changes(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, str]]:
    t = new["type"]
    secrets = SECRET_FIELDS.get(t, ())
    out = []
    if (old["venue"], old["group"]) != (new["venue"], new["group"]):
        out.append({"field": "location", "label": "Moved", "old": _where(old) or "—", "new": _where(new) or "—"})
    for field in sorted(set(old["data"]) | set(new["data"]), key=lambda f: list(new["data"]).index(f)
                        if f in new["data"] else 999):
        if field in IGNORED:
            continue
        a, b = old["data"].get(field), new["data"].get(field)
        if a == b or (a in (None, "", []) and b in (None, "", [])):
            continue
        if field in secrets:
            out.append({"field": field, "label": _label(t, field), "old": "", "new": "password changed"})
            continue
        out.append({"field": field, "label": _label(t, field), "old": _show(t, field, a), "new": _show(t, field, b)})
    return out


def diff(old: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    """What changed from snapshot ``old`` (None for the first version) to snapshot ``new``."""
    by_content = not (_has_ids(old) and _has_ids(new))
    before, after = _items(old, by_content), _items(new, by_content)
    sections = []
    counts = {"added": 0, "removed": 0, "changed": 0}
    for record_type, title in TYPES:
        added = [after[k] for k in after if k[0] == record_type and k not in before]
        removed = [before[k] for k in before if k[0] == record_type and k not in after]
        changed = []
        for k in after:
            if k[0] == record_type and k in before:
                fields = _field_changes(before[k], after[k])
                if fields:
                    changed.append({"name": _name(after[k]), "where": _where(after[k]), "fields": fields})
        if not (added or removed or changed):
            continue
        counts["added"] += len(added)
        counts["removed"] += len(removed)
        counts["changed"] += len(changed)
        sections.append({
            "type": record_type, "title": title,
            "added": [{"name": _name(i), "where": _where(i), "summary": _summary(i)} for i in added],
            "removed": [{"name": _name(i), "where": _where(i), "summary": _summary(i)} for i in removed],
            "changed": changed,
        })

    def totals(snap):
        return {t["venue"]: t["total"] for t in (snap or {}).get("totals", {}).get("ethernet", [])}

    old_totals, new_totals = totals(old), totals(new)
    points = [{"venue": v, "old": old_totals.get(v), "new": new_totals.get(v)}
              for v in list(dict.fromkeys([*new_totals, *old_totals]))
              if old is None or old_totals.get(v) != new_totals.get(v)]
    return {
        "first": old is None,
        "note": new.get("note"),
        "counts": counts,
        "items": len(after),
        "sections": sections,
        "points": points,
    }


def lines(log: dict[str, Any]) -> list[tuple[str, str, str, str, str]]:
    """Flat rows (change, type, item, where, details) for the Excel sheet."""
    rows = []
    for s in log["sections"]:
        for i in s["added"]:
            rows.append(("Added", s["title"], i["name"], i["where"], i["summary"]))
        for i in s["removed"]:
            rows.append(("Removed", s["title"], i["name"], i["where"], i["summary"]))
        for i in s["changed"]:
            rows.append(("Changed", s["title"], i["name"], i["where"],
                         "; ".join(f"{f['label']}: {f['old']} → {f['new']}" if f["old"] else f"{f['label']}: {f['new']}"
                                   for f in i["fields"])))
    for p in log["points"]:
        rows.append(("Total", "Ethernet points", p["venue"], "", f"{p['old'] if p['old'] is not None else '—'} → {p['new']}"))
    return rows
