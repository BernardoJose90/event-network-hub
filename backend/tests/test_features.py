"""Pausing the help desk and reports (FEATURE_HELPDESK / FEATURE_REPORTS)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings, _flag
from app.main import create_app

from .conftest import TEST_DATABASE_URL

ADMIN = {"X-Role": "network-admin", "X-User": "alice"}


def _client(tmp_path: Path, **flags) -> TestClient:
    settings = Settings(data_dir=tmp_path, gotenberg_url="http://unused", gotenberg_timeout_seconds=1,
                        max_upload_bytes=1024 * 1024, database_url=TEST_DATABASE_URL)
    return TestClient(create_app(replace(settings, **flags)))


def test_paused_areas_have_no_api(tmp_path: Path) -> None:
    with _client(tmp_path, helpdesk_enabled=False, reports_enabled=False, websummit_theme_enabled=False) as c:
        assert c.get("/api/features").json() == {"helpdesk": False, "reports": False, "websummit_theme": False}
        for path in ("/api/tickets", "/api/tickets/meta", "/api/reports"):
            assert c.get(path, headers=ADMIN).status_code == 404, path
        assert c.post("/api/bot/tickets", json={}).status_code == 404
        assert c.get("/api/documents", headers=ADMIN).status_code == 200  # events are unaffected


def test_enabled_by_default(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        assert c.get("/api/features").json() == {"helpdesk": True, "reports": True, "websummit_theme": True}
        assert c.get("/api/tickets/meta", headers=ADMIN).status_code == 200
        assert c.get("/api/reports", headers=ADMIN).status_code == 200


def test_help_desk_alert_choices_are_hidden_but_kept_while_paused(tmp_path: Path) -> None:
    with _client(tmp_path) as c:
        c.put("/api/notifications/settings", headers=ADMIN, json={"push_types": ["ticket_urgent", "requests"]})
    with _client(tmp_path, helpdesk_enabled=False) as c:
        shown = [p["key"] for p in c.get("/api/notifications/settings", headers=ADMIN).json()["push_types"]]
        assert "ticket_urgent" not in shown and "sla" not in shown and "requests" in shown
        r = c.put("/api/notifications/settings", headers=ADMIN, json={"push_types": []})  # turn "requests" off
        assert r.json()["push_types"] == []
    with _client(tmp_path) as c:
        on = [p["key"] for p in c.get("/api/notifications/settings", headers=ADMIN).json()["push_types"] if p["on"]]
        assert on == ["ticket_urgent"]  # the hidden choice survived the pause


def test_flag_parsing(monkeypatch) -> None:
    for value, expected in (("", True), ("true", True), ("1", True), ("false", False), ("off", False)):
        monkeypatch.setenv("FEATURE_X", value)
        assert _flag("FEATURE_X") is expected
