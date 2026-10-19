from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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
                        max_upload_bytes=1024 * 1024)
    app = create_app(settings)
    with TestClient(app) as c:
        app.state.converter = FakeConverter()
        yield c


@pytest.fixture
def event(client: TestClient) -> int:
    r = client.post("/api/documents/upload", headers=ADMIN,
                    files={"file": ("LIS26 _ Event_Network_Requirements_V1.xlsx", records_workbook(), XLSX)})
    doc = r.json()["document"]["id"]
    assert client.post(f"/api/events/{doc}/import", headers=ADMIN, json={}).status_code == 200
    assert client.post(f"/api/events/{doc}/publish", headers=ADMIN).status_code == 201  # V2
    return doc


def _find(client, doc, rtype, headers=ADMIN, **match):
    rows = client.get(f"/api/events/{doc}/records/{rtype}", headers=headers).json()
    return next(r for r in rows if all(r["data"].get(k) == v for k, v in match.items()))


def _patch(client, doc, record, data):
    r = client.patch(f"/api/events/{doc}/records/{record['type']}/{record['id']}", headers=ADMIN,
                     json={"revision": record["revision"], "data": data})
    assert r.status_code == 200, r.text
    return r.json()


def _notifications(client, headers):
    return [n["title"] for n in client.get("/api/notifications", headers=headers).json()["items"]]


def test_viewers_see_the_published_version_and_admins_the_draft(client: TestClient, event: int) -> None:
    office = _find(client, event, "ethernet", purpose="Photographers Office")
    _patch(client, event, office, {"ports": 40})  # draft change, not published

    admin = client.get(f"/api/events/{event}/overview", headers=ADMIN).json()
    viewer = client.get(f"/api/events/{event}/overview", headers=VIEWER).json()
    assert admin["showing"] is None and admin["unpublished_changes"] == 1
    assert viewer["showing"]["version"] == 2 and viewer["unpublished_changes"] == 0
    assert viewer["counts"] == admin["counts"]
    assert _find(client, event, "ethernet", headers=VIEWER, purpose="Photographers Office")["data"]["ports"] == 2
    assert _find(client, event, "ethernet", purpose="Photographers Office")["data"]["ports"] == 40
    # same item IDs, so comments line up; venues and sections keep their live IDs
    assert _find(client, event, "ethernet", headers=VIEWER, purpose="Photographers Office")["id"] == office["id"]
    assert [v["id"] for v in viewer["venues"]] == [v["id"] for v in admin["venues"]]
    altice = next(t for t in viewer["totals"]["ethernet"] if t["venue"] == "Altice Arena")
    assert altice["total"] == 124  # the published total, not the draft's

    # passwords: hidden in lists, the published value on reveal (and logged)
    prod = _find(client, event, "wifi", ssid="#Production")
    _patch(client, event, prod, {"password": "Draft-only-1"})
    seen = _find(client, event, "wifi", headers=VIEWER, ssid="#Production")
    assert "password" not in seen["data"] and seen["has_password"]
    revealed = client.post(f"/api/events/{event}/records/wifi/{prod['id']}/reveal", headers=VIEWER).json()
    assert revealed["password"] != "Draft-only-1"
    history = client.get(f"/api/events/{event}/history", headers=ADMIN).json()
    assert any(e["action"] == "reveal" and e["user"] == "victor" for e in history)


def test_viewer_comments_on_an_item_and_admins_are_told(client: TestClient, event: int) -> None:
    office = _find(client, event, "ethernet", headers=VIEWER, purpose="Photographers Office")
    r = client.post(f"/api/events/{event}/comments", headers=VIEWER,
                    json={"record_id": office["id"], "body": "  Needs 4 more points for the camera desk  "})
    assert r.status_code == 201, r.text
    c = r.json()
    assert (c["version"], c["record_type"], c["item_label"], c["user"], c["role"]) == \
        (2, "ethernet", "Photographers Office", "victor", "viewer")
    assert c["body"] == "Needs 4 more points for the camera desk" and c["item_where"]

    listed = client.get(f"/api/events/{event}/comments", headers=ADMIN).json()
    assert listed["open"] == 1 and listed["by_record"] == {str(office["id"]): 1} and listed["latest_version"] == 2
    assert client.get(f"/api/events/{event}/overview", headers=VIEWER).json()["open_comments"] == 1
    assert any("New comment on LIS26" in t for t in _notifications(client, ADMIN))
    assert not any("New comment" in t for t in _notifications(client, VIEWER))
    # a comment isn't a change to the requirements
    assert client.get(f"/api/events/{event}/overview", headers=ADMIN).json()["unpublished_changes"] == 0

    general = client.post(f"/api/events/{event}/comments", headers=VIEWER, json={"body": "Broadcast truck VLAN?"})
    assert general.status_code == 201 and general.json()["record_id"] is None


