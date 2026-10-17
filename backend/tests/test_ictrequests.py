from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import Settings
from app.main import create_app

from .conftest import FakeConverter
from .records_fixture import records_workbook

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ADMIN = {"X-Role": "network-admin", "X-User": "alice"}
VIEWER = {"X-Role": "viewer", "X-User": "victor"}


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, gotenberg_url="http://unused", gotenberg_timeout_seconds=1,
                        max_upload_bytes=1024 * 1024, app_public_url="https://hub.example")
    app = create_app(settings)
    with TestClient(app) as c:
        app.state.converter = FakeConverter()
        c.settings = settings
        yield c


@pytest.fixture
def event(client: TestClient) -> int:
    r = client.post("/api/documents/upload", headers=ADMIN,
                    files={"file": ("LIS26 _ Event_Network_Requirements_V1.xlsx", records_workbook(), XLSX)})
    doc = r.json()["document"]["id"]
    assert client.post(f"/api/events/{doc}/import", headers=ADMIN, json={}).status_code == 200
    return doc


def _link(client, doc, team="av") -> dict:
    r = client.post(f"/api/events/{doc}/ict-requests/links", headers=ADMIN, json={"team": team})
    assert r.status_code == 201, r.text
    return r.json()


def _area(client, doc) -> str:
    lists = client.get(f"/api/events/{doc}/overview", headers=ADMIN).json()["lists"]
    return next(i["value"] for i in lists if i["kind"] == "location")


WIRED = {"kind": "wired", "purpose": "Stream encoders", "device_type": "Video encoder", "ports": 4, "poe": "no"}
WIFI = {"kind": "wifi", "purpose": "Camera uploads", "network_name": "AV_Uploads", "devices": 12, "password": "yes"}


def _submit(client, token, *lines, name="Ana"):
    return client.post(f"/api/request-links/{token}/requests",
                       json={"requester_name": name, "requester_contact": "ana@example.com", "lines": list(lines)})


def test_links_and_public_form(client: TestClient, event: int) -> None:
    assert client.post(f"/api/events/{event}/ict-requests/links", headers=VIEWER, json={"team": "av"}).status_code == 403
    assert client.post(f"/api/events/{event}/ict-requests/links", headers=ADMIN, json={"team": "x"}).status_code == 422
    link = _link(client, event, "cctv")
    assert link["url"] == f"https://hub.example/request/{link['token']}" and len(link["token"]) >= 32
    form = client.get(f"/api/request-links/{link['token']}").json()
    assert (form["event"], form["team"]["label"], form["late"]) == ("LIS26", "CCTV", False)
    assert form["template"]["poe"] == "yes" and "IP camera" in form["template"]["devices"]
    assert form["areas"] and form["bandwidths"]
    assert client.get("/api/request-links/" + "x" * 32).status_code == 404

    client.post(f"/api/events/{event}/ict-requests/links/{link['id']}/revoke", headers=ADMIN)
    assert client.get(f"/api/request-links/{link['token']}").status_code == 404

    tpl = client.post("/api/templates", headers=ADMIN, json={"name": "LIS", "event_id": event}).json()["id"]
    assert client.post(f"/api/events/{tpl}/ict-requests/links", headers=ADMIN, json={"team": "av"}).status_code == 404


def _unpublished(client, doc) -> int:
    return client.get(f"/api/events/{doc}/overview", headers=ADMIN).json()["unpublished_changes"]


def test_submit_validate_and_late(client: TestClient, event: int) -> None:
    baseline = _unpublished(client, event)
    token = _link(client, event)["token"]
    area = _area(client, event)
    r = _submit(client, token, WIRED | {"area": area, "ports": None, "poe": "not_sure"}, WIFI | {"area": area})
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body["requests"]) == 2 and not body["late"]
    assert body["requests"][1]["data"].get("ports") is None  # "not sure" is allowed
    assert "reviewed_by" not in body["requests"][0]  # requesters don't see reviewer details

    assert _submit(client, token).status_code == 422  # no lines
    assert _submit(client, token, WIRED | {"area": area, "bandwidth": "9 Tbps"}).status_code == 422
    assert _submit(client, token, WIRED | {"area": area, "colour": "red"}).status_code == 422
    assert _submit(client, token, WIRED | {"area": ""}).status_code == 422

    client.put(f"/api/events/{event}/ict-requests/settings", headers=ADMIN, json={"deadline": "2000-01-01"})
    late = _submit(client, token, WIRED | {"area": area}).json()
    assert late["late"] and client.get(f"/api/request-links/{token}").json()["late"]

    ov = client.get(f"/api/events/{event}/ict-requests", headers=VIEWER).json()
    assert ov["summary"]["total"] == 3 and ov["summary"]["late"] == 1 and ov["summary"]["unknowns"] == 2  # no ports, no bandwidth
    assert ov["summary"]["by_team"]["av"]["ports_requested"] == 4 and ov["summary"]["by_team"]["av"]["ports_unknown"] == 1
    # submissions are not changes to the requirements
    assert _unpublished(client, event) == baseline


def _placement(client, doc, kind):
    ov = client.get(f"/api/events/{doc}/overview", headers=ADMIN).json()
    return ov["venues"][0]["id"], next(g["id"] for g in ov["groups"] if g["kind"] == kind)


