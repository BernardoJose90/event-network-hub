from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import Settings
from app.main import create_app
from app.records.importer import import_workbook, values_missing_from_lists

from .conftest import FakeConverter
from .records_fixture import records_workbook

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ADMIN = {"X-Role": "network-admin", "X-User": "alice"}
VIEWER = {"X-Role": "viewer", "X-User": "victor"}


# --- importer -------------------------------------------------------------------


@pytest.fixture(scope="module")
def imported():
    return import_workbook(records_workbook())


def test_venues_groups_and_sections(imported) -> None:
    assert imported.venues == ["Altice Arena", "FIL 1-5"]
    assert imported.groups["wifi"] == ["Core Networks", "Lounges", "Partners"]  # theme-coloured rows included
    assert imported.groups["ethernet"] == ["INTERNAL WORKSPACES", "PARTNER MASTERCLASSES", "Stages"]  # not "Total"


def test_record_counts_and_placement(imported) -> None:
    counts = Counter((r.type, r.venue, r.group) for r in imported.records)
    assert counts[("wifi", "Altice Arena", "Core Networks")] == 2
    assert counts[("wifi", "FIL 1-5", "Partners")] == 1
    assert counts[("ethernet", "Altice Arena", "INTERNAL WORKSPACES")] == 4  # incl. the empty-points Bag Drop row
    assert counts[("ethernet", "Altice Arena", "Stages")] == 2
    assert counts[("partner", None, None)] == 3
    assert counts[("vlan_extension", None, None)] == 2  # the empty row is skipped


def test_values_are_normalised(imported) -> None:
    wifi = next(r for r in imported.records if r.type == "wifi" and r.data.get("ssid") == "#Production")
    assert wifi.data == {"ref_id": "2", "ssid": "#Production", "coverage": "offices", "password_required": "yes",
                         "broadcast": "yes", "vlan": "Operational_Prod", "band": "5 GHz",
                         "password": "StaffPass1!", "purpose": "Staff Network"}
    eth = next(r for r in imported.records if r.type == "ethernet" and r.data.get("purpose") == "Stage 1")
    assert eth.data["ports"] == 13 and eth.data["location"] == "Mystery Hall"
    assert eth.data["bandwidth_ns"] == "50 Mbps" and eth.data["bandwidth_ew"] == "1 Gbps"
    ibm = next(r for r in imported.records if r.type == "partner" and r.data["partner"] == "IBM")
    assert (ibm.data["purchased"], ibm.data["bandwidth_mbps"], ibm.data["pavilion"]) == ("yes", 60, "1")
    vlan = next(r for r in imported.records if r.type == "vlan_extension")
    assert vlan.data == {"vlan": "Operational_Prod", "venues": {"FIL 1-5": True, "Altice Arena": True}}


def test_warnings_and_lists(imported) -> None:
    joined = " | ".join(imported.warnings)
    assert "Scratch notes" in joined
    assert "'maybe' is not a valid broadcast" in joined
    assert "'tbc' is not a number of points" in joined
    assert "'fifty' is not a number" in joined
    assert "placeholder or note row" in joined
    assert imported.lists["vlan"] == ["General", "Operational_Prod", "Operations_Stages", "Partner_Custom"]
    assert imported.lists["bandwidth"] == ["10Mbs", "20Mbs"]
    assert values_missing_from_lists(imported) == {"vlan": ["Operational_Catering"], "location": ["Mystery Hall"]}
    assert len(imported.legacy_log) == 2 and "Executive lounge" in imported.legacy_log[0]
    assert "=D2-C2" not in imported.legacy_log[0]


# --- API ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, gotenberg_url="http://unused", gotenberg_timeout_seconds=1,
                        max_upload_bytes=1024 * 1024)
    app = create_app(settings)
    with TestClient(app) as c:
        app.state.converter = FakeConverter()
        c.settings = settings
        yield c


@pytest.fixture
def event(client: TestClient) -> int:
    r = client.post("/api/documents/upload", headers=ADMIN,
                    files={"file": ("LIS26 _ Event_Network_Requirements_V1.xlsx", records_workbook(), XLSX)})
    doc_id = r.json()["document"]["id"]
    r = client.post(f"/api/events/{doc_id}/import", headers=ADMIN, json={})
    assert r.status_code == 200, r.text
    return doc_id


def _records(client, doc, rtype, headers=ADMIN):
    return client.get(f"/api/events/{doc}/records/{rtype}", headers=headers).json()


def _find(client, doc, rtype, **match):
    return next(r for r in _records(client, doc, rtype) if all(r["data"].get(k) == v for k, v in match.items()))