def test_replies_resolve_and_reopen(client: TestClient, event: int) -> None:
    c = client.post(f"/api/events/{event}/comments", headers=VIEWER, json={"body": "Is the press Wi-Fi 6GHz?"}).json()
    r = client.post(f"/api/events/{event}/comments/{c['id']}/replies", headers=ADMIN, json={"body": "Yes, from V3"})
    assert r.status_code == 201
    assert any("alice replied" in t for t in _notifications(client, VIEWER))

    assert client.post(f"/api/events/{event}/comments/{c['id']}/resolve", headers=VIEWER).status_code == 403
    resolved = client.post(f"/api/events/{event}/comments/{c['id']}/resolve", headers=ADMIN).json()
    assert resolved["resolved_by"] == "alice" and resolved["resolved_at"]
    assert client.post(f"/api/events/{event}/comments/{c['id']}/resolve", headers=ADMIN).status_code == 409
    assert any("was resolved" in t for t in _notifications(client, VIEWER))

    thread = client.get(f"/api/events/{event}/comments", headers=VIEWER, params={"state": "resolved"}).json()
    assert [t["id"] for t in thread["threads"]] == [c["id"]] and thread["open"] == 0
    assert [x["body"] for x in thread["threads"][0]["replies"]] == ["Yes, from V3"]
    assert client.post(f"/api/events/{event}/comments/{c['id']}/reopen", headers=ADMIN).status_code == 200
    assert client.get(f"/api/events/{event}/comments", headers=VIEWER).json()["open"] == 1

    reply_id = thread["threads"][0]["replies"][0]["id"]
    assert client.post(f"/api/events/{event}/comments/{reply_id}/replies", headers=VIEWER,
                       json={"body": "nested"}).status_code == 422


def test_open_comments_carry_over_to_the_next_version(client: TestClient, event: int) -> None:
    client.post(f"/api/events/{event}/comments", headers=VIEWER, json={"body": "On V2"})
    office = _find(client, event, "ethernet", purpose="Photographers Office")
    _patch(client, event, office, {"ports": 6})
    client.post(f"/api/events/{event}/publish", headers=ADMIN)  # V3
    listed = client.get(f"/api/events/{event}/comments", headers=VIEWER).json()
    assert listed["open"] == 1 and listed["threads"][0]["version"] == 2 and listed["latest_version"] == 3


def test_comment_rules(client: TestClient, event: int) -> None:
    url = f"/api/events/{event}/comments"
    assert client.post(url, headers={"X-Role": "viewer"}, json={"body": "hi"}).status_code == 422  # no name
    assert client.post(url, headers={}, json={"body": "hi"}).status_code == 403
    assert client.post(url, headers=VIEWER, json={"body": "   "}).status_code == 422
    assert client.post(url, headers=VIEWER, json={"body": "x" * 2001}).status_code == 422
    assert client.post(url, headers=VIEWER, json={"body": "hi", "record_id": 999999}).status_code == 404

    # a deleted item's comments keep their label
    bar = _find(client, event, "ethernet", purpose="FoodTrucks / Bar")
    c = client.post(url, headers=VIEWER, json={"body": "Remove?", "record_id": bar["id"]}).json()
    client.delete(f"/api/events/{event}/records/ethernet/{bar['id']}?revision={bar['revision']}", headers=ADMIN)
    assert client.get(url, headers=VIEWER).json()["threads"][0]["item_label"] == "FoodTrucks / Bar"
    # ...and the item can still be commented on while it's in the published version viewers see
    assert client.post(url, headers=VIEWER, json={"body": "Again", "record_id": bar["id"]}).status_code == 201
    assert c["item_label"] == "FoodTrucks / Bar"

    # archived events take no new comments
    assert client.post(f"/api/documents/{event}/archive", headers=ADMIN).status_code == 200
    assert client.post(url, headers=VIEWER, json={"body": "late"}).status_code == 409
    assert client.get(url, headers=VIEWER).status_code == 200


def test_before_anything_is_published_viewers_see_the_draft(client: TestClient) -> None:
    r = client.post("/api/documents/upload", headers=ADMIN,
                    files={"file": ("VAN26 _ Event_Network_Requirements_V1.xlsx", records_workbook(), XLSX)})
    doc = r.json()["document"]["id"]
    client.post(f"/api/events/{doc}/import", headers=ADMIN, json={})
    ov = client.get(f"/api/events/{doc}/overview", headers=VIEWER).json()
    assert ov["showing"] is None and ov["has_records"]
    c = client.post(f"/api/events/{doc}/comments", headers=VIEWER, json={"body": "Early feedback"}).json()
    assert c["version"] is None
