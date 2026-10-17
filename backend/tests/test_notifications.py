from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import Settings
from app.main import create_app
from app.notifications import service, worker

from .conftest import FakeConverter

ADMIN = {"X-Role": "network-admin", "X-User": "alice"}
BOB = {"X-Role": "network-admin", "X-User": "bob"}
VIEWER = {"X-Role": "viewer", "X-User": "victor"}
SUB = {"endpoint": "https://push.example.com/send/abc123", "keys": {"p256dh": "B" * 87, "auth": "A" * 22}}


class Recorder:
    """Stands in for the real channels; records what would have been sent."""

    def __init__(self) -> None:
        self.push, self.slack, self.email = [], [], []
        self.fail_push_gone = False

    def install(self, sender) -> None:
        def push(sub, payload, urgent):
            if self.fail_push_gone:
                raise worker.DeliveryError("gone", gone=True)
            self.push.append((sub["endpoint"], payload, urgent))
        sender.send_push = push
        sender.send_slack = lambda payload: self.slack.append(payload)
        sender.send_email = lambda to, subject, text: self.email.append((to, subject, text))


def _app(tmp_path: Path, **settings):
    s = Settings(data_dir=tmp_path, gotenberg_url="http://unused", gotenberg_timeout_seconds=1,
                 max_upload_bytes=1024 * 1024, app_public_url="https://hub.example", **settings)
    return create_app(s), s


@pytest.fixture
def env(tmp_path: Path):
    app, settings = _app(tmp_path, slack_webhook_url="https://hooks.slack.example/T/B/x", smtp_host="smtp.example",
                         escalation_emails=("lead@example.com",))
    with TestClient(app) as c:
        app.state.converter = FakeConverter()
        rec = Recorder()
        rec.install(app.state.notification_sender)
        c.settings = settings
        c.rec = rec
        c.deliver = lambda: worker.deliver_due(settings.db_path, app.state.notification_sender)
        yield c


