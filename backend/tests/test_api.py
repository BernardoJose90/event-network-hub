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
ADMIN = {"X-Role": "network-admin"}
NAME_V1 = "LIS26 _ Event_Network_Requirements_V1.xlsx"


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


def _upload(client: TestClient, filename=NAME_V1, data=None, headers=None):
    return client.post(
        "/api/documents/upload",
        files={"file": (filename, data if data is not None else _wb(filename), XLSX)},
        headers={**ADMIN, "X-User": "alice"} if headers is None else headers,
    )


def _doc_with_version(client: TestClient) -> int:
    r = _upload(client)
    assert r.status_code == 201, r.text
    return r.json()["document"]["id"]


# --- upload ---------------------------------------------------------------


def test_upload_creates_document_for_event(client: TestClient) -> None:
    r = _upload(client, filename="LIS25 _ Event_Network_Requirements_V5.xlsx")

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["document"]["name"] == "LIS25 | Event_Network_Requirements"
    assert (body["version"]["version"], body["version"]["status"]) == (5, "ready")
    assert body["version"]["uploaded_by"] == "alice"
    assert body["message"] == "V5 added to LIS25 | Event_Network_Requirements."


def test_new_version_updates_existing_document(client: TestClient) -> None:
    doc_id = _doc_with_version(client)
    r = _upload(client, filename="LIS26 | Event_Network_Requirements_V2.xlsx")

    assert r.status_code == 201, r.text
    assert r.json()["document"]["id"] == doc_id
    assert [v["version"] for v in client.get(f"/api/documents/{doc_id}/versions").json()] == [2, 1]
    assert len(client.get("/api/documents").json()) == 1
    latest = client.get(f"/api/documents/{doc_id}/download", headers=ADMIN)
    assert latest.headers["x-document-version"] == "2"


def test_document_list_includes_every_version(client: TestClient) -> None:
    _upload(client, filename="LIS26 _ Event_Network_Requirements_V1.xlsx")
    _upload(client, filename="LIS26 _ Event_Network_Requirements_V2.xlsx")
    client.app.state.converter.fail = True
    _upload(client, filename="LIS26 _ Event_Network_Requirements_V3.xlsx")
    client.app.state.converter.fail = False
    _upload(client, filename="LIS27 _ Event_Network_Requirements_V1.xlsx")

    docs = client.get("/api/documents").json()

    assert [(d["name"], d["latest_ready_version"], d["versions"]) for d in docs] == [
        ("LIS26 | Event_Network_Requirements", 2, [
            {"version": 3, "status": "failed"},
            {"version": 2, "status": "ready"},
            {"version": 1, "status": "ready"},
        ]),
        ("LIS27 | Event_Network_Requirements", 1, [{"version": 1, "status": "ready"}]),
    ]


def test_same_version_twice_is_rejected(client: TestClient) -> None:
    doc_id = _doc_with_version(client)
    r = _upload(client, data=_wb("different contents"))

    assert r.status_code == 409
    assert "V1 of LIS26 | Event_Network_Requirements has already been uploaded" in r.json()["detail"]
    assert len(client.get(f"/api/documents/{doc_id}/versions").json()) == 1


def test_same_version_under_another_spelling_is_rejected(client: TestClient) -> None:
    assert _upload(client, filename="VAN26 _ Event_Network_Requirements_V1.xlsx").status_code == 201
    r = _upload(client, filename="VAN26 _ Event Network Requirements - V1.xlsx", data=_wb("other"))
    assert r.status_code == 409
    assert "V1 of VAN26 | Event_Network_Requirements has already been uploaded" in r.json()["detail"]
    assert len(client.get("/api/documents").json()) == 1


def test_spaces_and_hyphens_are_accepted(client: TestClient) -> None:
    r = _upload(client, filename="VAN26 _ Event Network Requirements - V1.xlsx")
    assert r.status_code == 201, r.text
    assert r.json()["document"]["name"] == "VAN26 | Event_Network_Requirements"
    assert r.json()["version"]["version"] == 1


@pytest.mark.parametrize(
    ("filename", "message"),
    [
        ("Budget.xlsx", "must contain 'Event Network Requirements'"),
        ("Event_Network_Requirements_V1.xlsx", "event code"),
        ("LIS26 _ Event_Network_Requirements.xlsx", "version number"),
        ("LIS26 _ Event_Network_Requirements_V1.xlsm", ".xlsm"),
        ("LIS26 _ Event_Network_Requirements_V1.csv", "Only Excel .xlsx"),
    ],
)
def test_bad_files_are_rejected_and_create_nothing(client: TestClient, filename, message) -> None:
    r = _upload(client, filename=filename)
    assert r.status_code == 422
    assert message in r.json()["detail"]
    assert client.get("/api/documents").json() == []


@pytest.mark.parametrize("headers", [{}, {"X-Role": "viewer"}, {"X-Role": "superuser"}])
def test_only_admin_can_upload(client: TestClient, headers) -> None:
    r = _upload(client, headers=headers)
    assert r.status_code == 403
    assert client.get("/api/documents").json() == []


