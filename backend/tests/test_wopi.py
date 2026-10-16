from __future__ import annotations

from functools import lru_cache

import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app import db
from app.config import Settings
from app.main import create_app
from app.wopi_tokens import EditGrant, InvalidToken, issue, verify

from .conftest import FakeConverter, xlsx_bytes

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ADMIN = {"X-Role": "network-admin", "X-User": "alice"}
SECRET = "test-secret"


class FakeDiscovery:
    def xlsx_edit_urlsrc(self) -> str:
        return "http://localhost:9980/browser/abc/cool.html?"

    def editor_url(self, wopi_src: str) -> str:
        from urllib.parse import quote

        return f"{self.xlsx_edit_urlsrc()}WOPISrc={quote(wopi_src, safe='')}"


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path, gotenberg_url="http://unused", gotenberg_timeout_seconds=1,
        max_upload_bytes=1024 * 1024, wopi_token_secret=SECRET,
    )
    app = create_app(settings)
    with TestClient(app) as c:
        app.state.converter = FakeConverter()
        app.state.discovery = FakeDiscovery()
        c.settings = settings
        yield c


@lru_cache(maxsize=None)  # same bytes every time: saved workbooks embed the current time
def _wb(text: str) -> bytes:
    def build(wb: Workbook) -> None:
        wb.active["A1"] = text

    return xlsx_bytes(build)


def _doc(client: TestClient, version: int = 6) -> int:
    r = client.post(
        "/api/documents/upload",
        files={"file": (f"LIS25 _ Event_Network_Requirements_V{version}.xlsx", _wb(f"v{version}"), XLSX)},
        headers=ADMIN,
    )
    assert r.status_code == 201, r.text
    return r.json()["document"]["id"]