def test_approve_creates_rows(client: TestClient, event: int) -> None:
    token = _link(client, event)["token"]
    area = _area(client, event)
    wired, unsure, wifi = _submit(client, token, WIRED | {"area": area, "room": "B12"},
                                  WIRED | {"area": area, "ports": None}, WIFI | {"area": area}).json()["requests"]
    venue, section = _placement(client, event, "ethernet")
    url = f"/api/events/{event}/ict-requests"
    baseline = _unpublished(client, event)
    before = len(client.get(f"/api/events/{event}/records/ethernet", headers=ADMIN).json())

    r = client.post(f"{url}/{wired['id']}/approve", headers=VIEWER, json={"revision": 1, "venue_id": venue, "group_id": section})
    assert r.status_code == 403
    r = client.post(f"{url}/{wired['id']}/approve", headers=ADMIN,
                    json={"revision": wired["revision"], "venue_id": venue, "group_id": section, "vlan": "General"})
    assert r.status_code == 200, r.text
    approved = r.json()
    assert approved["status"] == "approved" and approved["record_id"]
    rows = client.get(f"/api/events/{event}/records/ethernet", headers=ADMIN).json()
    row = next(x for x in rows if x["id"] == approved["record_id"])
    assert len(rows) == before + 1
    assert (row["data"]["purpose"], row["data"]["location"], row["data"]["ports"], row["data"]["room"]) == (
        "AV: Stream encoders", area, 4, "B12")
    assert f"ICT request #{wired['id']} (AV, Ana)" in row["data"]["notes"] and "device: Video encoder" in row["data"]["notes"]
    again = client.post(f"{url}/{wired['id']}/approve", headers=ADMIN,
                        json={"revision": approved["revision"], "venue_id": venue, "group_id": section})
    assert again.status_code == 409

    r = client.post(f"{url}/{unsure['id']}/approve", headers=ADMIN,
                    json={"revision": unsure["revision"], "venue_id": venue, "group_id": section})
    assert r.status_code == 422 and "how many ports" in r.json()["detail"]
    r = client.post(f"{url}/{unsure['id']}/approve", headers=ADMIN,
                    json={"revision": unsure["revision"], "venue_id": venue, "group_id": section, "ports": 2,
                          "location": "Nowhere Hall"})
    assert r.status_code == 422 and "Nowhere Hall" in r.json()["detail"]  # areas must be in Setup

    venue, group = _placement(client, event, "wifi")
    r = client.post(f"{url}/{wifi['id']}/approve", headers=ADMIN,
                    json={"revision": wifi["revision"], "venue_id": venue, "group_id": group})
    net = next(x for x in client.get(f"/api/events/{event}/records/wifi", headers=ADMIN).json()
               if x["id"] == r.json()["record_id"])
    assert (net["data"]["ssid"], net["data"]["password_required"]) == ("AV_Uploads", "yes")
    # the approvals are requirement changes, ready to publish
    assert _unpublished(client, event) == baseline + 2


def test_needs_info_resubmit_and_decline(client: TestClient, event: int) -> None:
    token = _link(client, event, "photo_video")["token"]
    area = _area(client, event)
    first, second = _submit(client, token, WIRED | {"area": area, "ports": None}, WIFI | {"area": area}).json()["requests"]
    url = f"/api/events/{event}/ict-requests"
    r = client.post(f"{url}/{first['id']}/needs-info", headers=ADMIN,
                    json={"revision": first["revision"], "note": "How many cameras?"})
    assert r.json()["status"] == "needs_info"
    mine = client.get(f"/api/request-links/{token}").json()["requests"]
    asked = next(x for x in mine if x["id"] == first["id"])
    assert asked["status_label"] == "Needs more information" and asked["review_note"] == "How many cameras?"

    r = client.put(f"/api/request-links/{token}/requests/{first['id']}",
                   json={"revision": asked["revision"], "line": WIRED | {"area": area, "ports": 6}})
    assert r.status_code == 200 and r.json()["status"] == "submitted" and r.json()["data"]["ports"] == 6
    other = _link(client, event, "cctv")["token"]
    assert client.put(f"/api/request-links/{other}/requests/{first['id']}",
                      json={"revision": 3, "line": WIRED | {"area": area}}).status_code == 404

    assert client.post(f"{url}/{second['id']}/decline", headers=ADMIN, json={"revision": 1, "note": ""}).status_code == 422
    r = client.post(f"{url}/{second['id']}/decline", headers=ADMIN,
                    json={"revision": second["revision"], "note": "Use the media Wi-Fi"})
    assert r.json()["status"] == "declined"
    assert client.put(f"/api/request-links/{token}/requests/{second['id']}",
                      json={"revision": r.json()["revision"], "line": WIFI | {"area": area}}).status_code == 409
    history = client.get(f"/api/events/{event}/history?entity=request", headers=ADMIN).json()
    assert any("Declined ICT request" in e["summary"] for e in history)


def test_archived_event_closes_requests(client: TestClient, event: int) -> None:
    token = _link(client, event)["token"]
    client.post(f"/api/documents/{event}/archive", headers=ADMIN)
    assert client.get(f"/api/request-links/{token}").status_code == 410
    assert _submit(client, token, WIRED | {"area": "x"}).status_code == 410
    assert client.post(f"/api/events/{event}/ict-requests/links", headers=ADMIN, json={"team": "av"}).status_code == 409


def test_deleting_the_event_removes_requests(client: TestClient, event: int) -> None:
    token = _link(client, event)["token"]
    _submit(client, token, WIRED | {"area": _area(client, event)})
    client.post(f"/api/documents/{event}/archive", headers=ADMIN)
    client.delete(f"/api/documents/{event}", headers=ADMIN)
    conn = db.connect(client.settings.db_path)
    assert [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("ict_requests", "request_links")] == [0, 0]
    conn.close()
