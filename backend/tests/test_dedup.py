from __future__ import annotations

import hashlib
import sqlite3

import pytest
from openpyxl import Workbook

from app import db, service
from app.service import upload_version
from app.storage import version_key

from .conftest import FakeConverter, xlsx_bytes


def _wb(text: str) -> bytes:
    def build(wb: Workbook) -> None:
        wb.active["A1"] = text

    return xlsx_bytes(build)


def _upload(conn, storage, converter, document, data):
    return upload_version(conn, storage, converter, document=document, data=data, uploaded_by="tester")


def test_first_upload_creates_version_one(conn, storage, converter, document) -> None:
    data = _wb("v1")
    result = _upload(conn, storage, converter, document, data)

    assert result.outcome == "created"
    v = result.version
    assert (v["version"], v["status"], v["uploaded_by"]) == (1, "ready", "tester")
    assert v["sha256"] == hashlib.sha256(data).hexdigest()
    for name in ("original.xlsx", "full.pdf"):
        assert storage.get(version_key(document["id"], 1, name))
    assert storage.get(version_key(document["id"], 1, "original.xlsx")) == data


def test_same_file_twice_is_unchanged(conn, storage, converter, document) -> None:
    data = _wb("v1")
    _upload(conn, storage, converter, document, data)
    calls_before = len(converter.calls)

    result = _upload(conn, storage, converter, document, data)

    assert result.outcome == "unchanged"
    assert result.version["version"] == 1
    assert "unchanged" in result.message
    assert len(db.list_versions(conn, document["id"])) == 1
    assert len(converter.calls) == calls_before  # no conversion work done


def test_changed_file_creates_next_version(conn, storage, converter, document) -> None:
    _upload(conn, storage, converter, document, _wb("v1"))
    result = _upload(conn, storage, converter, document, _wb("v2"))

    assert result.outcome == "created"
    assert result.version["version"] == 2


def test_reverting_to_older_content_is_a_new_version(conn, storage, converter, document) -> None:
    _upload(conn, storage, converter, document, _wb("v1"))
    _upload(conn, storage, converter, document, _wb("v2"))
    result = _upload(conn, storage, converter, document, _wb("v1"))

    assert result.outcome == "created"
    assert result.version["version"] == 3


def test_same_file_after_failed_conversion_is_retried(conn, storage, converter, document) -> None:
    data = _wb("v1")
    converter.fail = True
    failed = _upload(conn, storage, converter, document, data)
    assert failed.outcome == "failed"
    assert failed.version["status"] == "failed"

    converter.fail = False
    retried = _upload(conn, storage, converter, document, data)
    assert retried.outcome == "created"
    assert retried.version["version"] == 2


def test_failed_conversion_keeps_previous_ready_version(conn, storage, converter, document) -> None:
    _upload(conn, storage, converter, document, _wb("v1"))
    converter.fail = True
    result = _upload(conn, storage, converter, document, _wb("v2"))

    assert result.outcome == "failed"
    assert "previous ready version" in result.message
    latest_ready = db.latest_version(conn, document["id"], statuses=(db.STATUS_READY,))
    assert latest_ready["version"] == 1


def test_version_number_race_is_retried(conn, storage, converter, document, monkeypatch) -> None:
    _upload(conn, storage, converter, document, _wb("v1"))

    # Simulate a concurrent upload: the first read of the next number is stale.
    real = db.next_version_number
    stale = iter([1])
    monkeypatch.setattr(db, "next_version_number", lambda c, d: next(stale, None) or real(c, d))

    result = _upload(conn, storage, converter, document, _wb("v2"))
    assert result.outcome == "created"
    assert result.version["version"] == 2


def test_version_allocation_gives_up_eventually(conn, document, monkeypatch) -> None:
    db.insert_version(conn, document_id=document["id"], version=1, sha256="x",
                      uploaded_by="t", warnings=[])
    monkeypatch.setattr(db, "next_version_number", lambda c, d: 1)

    with pytest.raises(service.VersionAllocationError):
        service.allocate_version(conn, document_id=document["id"], sha256="y",
                                 uploaded_by="t", warnings=[])


def test_unique_constraint_enforced(conn, document) -> None:
    db.insert_version(conn, document_id=document["id"], version=1, sha256="x",
                      uploaded_by="t", warnings=[])
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_version(conn, document_id=document["id"], version=1, sha256="y",
                          uploaded_by="t", warnings=[])


def test_warnings_are_stored_on_version(conn, storage, converter: FakeConverter, document) -> None:
    def build(wb: Workbook) -> None:
        wb.active.title = "Sheet1"
        wb.active["B2"] = "#REF!"

    result = _upload(conn, storage, converter, document, xlsx_bytes(build))
    assert result.version["warnings"] == [{"sheet": "Sheet1", "cell": "B2", "error": "#REF!"}]


def test_document_deleted_during_upload(conn, storage, converter, document, monkeypatch) -> None:
    db.delete_document(conn, document["id"])
    monkeypatch.setattr(service, "find_duplicate", lambda *a: None)

    with pytest.raises(service.DocumentGone):
        _upload(conn, storage, converter, document, _wb("v1"))