def test_import_result_and_overview(client: TestClient, event: int) -> None:
    ov = client.get(f"/api/events/{event}/overview", headers=VIEWER).json()
    assert ov["has_records"] and ov["counts"] == {"wifi": 4, "ethernet": 7, "partner": 3, "vlan_extension": 2}
    assert ov["flagged_list_values"] == 2
    altice = ov["totals"]["ethernet"][0]
    assert altice["venue"] == "Altice Arena"
    assert [(s["section"], s["ports"]) for s in altice["sections"]] == [
        ("INTERNAL WORKSPACES", 111), ("PARTNER MASTERCLASSES", 0), ("Stages", 13)]
    assert altice["total"] == 124
    ext = _records(client, event, "vlan_extension")[0]["data"]
    venue_ids = {v["name"]: str(v["id"]) for v in ov["venues"]}
    assert ext["venues"] == {venue_ids["FIL 1-5"]: True, venue_ids["Altice Arena"]: True}
    legacy = client.get(f"/api/events/{event}/history?entity=legacy", headers=VIEWER).json()
    assert len(legacy) == 2


def test_import_twice_needs_replace(client: TestClient, event: int) -> None:
    assert client.post(f"/api/events/{event}/import", headers=ADMIN, json={}).status_code == 409
    r = client.post(f"/api/events/{event}/import", headers=ADMIN, json={"replace": True})
    assert r.status_code == 200
    assert client.get(f"/api/events/{event}/overview", headers=ADMIN).json()["counts"]["ethernet"] == 7


def test_passwords_are_masked_and_reveals_logged(client: TestClient, event: int) -> None:
    wifi = _find(client, event, "wifi", ssid="#Production")
    assert "password" not in wifi["data"] and wifi["has_password"] is True
    r = client.post(f"/api/events/{event}/records/wifi/{wifi['id']}/reveal", headers=VIEWER)
    assert r.json() == {"password": "StaffPass1!"}
    entry = client.get(f"/api/events/{event}/history", headers=ADMIN).json()[0]
    assert (entry["action"], entry["user"], entry["record_id"]) == ("reveal", "victor", wifi["id"])


def test_viewers_cannot_change_anything(client: TestClient, event: int) -> None:
    eth = _find(client, event, "ethernet", purpose="Photographers Office")
    base = f"/api/events/{event}"
    attempts = [
        client.post(f"{base}/records/ethernet", headers=VIEWER, json={"data": {}}),
        client.patch(f"{base}/records/ethernet/{eth['id']}", headers=VIEWER, json={"revision": 1, "data": {}}),
        client.delete(f"{base}/records/ethernet/{eth['id']}?revision=1", headers=VIEWER),
        client.post(f"{base}/lists/vlan", headers=VIEWER, json={"value": "X"}),
        client.post(f"{base}/import", headers=VIEWER, json={"replace": True}),
    ]
    assert [a.status_code for a in attempts] == [403] * 5
    assert client.get(f"{base}/records/ethernet", headers={}).status_code == 403  # no role at all


def test_create_update_delete_are_logged(client: TestClient, event: int) -> None:
    ov = client.get(f"/api/events/{event}/overview", headers=ADMIN).json()
    venue = ov["venues"][0]["id"]
    section = next(g["id"] for g in ov["groups"] if g["kind"] == "ethernet" and g["name"] == "Stages")
    r = client.post(f"/api/events/{event}/records/ethernet", headers=ADMIN, json={
        "venue_id": venue, "group_id": section, "data": {"purpose": "Stage 3", "vlan": "General", "ports": 5}})
    assert r.status_code == 201, r.text
    created = r.json()
    totals = client.get(f"/api/events/{event}/overview", headers=ADMIN).json()["totals"]["ethernet"][0]
    assert totals["sections"][2]["ports"] == 18

    r = client.patch(f"/api/events/{event}/records/ethernet/{created['id']}", headers=ADMIN,
                     json={"revision": created["revision"], "data": {"ports": 7, "notes": "BOH"}})
    assert r.status_code == 200 and r.json()["revision"] == created["revision"] + 1
    r = client.delete(f"/api/events/{event}/records/ethernet/{created['id']}?revision={r.json()['revision']}",
                      headers=ADMIN)
    assert r.status_code == 204

    log = client.get(f"/api/events/{event}/history?entity=ethernet", headers=ADMIN).json()
    assert [(e["action"], e["field"], e["old"], e["new"]) for e in log] == [
        ("delete", None, '{"purpose": "Stage 3", "vlan": "General", "ports": 7, "notes": "BOH"}', None),
        ("update", "ports", "5", "7"),
        ("update", "notes", None, "BOH"),
        ("create", None, None, None),
    ]
    assert log[1]["summary"] == "Changed Ethernet Points of Ethernet requirement 'Stage 3'"


def test_password_changes_are_logged_without_values(client: TestClient, event: int) -> None:
    wifi = _find(client, event, "wifi", ssid="#Production")
    client.patch(f"/api/events/{event}/records/wifi/{wifi['id']}", headers=ADMIN,
                 json={"revision": wifi["revision"], "data": {"password": "NewSecret9"}})
    entry = client.get(f"/api/events/{event}/history?entity=wifi", headers=ADMIN).json()[0]
    assert (entry["field"], entry["old"], entry["new"]) == ("password", None, None)
    assert "NewSecret9" not in str(client.get(f"/api/events/{event}/history", headers=ADMIN).json())


