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


ADMIN = {"X-Role": "network-admin"}


def _upload_new(client: TestClient, filename="lis25.xlsx", data=None, headers=None, **form):
    return client.post(
        "/api/documents/upload",
        files={"file": (filename, data if data is not None else _wb("v1"), XLSX)},
        data=form,
        headers={**ADMIN, "X-User": "alice"} if headers is None else headers,
    )


def _doc_with_version(client: TestClient) -> int:
    r = _upload_new(client, name="LIS25 Network Requirements")
    assert r.status_code == 201, r.text
    return r.json()["document"]["id"]


def test_upload_new_document_named_after_file(client: TestClient) -> None:
    r = _upload_new(client, filename="Event_Network_Requirements_V5.xlsx")

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["document"]["name"] == "Event_Network_Requirements_V5"
    assert (body["version"]["version"], body["version"]["status"]) == (1, "ready")
    assert body["version"]["uploaded_by"] == "alice"
    assert [d["name"] for d in client.get("/api/documents").json()] == ["Event_Network_Requirements_V5"]


def test_upload_new_document_with_explicit_name(client: TestClient) -> None:
    r = _upload_new(client, name="  LIS25 Network Requirements ")
    assert r.json()["document"]["name"] == "LIS25 Network Requirements"


def test_upload_new_document_name_conflict(client: TestClient) -> None:
    assert _upload_new(client).status_code == 201
    r = _upload_new(client, data=_wb("other"))
    assert r.status_code == 409
    assert "new version" in r.json()["detail"]
    assert len(client.get("/api/documents").json()) == 1


@pytest.mark.parametrize("headers", [{}, {"X-Role": "viewer"}, {"X-Role": "superuser"}])
def test_only_admin_can_create_documents(client: TestClient, headers) -> None:
    r = _upload_new(client, headers=headers)
    assert r.status_code == 403
    assert client.get("/api/documents").json() == []


def test_invalid_file_does_not_create_document(client: TestClient) -> None:
    r = _upload_new(client, filename="x.xlsm")
    assert r.status_code == 422
    assert ".xlsm" in r.json()["detail"]
    assert client.get("/api/documents").json() == []


def test_old_json_create_endpoint_is_gone(client: TestClient) -> None:
    assert client.post("/api/documents", json={"name": "A"}, headers=ADMIN).status_code == 405


def test_upload_rejects_xlsm(client: TestClient) -> None:
    doc_id = _doc_with_version(client)
    r = client.post(f"/api/documents/{doc_id}/versions",
                    files={"file": ("x.xlsm", _wb("v2"), XLSX)})
    assert r.status_code == 422
    assert ".xlsm" in r.json()["detail"]


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


def test_delete_leaves_other_documents_alone(client: TestClient) -> None:
    keep = _upload_new(client, filename="keep.xlsx").json()["document"]["id"]
    drop = _upload_new(client, filename="drop.xlsx", data=_wb("other")).json()["document"]["id"]

    assert client.delete(f"/api/documents/{drop}", headers=ADMIN).status_code == 204
    assert [d["id"] for d in client.get("/api/documents").json()] == [keep]
    assert client.get(f"/api/documents/{keep}/download", headers=ADMIN).status_code == 200


@pytest.mark.parametrize("headers", [{}, {"X-Role": "viewer"}])
def test_only_admin_can_delete(client: TestClient, headers) -> None:
    doc_id = _doc_with_version(client)
    r = client.delete(f"/api/documents/{doc_id}", headers=headers)
    assert r.status_code == 403
    assert len(client.get("/api/documents").json()) == 1


def test_delete_missing_document_is_404(client: TestClient) -> None:
    assert client.delete("/api/documents/999", headers=ADMIN).status_code == 404


def test_upload_unchanged_returns_200(client: TestClient) -> None:
    doc_id = _doc_with_version(client)
    r = client.post(f"/api/documents/{doc_id}/versions",
                    files={"file": ("lis25.xlsx", _wb("v1"), XLSX)})
    assert r.status_code == 200
    assert r.json()["outcome"] == "unchanged"


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
    doc_id = _doc_with_version(client)
    r = client.get(f"/api/documents/{doc_id}/download?format=xlsx", headers={"X-Role": role})

    assert r.status_code == 200
    assert r.headers["content-type"] == XLSX
    assert r.content == _wb("v1")  # byte-identical to the upload
    assert r.headers["content-disposition"].endswith('-v1.xlsx"')

    conn = db.connect(client.settings.db_path)
    formats = [x[0] for x in conn.execute("SELECT format FROM download_log")]
    conn.close()
    assert formats == ["xlsx"]


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