def test_failed_conversion_can_be_retried(client: TestClient) -> None:
    client.app.state.converter.fail = True
    r = _upload(client)
    assert r.status_code == 502
    assert r.json()["version"]["status"] == "failed"
    doc_id = r.json()["document"]["id"]
    h = {"X-Role": "viewer"}
    assert client.get(f"/api/documents/{doc_id}/download?version=1", headers=h).status_code == 409
    assert client.get(f"/api/documents/{doc_id}/download", headers=h).status_code == 404

    client.app.state.converter.fail = False
    assert _upload(client).status_code == 201
    assert client.get(f"/api/documents/{doc_id}/download", headers=h).status_code == 200


def test_removed_endpoints(client: TestClient) -> None:
    doc_id = _doc_with_version(client)
    assert client.post("/api/documents", json={"name": "A"}, headers=ADMIN).status_code == 405
    r = client.post(f"/api/documents/{doc_id}/versions",
                    files={"file": (NAME_V1, _wb("x"), XLSX)}, headers=ADMIN)
    assert r.status_code == 405


# --- delete ---------------------------------------------------------------


def test_admin_deletes_document_with_everything(client: TestClient) -> None:
    doc_id = _doc_with_version(client)
    client.get(f"/api/documents/{doc_id}/download", headers={"X-Role": "viewer"})
    files_dir = client.settings.files_dir / "documents" / str(doc_id)
    assert files_dir.exists()

    r = client.delete(f"/api/documents/{doc_id}", headers=ADMIN)

    assert r.status_code == 204
    assert client.get("/api/documents").json() == []
    assert client.get(f"/api/documents/{doc_id}/versions").status_code == 404
    assert client.get(f"/api/documents/{doc_id}/download", headers=ADMIN).status_code == 404
    assert not files_dir.exists()
    conn = db.connect(client.settings.db_path)
    counts = [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("documents", "document_versions", "download_log")]
    conn.close()
    assert counts == [0, 0, 0]


def test_after_delete_the_event_can_start_again_at_v1(client: TestClient) -> None:
    doc_id = _doc_with_version(client)
    client.delete(f"/api/documents/{doc_id}", headers=ADMIN)
    assert _upload(client).status_code == 201


def test_delete_leaves_other_documents_alone(client: TestClient) -> None:
    keep = _upload(client, filename="LIS26 _ Event_Network_Requirements_V1.xlsx").json()["document"]["id"]
    drop = _upload(client, filename="LIS27 _ Event_Network_Requirements_V1.xlsx").json()["document"]["id"]

    assert client.delete(f"/api/documents/{drop}", headers=ADMIN).status_code == 204
    assert [d["id"] for d in client.get("/api/documents").json()] == [keep]
    assert client.get(f"/api/documents/{keep}/download", headers=ADMIN).status_code == 200


@pytest.mark.parametrize("headers", [{}, {"X-Role": "viewer"}])
def test_only_admin_can_delete(client: TestClient, headers) -> None:
    doc_id = _doc_with_version(client)
    assert client.delete(f"/api/documents/{doc_id}", headers=headers).status_code == 403
    assert len(client.get("/api/documents").json()) == 1


def test_delete_missing_document_is_404(client: TestClient) -> None:
    assert client.delete("/api/documents/999", headers=ADMIN).status_code == 404


# --- download -------------------------------------------------------------


@pytest.mark.parametrize("role", ["viewer", "network-admin"])
def test_every_role_gets_the_full_pdf(client: TestClient, role) -> None:
    doc_id = _doc_with_version(client)
    r = client.get(f"/api/documents/{doc_id}/download", headers={"X-Role": role, "X-User": "bob"})

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.endswith(b"-v1.xlsx")  # FakeConverter echoes the filename

    conn = db.connect(client.settings.db_path)
    rows = [dict(x) for x in conn.execute("SELECT user, format FROM download_log")]
    conn.close()
    assert rows == [{"user": "bob", "format": "pdf"}]


@pytest.mark.parametrize("role", ["viewer", "network-admin"])
def test_download_original_excel(client: TestClient, role) -> None:
    data = _wb("original")
    r = _upload(client, data=data)
    doc_id = r.json()["document"]["id"]
    r = client.get(f"/api/documents/{doc_id}/download?format=xlsx", headers={"X-Role": role})

    assert r.status_code == 200
    assert r.headers["content-type"] == XLSX
    assert r.content == data  # byte-identical to the upload
    assert r.headers["content-disposition"].endswith('-v1.xlsx"')


def test_unknown_download_format_rejected(client: TestClient) -> None:
    doc_id = _doc_with_version(client)
    r = client.get(f"/api/documents/{doc_id}/download?format=docx", headers={"X-Role": "viewer"})
    assert r.status_code == 422


@pytest.mark.parametrize("headers", [{}, {"X-Role": "superuser"}])
def test_download_without_known_role_is_forbidden(client: TestClient, headers) -> None:
    doc_id = _doc_with_version(client)
    assert client.get(f"/api/documents/{doc_id}/download", headers=headers).status_code == 403


def test_download_specific_and_missing_version(client: TestClient) -> None:
    doc_id = _doc_with_version(client)
    _upload(client, filename="LIS26 _ Event_Network_Requirements_V2.xlsx")
    h = {"X-Role": "viewer"}
    r = client.get(f"/api/documents/{doc_id}/download?version=1", headers=h)
    assert (r.status_code, r.headers["x-document-version"]) == (200, "1")
    assert client.get(f"/api/documents/{doc_id}/download?version=9", headers=h).status_code == 404