def _start(client: TestClient, doc_id: int, version: int = 6) -> tuple[str, str]:
    r = client.post(f"/api/documents/{doc_id}/versions/{version}/edit", headers=ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    wopi_src = parse_qs(urlparse(body["editor_url"]).query)["WOPISrc"][0]
    assert wopi_src.startswith("http://backend:8000/wopi/files/")
    file_id = wopi_src.rsplit("/", 1)[1]
    assert body["access_token_ttl"] > time.time() * 1000
    return file_id, body["access_token"]


def _versions(client: TestClient, doc_id: int) -> list[tuple[int, str, int | None]]:
    return [(v["version"], v["status"], v["edited_from"])
            for v in client.get(f"/api/documents/{doc_id}/versions").json()]


# --- tokens ---------------------------------------------------------------


def test_token_roundtrip_and_tamper_detection() -> None:
    grant = EditGrant(session_id="s1", document_id=3, user="alice", expires_at=int(time.time()) + 60)
    token = issue(SECRET, grant)
    assert verify(SECRET, token) == grant

    with pytest.raises(InvalidToken):
        verify("other-secret", token)
    payload, sig = token.split(".")
    with pytest.raises(InvalidToken):
        verify(SECRET, payload[:-2] + "AA." + sig)
    with pytest.raises(InvalidToken):
        verify(SECRET, "garbage")


def test_expired_token_rejected() -> None:
    token = issue(SECRET, EditGrant("s1", 3, "alice", expires_at=int(time.time()) - 1))
    with pytest.raises(InvalidToken, match="expired"):
        verify(SECRET, token)


# --- starting an edit -----------------------------------------------------


@pytest.mark.parametrize("headers", [{}, {"X-Role": "viewer"}])
def test_only_admin_can_edit(client: TestClient, headers) -> None:
    doc_id = _doc(client)
    assert client.post(f"/api/documents/{doc_id}/versions/6/edit", headers=headers).status_code == 403


def test_edit_missing_or_failed_version(client: TestClient) -> None:
    doc_id = _doc(client)
    assert client.post(f"/api/documents/{doc_id}/versions/9/edit", headers=ADMIN).status_code == 404
    client.app.state.converter.fail = True
    r = client.post(
        "/api/documents/upload",
        files={"file": ("LIS25 _ Event_Network_Requirements_V7.xlsx", _wb("v7"), XLSX)}, headers=ADMIN,
    )
    assert r.json()["version"]["status"] == "failed"
    assert client.post(f"/api/documents/{doc_id}/versions/7/edit", headers=ADMIN).status_code == 409


def test_editor_unavailable_is_503(client: TestClient) -> None:
    from app.collabora import EditorUnavailable

    class Down(FakeDiscovery):
        def xlsx_edit_urlsrc(self) -> str:
            raise EditorUnavailable("The spreadsheet editor is not reachable")

    doc_id = _doc(client)
    client.app.state.discovery = Down()
    r = client.post(f"/api/documents/{doc_id}/versions/6/edit", headers=ADMIN)
    assert r.status_code == 503


# --- WOPI -----------------------------------------------------------------


def test_check_file_info_and_get_file(client: TestClient) -> None:
    doc_id = _doc(client)
    file_id, token = _start(client, doc_id)

    info = client.get(f"/wopi/files/{file_id}", params={"access_token": token}).json()
    assert info["BaseFileName"] == "LIS25 _ Event_Network_Requirements_V6.xlsx"
    assert info["UserCanWrite"] is True
    assert info["UserId"] == "alice"
    assert info["PostMessageOrigin"] == "http://localhost:5173"
    content = client.get(f"/wopi/files/{file_id}/contents", params={"access_token": token}).content
    assert content == _wb("v6")
    assert info["Size"] == len(content)


def test_save_creates_next_version_and_keeps_original(client: TestClient) -> None:
    doc_id = _doc(client)
    file_id, token = _start(client, doc_id)

    r = client.post(f"/wopi/files/{file_id}/contents", params={"access_token": token}, content=_wb("edited"))

    assert r.status_code == 200, r.text
    assert "LastModifiedTime" in r.json()
    assert _versions(client, doc_id) == [(7, "ready", 6), (6, "ready", None)]
    h = {"X-Role": "viewer"}
    assert client.get(f"/api/documents/{doc_id}/download?format=xlsx", headers=h).content == _wb("edited")
    assert client.get(f"/api/documents/{doc_id}/download?format=xlsx&version=6", headers=h).content == _wb("v6")


def test_later_saves_in_same_session_update_the_same_version(client: TestClient) -> None:
    doc_id = _doc(client)
    file_id, token = _start(client, doc_id)
    for text in ("first save", "autosave", "final save"):
        client.post(f"/wopi/files/{file_id}/contents", params={"access_token": token}, content=_wb(text))

    assert _versions(client, doc_id) == [(7, "ready", 6), (6, "ready", None)]
    xlsx = client.get(f"/api/documents/{doc_id}/download?format=xlsx", headers={"X-Role": "viewer"})
    assert xlsx.content == _wb("final save")
    # The editor now reads back what it saved.
    assert client.get(f"/wopi/files/{file_id}/contents", params={"access_token": token}).content == _wb("final save")


def test_two_sessions_make_two_versions(client: TestClient) -> None:
    doc_id = _doc(client)
    a = _start(client, doc_id)
    b = _start(client, doc_id)
    client.post(f"/wopi/files/{a[0]}/contents", params={"access_token": a[1]}, content=_wb("a"))
    client.post(f"/wopi/files/{b[0]}/contents", params={"access_token": b[1]}, content=_wb("b"))
    assert [v[0] for v in _versions(client, doc_id)] == [8, 7, 6]


def test_editing_an_older_version_saves_after_the_newest(client: TestClient) -> None:
    doc_id = _doc(client, version=1)
    _doc(client, version=2)
    file_id, token = _start(client, doc_id, version=1)
    client.post(f"/wopi/files/{file_id}/contents", params={"access_token": token}, content=_wb("from v1"))
    assert _versions(client, doc_id)[0] == (3, "ready", 1)


def test_upload_of_taken_number_after_edit_is_rejected(client: TestClient) -> None:
    doc_id = _doc(client)
    file_id, token = _start(client, doc_id)
    client.post(f"/wopi/files/{file_id}/contents", params={"access_token": token}, content=_wb("edited"))
    r = client.post(
        "/api/documents/upload",
        files={"file": ("LIS25 _ Event_Network_Requirements_V7.xlsx", _wb("x"), XLSX)}, headers=ADMIN,
    )
    assert r.status_code == 409


def test_wopi_rejects_bad_tokens(client: TestClient) -> None:
    doc_id = _doc(client)
    file_id, token = _start(client, doc_id)
    other_id, other_token = _start(client, doc_id)

    assert client.get(f"/wopi/files/{file_id}", params={"access_token": "nope"}).status_code == 401
    assert client.get(f"/wopi/files/{file_id}", params={"access_token": other_token}).status_code == 401
    assert client.get(f"/wopi/files/{file_id}").status_code == 422
    expired = issue(SECRET, EditGrant(file_id, doc_id, "alice", expires_at=int(time.time()) - 1))
    r = client.post(f"/wopi/files/{file_id}/contents", params={"access_token": expired}, content=_wb("x"))
    assert r.status_code == 401
    assert len(_versions(client, doc_id)) == 1


def test_put_file_rejects_non_xlsx(client: TestClient) -> None:
    doc_id = _doc(client)
    file_id, token = _start(client, doc_id)
    r = client.post(f"/wopi/files/{file_id}/contents", params={"access_token": token}, content=b"not a zip")
    assert r.status_code == 400
    assert len(_versions(client, doc_id)) == 1


def test_wopi_after_document_deleted(client: TestClient) -> None:
    doc_id = _doc(client)
    file_id, token = _start(client, doc_id)
    client.post(f"/api/documents/{doc_id}/archive", headers=ADMIN)
    client.delete(f"/api/documents/{doc_id}", headers=ADMIN)
    assert client.get(f"/wopi/files/{file_id}", params={"access_token": token}).status_code == 404
    r = client.post(f"/wopi/files/{file_id}/contents", params={"access_token": token}, content=_wb("x"))
    assert r.status_code == 404


def test_session_rows_are_removed_with_document(client: TestClient) -> None:
    doc_id = _doc(client)
    _start(client, doc_id)
    client.post(f"/api/documents/{doc_id}/archive", headers=ADMIN)
    client.delete(f"/api/documents/{doc_id}", headers=ADMIN)
    conn = db.connect(client.settings.db_path)
    assert conn.execute("SELECT COUNT(*) FROM edit_sessions").fetchone()[0] == 0
    conn.close()
