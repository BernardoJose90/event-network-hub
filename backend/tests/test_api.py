from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app import db
from app.config import Settings
from app.main import create_app

from .conftest import FakeConverter, xlsx_bytes

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path, gotenberg_url="http://unused", gotenberg_timeout_seconds=1,
        max_upload_bytes=1024 * 1024,
    )
    app = create_app(settings)
    with TestClient(app) as c:
        app.state.converter = FakeConverter()
        c.settings = settings
        yield c


def _wb(text: str) -> bytes:
    def build(wb: Workbook) -> None:
        wb.active["A1"] = text

    return xlsx_bytes(build)


def _doc_with_version(client: TestClient) -> int:
    doc = client.post("/api/documents", json={"name": "LIS25 Network Requirements"}).json()
    r = client.post(
        f"/api/documents/{doc['id']}/versions",
        files={"file": ("lis25.xlsx", _wb("v1"), XLSX)},
        headers={"X-User": "alice"},
    )
    assert r.status_code == 201, r.text
    return doc["id"]


def test_duplicate_document_name_conflicts(client: TestClient) -> None:
    assert client.post("/api/documents", json={"name": "A"}).status_code == 201
    assert client.post("/api/documents", json={"name": "A"}).status_code == 409


def test_upload_rejects_xlsm(client: TestClient) -> None:
    doc = client.post("/api/documents", json={"name": "A"}).json()
    r = client.post(f"/api/documents/{doc['id']}/versions",
                    files={"file": ("x.xlsm", _wb("v1"), XLSX)})
    assert r.status_code == 422
    assert ".xlsm" in r.json()["detail"]


def test_upload_unchanged_returns_200(client: TestClient) -> None:
    doc_id = _doc_with_version(client)
    r = client.post(f"/api/documents/{doc_id}/versions",
                    files={"file": ("lis25.xlsx", _wb("v1"), XLSX)})
    assert r.status_code == 200
    assert r.json()["outcome"] == "unchanged"


@pytest.mark.parametrize(
    ("role", "expected_suffix", "redacted"),
    [("viewer", b"-redacted.xlsx", True), ("network-admin", b"-v1.xlsx", False)],
)
def test_download_by_role(client: TestClient, role, expected_suffix, redacted) -> None:
    doc_id = _doc_with_version(client)
    r = client.get(f"/api/documents/{doc_id}/download", headers={"X-Role": role, "X-User": "bob"})

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.endswith(expected_suffix)  # FakeConverter echoes the filename
    assert r.headers["x-redacted"] == str(redacted).lower()

    conn = db.connect(client.settings.db_path)
    rows = [dict(x) for x in conn.execute("SELECT user, redacted FROM download_log")]
    conn.close()
    assert rows == [{"user": "bob", "redacted": int(redacted)}]


@pytest.mark.parametrize("headers", [{}, {"X-Role": "superuser"}])
def test_download_without_known_role_is_forbidden(client: TestClient, headers) -> None:
    doc_id = _doc_with_version(client)
    assert client.get(f"/api/documents/{doc_id}/download", headers=headers).status_code == 403


def test_download_specific_and_missing_version(client: TestClient) -> None:
    doc_id = _doc_with_version(client)
    h = {"X-Role": "viewer"}
    assert client.get(f"/api/documents/{doc_id}/download?version=1", headers=h).status_code == 200
    assert client.get(f"/api/documents/{doc_id}/download?version=9", headers=h).status_code == 404


def test_failed_version_not_downloadable_but_previous_is(client: TestClient) -> None:
    doc_id = _doc_with_version(client)
    client.app.state.converter.fail = True
    r = client.post(f"/api/documents/{doc_id}/versions",
                    files={"file": ("lis25.xlsx", _wb("v2"), XLSX)})
    assert r.status_code == 502
    assert r.json()["version"]["status"] == "failed"

    h = {"X-Role": "viewer"}
    assert client.get(f"/api/documents/{doc_id}/download?version=2", headers=h).status_code == 409
    latest = client.get(f"/api/documents/{doc_id}/download", headers=h)
    assert latest.headers["x-document-version"] == "1"