def test_validation(client: TestClient, event: int) -> None:
    ov = client.get(f"/api/events/{event}/overview", headers=ADMIN).json()
    venue = ov["venues"][0]["id"]
    section = next(g["id"] for g in ov["groups"] if g["kind"] == "ethernet")
    base = f"/api/events/{event}/records/ethernet"

    def create(data, **extra):
        return client.post(base, headers=ADMIN, json={"venue_id": venue, "group_id": section, "data": data, **extra})

    r = create({"purpose": "X", "vlan": "Not_A_Vlan"})
    assert r.status_code == 422 and "not in the vlan list" in r.json()["detail"]
    r = create({"purpose": "X", "ports": "many"})
    assert r.status_code == 422 and r.json()["detail"].startswith("Ethernet Points")
    r = create({"purpose": "X", "unknown_field": 1})
    assert r.status_code == 422
    r = client.post(base, headers=ADMIN, json={"data": {"purpose": "X"}})  # no venue/section
    assert r.status_code == 422
    r = client.post(f"/api/events/{event}/records/partner", headers=ADMIN,
                    json={"venue_id": venue, "data": {"partner": "X"}})
    assert r.status_code == 422


def test_stale_revision_is_rejected(client: TestClient, event: int) -> None:
    eth = _find(client, event, "ethernet", purpose="Photographers Office")
    url = f"/api/events/{event}/records/ethernet/{eth['id']}"
    assert client.patch(url, headers=ADMIN, json={"revision": eth["revision"], "data": {"ports": 3}}).status_code == 200
    r = client.patch(url, headers={"X-Role": "network-admin", "X-User": "bob"},
                     json={"revision": eth["revision"], "data": {"ports": 9}})
    assert r.status_code == 409 and "alice changed this record" in r.json()["detail"]
    assert client.delete(f"{url}?revision={eth['revision']}", headers=ADMIN).status_code == 409


def test_insert_before_and_move(client: TestClient, event: int) -> None:
    rows = [r for r in _records(client, event, "ethernet") if r["data"].get("purpose") in ("Photographers Office", "PCT Event Control")]
    first, second = rows
    r = client.post(f"/api/events/{event}/records/ethernet", headers=ADMIN, json={
        "venue_id": first["venue_id"], "group_id": first["group_id"], "before_id": second["id"],
        "data": {"purpose": "Inserted"}})
    names = [x["data"].get("purpose") for x in _records(client, event, "ethernet") if x["group_id"] == first["group_id"]
             and x["venue_id"] == first["venue_id"]]
    assert names[:3] == ["Photographers Office", "Inserted", "PCT Event Control"]
    inserted = r.json()
    r = client.post(f"/api/events/{event}/records/ethernet/{inserted['id']}/move", headers=ADMIN,
                    json={"revision": inserted["revision"], "before_id": first["id"]})
    assert r.status_code == 200
    names = [x["data"].get("purpose") for x in _records(client, event, "ethernet") if x["group_id"] == first["group_id"]
             and x["venue_id"] == first["venue_id"]]
    assert names[:2] == ["Inserted", "Photographers Office"]


def test_lists(client: TestClient, event: int) -> None:
    base = f"/api/events/{event}/lists"
    lists = client.get(f"/api/events/{event}/overview", headers=ADMIN).json()["lists"]
    general = next(i for i in lists if i["kind"] == "vlan" and i["value"] == "General")
    flagged = next(i for i in lists if i["flagged"])

    assert client.delete(f"{base}/item/{general['id']}", headers=ADMIN).status_code == 409  # in use
    r = client.patch(f"{base}/item/{general['id']}", headers=ADMIN, json={"value": "General_Access"})
    assert r.status_code == 200
    assert _find(client, event, "wifi", ssid="#WebSummit")["data"]["vlan"] == "General_Access"
    assert client.post(f"{base}/vlan", headers=ADMIN, json={"value": "General_Access"}).status_code == 409
    assert client.post(f"{base}/vlan", headers=ADMIN, json={"value": "Guest"}).status_code == 201
    r = client.post(f"{base}/item/{flagged['id']}/accept", headers=ADMIN)
    assert not next(i for i in r.json() if i["id"] == flagged["id"])["flagged"]


def test_venues_and_sections(client: TestClient, event: int) -> None:
    ov = client.get(f"/api/events/{event}/overview", headers=ADMIN).json()
    altice = ov["venues"][0]
    assert client.delete(f"/api/events/{event}/venues/{altice['id']}", headers=ADMIN).status_code == 409
    r = client.post(f"/api/events/{event}/groups/ethernet", headers=ADMIN, json={"name": "Media Areas"})
    media = next(g for g in r.json() if g["name"] == "Media Areas")
    r = client.patch(f"/api/events/{event}/groups/{media['id']}", headers=ADMIN, json={"name": "Media"})
    assert any(g["name"] == "Media" for g in r.json())
    assert client.delete(f"/api/events/{event}/groups/{media['id']}", headers=ADMIN).status_code == 200


def test_deleting_the_document_removes_its_records(client: TestClient, event: int) -> None:
    client.delete(f"/api/documents/{event}", headers=ADMIN)
    conn = db.connect(client.settings.db_path)
    counts = [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("records", "event_lists", "event_venues", "event_groups", "change_log")]
    conn.close()
    assert counts == [0, 0, 0, 0, 0]