def _ticket(c, **fields):
    body = {"title": "Stand A12 offline", "category": "partner_stand", "severity": "P2", "stand": "A12"} | fields
    r = c.post("/api/tickets", headers=ADMIN, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _inbox(c, headers):
    return c.get("/api/notifications", headers=headers).json()


def test_in_app_audiences_and_read_state(env) -> None:
    t = _ticket(env)
    alice = _inbox(env, ADMIN)
    assert alice["unread"] == 1 and alice["items"][0]["title"].startswith("P2 ticket #")
    assert alice["items"][0]["link"] == f"/helpdesk?view=tickets&ticket={t['id']}"
    assert _inbox(env, VIEWER)["unread"] == 0  # help desk alerts go to the ICT team (network admins)
    assert env.get("/api/notifications", headers={}).status_code == 403

    env.post(f"/api/tickets/{t['id']}/assign", headers=ADMIN, json={"revision": t["revision"], "assignee": "bob"})
    bob = _inbox(env, BOB)
    assert bob["unread"] == 2 and bob["items"][0]["title"] == f"Ticket #{t['id']} assigned to you"
    assert all("assigned to you" not in i["title"] for i in _inbox(env, ADMIN)["items"])  # not to the assigner

    r = env.post("/api/notifications/read", headers=BOB, json={"ids": [bob["items"][0]["id"]]})
    assert r.json()["marked"] == 1 and r.json()["unread"] == 1
    env.post("/api/notifications/read", headers=BOB, json={})
    assert _inbox(env, BOB)["unread"] == 0 and _inbox(env, ADMIN)["unread"] == 1  # read state is per person


def test_preferences_and_push_routing(env) -> None:
    prefs = env.get("/api/notifications/settings", headers=ADMIN).json()
    assert prefs["push_available"] and prefs["vapid_public_key"]
    assert {p["key"] for p in prefs["push_types"] if p["on"]} == {"ticket_urgent", "sla", "assigned"}
    assert env.post("/api/notifications/push/subscribe", headers=ADMIN,
                    json=SUB | {"endpoint": "http://insecure"}).status_code == 422
    assert env.post("/api/notifications/push/subscribe", headers=ADMIN, json=SUB).json() == {"devices": 1}
    env.post("/api/notifications/push/subscribe", headers=VIEWER,
             json=SUB | {"endpoint": "https://push.example.com/send/viewer"})

    _ticket(env, severity="P3")  # not urgent: not pushed by default
    _ticket(env, severity="P1")
    env.deliver()
    assert len(env.rec.push) == 1  # (plus the Slack message for the P1)
    endpoint, payload, urgent = env.rec.push[0]
    assert endpoint == SUB["endpoint"] and payload["title"].startswith("P1 ticket") and urgent
    assert payload["url"].startswith("/helpdesk?") and payload["tag"].startswith("n")

    saved = env.put("/api/notifications/settings", headers=ADMIN, json={"push_types": ["ticket_all", "bogus"]}).json()
    assert saved == {"push_types": ["ticket_all"]}
    _ticket(env, severity="P4")
    env.deliver()
    assert len(env.rec.push) == 2 and env.rec.push[-1][1]["title"].startswith("P4 ticket")

    env.post("/api/notifications/test", headers=VIEWER)
    env.deliver()
    assert env.rec.push[-1][0].endswith("/viewer") and env.rec.push[-1][1]["title"] == "Test alert from Event Network Hub"


def test_gone_subscriptions_are_removed(env) -> None:
    env.post("/api/notifications/push/subscribe", headers=ADMIN, json=SUB)
    env.rec.fail_push_gone = True
    _ticket(env, severity="P1")
    env.deliver()
    assert env.get("/api/notifications/settings", headers=ADMIN).json()["devices"] == 0
    conn = db.connect(env.settings.db_path)
    assert conn.execute("SELECT status FROM notification_deliveries WHERE channel = 'push'").fetchone()[0] == "failed"
    conn.close()


def test_urgent_tickets_go_to_slack(env) -> None:
    _ticket(env, severity="P3")
    _ticket(env, severity="P1", title="Keynote stage switch down")
    env.deliver()
    assert len(env.rec.slack) == 1
    payload = env.rec.slack[0]
    assert payload["text"].startswith("P1 ticket") and "Keynote" in payload["text"]
    button = payload["blocks"][1]["elements"][0]
    assert button["url"].startswith("https://hub.example/helpdesk?view=tickets&ticket=")


def _age(c, ticket_id, minutes):
    conn = db.connect(c.settings.db_path)
    with conn:
        conn.execute("UPDATE tickets SET created_at = ? WHERE id = ?",
                     ((datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds"), ticket_id))
    conn.close()


def test_sla_warning_then_breach_escalation_once(env) -> None:
    t = _ticket(env, severity="P1")
    low = _ticket(env, severity="P3")
    _age(env, t["id"], 4.5)
    assert worker.check_sla(env.settings.db_path) == 1
    assert worker.check_sla(env.settings.db_path) == 0  # once per ticket
    _age(env, t["id"], 6)
    _age(env, low["id"], 6)
    assert worker.check_sla(env.settings.db_path) == 2  # P1 breach + P3 breach (in-app only)
    env.deliver()
    titles = [s["text"] for s in env.rec.slack]
    assert any(x.startswith(f"#{t['id']} not acknowledged yet") for x in titles)
    assert any(x.startswith(f"SLA missed: #{t['id']}") for x in titles)
    assert not any(f"#{low['id']}" in x for x in titles)
    assert [e[0] for e in env.rec.email] == ["lead@example.com"]
    assert any("SLA missed" in i["title"] for i in _inbox(env, ADMIN)["items"])

    acked = _ticket(env, severity="P1")
    env.post(f"/api/tickets/{acked['id']}/acknowledge", headers=ADMIN, json={"revision": acked["revision"]})
    _age(env, acked["id"], 10)
    assert worker.check_sla(env.settings.db_path) == 0  # acknowledged tickets don't escalate


def test_ict_requests_notify_the_team_and_email_requesters(env) -> None:
    doc = env.post("/api/events", headers=ADMIN, json={"event_code": "LIS26"}).json()["id"]
    lists = env.get(f"/api/events/{doc}/overview", headers=ADMIN).json()
    env.post(f"/api/events/{doc}/lists/location", headers=ADMIN, json={"value": "Hall 1"})
    link = env.post(f"/api/events/{doc}/ict-requests/links", headers=ADMIN, json={"team": "av"}).json()
    r = env.post(f"/api/events/{doc}/ict-requests/links/{link['id']}/email", headers=ADMIN, json={"email": "nope"})
    assert r.status_code == 422
    r = env.post(f"/api/events/{doc}/ict-requests/links/{link['id']}/email", headers=ADMIN, json={"email": "AV-Lead@example.com"})
    assert r.status_code == 200 and r.json()["contact_email"] == "av-lead@example.com"
    req = env.post(f"/api/request-links/{link['token']}/requests", json={
        "requester_name": "Ana", "requester_contact": "ana@example.com",
        "lines": [{"kind": "wired", "area": "Hall 1", "purpose": "Encoders", "ports": 2}]}).json()["requests"][0]
    assert _inbox(env, ADMIN)["items"][0]["title"] == "1 new ICT request from AV"
    env.post(f"/api/events/{doc}/ict-requests/{req['id']}/needs-info", headers=ADMIN,
             json={"revision": req["revision"], "note": "Which hall exactly?"})
    env.deliver()
    sent = {to: (subject, text) for to, subject, text in env.rec.email}
    assert "/request/" in sent["av-lead@example.com"][1]
    assert sent["ana@example.com"][0] == f"Your ICT request #{req['id']} for LIS26 was needs more information"
    assert "Which hall exactly?" in sent["ana@example.com"][1]
    assert lists["document"]["name"].startswith("LIS26")


def test_deadline_reminders_7_and_1_days_before(env) -> None:
    doc = env.post("/api/events", headers=ADMIN, json={"event_code": "VAN26"}).json()["id"]
    link = env.post(f"/api/events/{doc}/ict-requests/links", headers=ADMIN, json={"team": "cctv"}).json()
    env.post(f"/api/events/{doc}/ict-requests/links/{link['id']}/email", headers=ADMIN, json={"email": "cctv@example.com"})
    deadline = date(2030, 5, 20)
    env.put(f"/api/events/{doc}/ict-requests/settings", headers=ADMIN, json={"deadline": deadline.isoformat()})
    assert worker.check_deadlines(env.settings.db_path, today=deadline - timedelta(days=3)) == 0
    assert worker.check_deadlines(env.settings.db_path, today=deadline - timedelta(days=7)) == 1
    assert worker.check_deadlines(env.settings.db_path, today=deadline - timedelta(days=7)) == 0  # once
    assert worker.check_deadlines(env.settings.db_path, today=deadline - timedelta(days=1)) == 1
    env.deliver()
    reminders = [s for to, s, _ in env.rec.email if to == "cctv@example.com" and s.startswith("Reminder")]
    assert reminders == ["Reminder: ICT requests for VAN26 are due in 7 days",
                         "Reminder: ICT requests for VAN26 are due in 1 day"]


def test_publishing_emails_followers(env) -> None:
    doc = env.post("/api/events", headers=ADMIN, json={"event_code": "QAT26"}).json()["id"]
    assert env.post(f"/api/events/{doc}/watchers", headers=VIEWER, json={"email": "x@example.com"}).status_code == 403
    assert env.post(f"/api/events/{doc}/watchers", headers=ADMIN, json={"email": "bad"}).status_code == 422
    env.post(f"/api/events/{doc}/watchers", headers=ADMIN, json={"email": "Venue@Example.com"})
    assert env.get(f"/api/events/{doc}/watchers", headers=VIEWER).json()["emails"] == ["venue@example.com"]
    ov = env.get(f"/api/events/{doc}/overview", headers=ADMIN).json()
    core = next(g["id"] for g in ov["groups"] if g["kind"] == "wifi")
    env.post(f"/api/events/{doc}/records/wifi", headers=ADMIN,
             json={"venue_id": ov["venues"][0]["id"], "group_id": core, "data": {"ssid": "Staff"}})
    assert env.post(f"/api/events/{doc}/publish", headers=ADMIN, json={"note": "First plan"}).status_code == 201
    env.deliver()
    to, subject, text = env.rec.email[-1]
    assert (to, subject) == ("venue@example.com", "QAT26 V1 published")
    assert "Note: First plan" in text and "https://hub.example/?doc=" in text
    assert _inbox(env, ADMIN)["items"][0]["title"] == "QAT26 V1 published"
    env.delete(f"/api/events/{doc}/watchers/venue@example.com", headers=ADMIN)
    assert env.get(f"/api/events/{doc}/watchers", headers=ADMIN).json()["emails"] == []


def test_live_stream_sends_updates(env) -> None:
    _ticket(env)
    assert env.get("/api/notifications/stream", params={"user": "alice", "role": "bogus"}).status_code == 403
    with env.stream("GET", "/api/notifications/stream",
                    params={"user": "alice", "role": "network-admin", "limit_seconds": 1}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    data = json.loads(body.split("event: update\ndata: ")[1].split("\n\n")[0])
    assert data["unread"] == 1 and data["items"][0]["severity"] == "urgent"  # a P2 ticket


def test_channels_off_without_configuration_and_stable_keys(tmp_path: Path) -> None:
    app, settings = _app(tmp_path)
    with TestClient(app) as c:
        key = c.get("/api/notifications/settings", headers=ADMIN).json()["vapid_public_key"]
        c.post("/api/tickets", headers=ADMIN, json={"title": "x", "category": "wifi", "severity": "P1"})
        conn = db.connect(settings.db_path)
        assert conn.execute("SELECT COUNT(*) FROM notification_deliveries").fetchone()[0] == 0  # no Slack, email or devices
        conn.close()
    assert (tmp_path / "vapid.json").exists()
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/notifications/settings", headers=ADMIN).json()["vapid_public_key"] == key
    assert service.CHANNELS.slack is False and service.CHANNELS.email is False
