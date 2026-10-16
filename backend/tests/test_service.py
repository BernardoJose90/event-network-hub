from __future__ import annotations

from functools import lru_cache

import hashlib

import pytest
from openpyxl import Workbook

from app import db
from app.naming import ParsedName
from app.service import DocumentGone, VersionExists, upload_file
from app.storage import ObjectNotFound, version_key

from .conftest import xlsx_bytes


@lru_cache(maxsize=None)  # same bytes every time: saved workbooks embed the current time
def _wb(text: str) -> bytes:
    def build(wb: Workbook) -> None:
        wb.active["A1"] = text

    return xlsx_bytes(build)


def _upload(conn, storage, converter, data, event="LIS26", version=1):
    return upload_file(conn, storage, converter, parsed=ParsedName(event, version),
                       data=data, uploaded_by="tester")


def test_first_upload_creates_document_and_version(conn, storage, converter) -> None:
    data = _wb("v1")
    result = _upload(conn, storage, converter, data)

    assert result.outcome == "created"
    assert result.document["name"] == "LIS26 | Event_Network_Requirements"
    v = result.version
    assert (v["version"], v["status"], v["uploaded_by"]) == (1, "ready", "tester")
    assert v["sha256"] == hashlib.sha256(data).hexdigest()
    doc_id = result.document["id"]
    assert storage.get(version_key(doc_id, 1, "original.xlsx")) == data
    assert storage.get(version_key(doc_id, 1, "full.pdf")).startswith(b"%PDF")


def test_new_version_number_updates_same_document(conn, storage, converter) -> None:
    first = _upload(conn, storage, converter, _wb("v1"), version=1)
    second = _upload(conn, storage, converter, _wb("v2"), version=2)

    assert second.document["id"] == first.document["id"]
    assert [v["version"] for v in db.list_versions(conn, first.document["id"])] == [2, 1]


def test_other_event_gets_its_own_document(conn, storage, converter) -> None:
    a = _upload(conn, storage, converter, _wb("a"), event="LIS26")
    b = _upload(conn, storage, converter, _wb("b"), event="LIS27")
    assert a.document["id"] != b.document["id"]


@pytest.mark.parametrize("same_content", [True, False])
def test_same_version_number_twice_is_rejected(conn, storage, converter, same_content) -> None:
    _upload(conn, storage, converter, _wb("v1"), version=1)
    calls = len(converter.calls)

    with pytest.raises(VersionExists, match="V1 of LIS26 | Event_Network_Requirements has already been uploaded"):
        _upload(conn, storage, converter, _wb("v1" if same_content else "changed"), version=1)

    assert len(converter.calls) == calls  # nothing converted
    assert len(db.list_versions(conn, 1)) == 1


def test_same_content_under_new_version_number_is_allowed(conn, storage, converter) -> None:
    _upload(conn, storage, converter, _wb("same"), version=1)
    assert _upload(conn, storage, converter, _wb("same"), version=2).outcome == "created"


def test_versions_may_arrive_out_of_order(conn, storage, converter) -> None:
    _upload(conn, storage, converter, _wb("v3"), version=3)
    result = _upload(conn, storage, converter, _wb("v1"), version=1)
    assert result.outcome == "created"
    latest = db.latest_version(conn, result.document["id"], statuses=(db.STATUS_READY,))
    assert latest["version"] == 3


def test_failed_version_can_be_uploaded_again(conn, storage, converter) -> None:
    converter.fail = True
    failed = _upload(conn, storage, converter, _wb("v1"))
    assert failed.outcome == "failed"
    assert "Upload the same file again" in failed.message
    doc_id = failed.document["id"]
    assert storage.get(version_key(doc_id, 1, "original.xlsx"))

    converter.fail = False
    retried = _upload(conn, storage, converter, _wb("v1 fixed"))

    assert retried.outcome == "created"
    versions = db.list_versions(conn, doc_id)
    assert [(v["version"], v["status"]) for v in versions] == [(1, "ready")]
    assert storage.get(version_key(doc_id, 1, "original.xlsx")) == _wb("v1 fixed")


def test_failed_conversion_keeps_previous_ready_version(conn, storage, converter) -> None:
    first = _upload(conn, storage, converter, _wb("v1"), version=1)
    converter.fail = True
    assert _upload(conn, storage, converter, _wb("v2"), version=2).outcome == "failed"

    latest = db.latest_version(conn, first.document["id"], statuses=(db.STATUS_READY,))
    assert latest["version"] == 1


def test_concurrent_duplicate_caught_by_unique_constraint(conn, storage, converter, monkeypatch) -> None:
    _upload(conn, storage, converter, _wb("v1"))
    # Simulate the other upload's row appearing between our check and our insert.
    monkeypatch.setattr(db, "get_version", lambda *a: None)
    with pytest.raises(VersionExists):
        _upload(conn, storage, converter, _wb("again"))


def test_document_deleted_during_upload(conn, storage, converter, monkeypatch) -> None:
    doc = db.create_document(conn, "LIS26 | Event_Network_Requirements")
    monkeypatch.setattr("app.service.find_or_create_document", lambda c, n: doc)
    db.delete_document(conn, doc["id"])

    with pytest.raises(DocumentGone):
        _upload(conn, storage, converter, _wb("v1"))


def test_warnings_are_stored_on_version(conn, storage, converter) -> None:
    def build(wb: Workbook) -> None:
        wb.active.title = "Sheet1"
        wb.active["B2"] = "#REF!"

    result = _upload(conn, storage, converter, xlsx_bytes(build))
    assert result.version["warnings"] == [{"sheet": "Sheet1", "cell": "B2", "error": "#REF!"}]


def test_unique_constraint_enforced(conn, document) -> None:
    import sqlite3

    db.insert_version(conn, document_id=document["id"], version=1, sha256="x", uploaded_by="t", warnings=[])
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_version(conn, document_id=document["id"], version=1, sha256="y", uploaded_by="t", warnings=[])


def test_missing_object_raises(storage) -> None:
    with pytest.raises(ObjectNotFound):
        storage.get(version_key(1, 1, "full.pdf"))
