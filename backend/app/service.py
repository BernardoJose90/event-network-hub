"""Upload pipeline: find or create the document, add the version, convert, store."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass
from typing import Any

from . import db
from .converter import ConversionError, Converter
from .naming import ParsedName
from .pdf_prep import prepare_for_pdf
from .storage import Storage, version_key
from .xlsx import find_formula_errors

log = logging.getLogger(__name__)

ORIGINAL = "original.xlsx"
FULL_PDF = "full.pdf"


class VersionExists(Exception):
    """This version number was already uploaded for the document. ``str(exc)`` is user-facing."""


class DocumentGone(LookupError):
    """The document was deleted while a version was being uploaded."""


@dataclass
class UploadResult:
    outcome: str  # "created" | "failed"
    document: dict[str, Any]
    version: dict[str, Any]
    message: str


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def find_or_create_document(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    existing = db.get_document_by_name(conn, name)
    if existing is not None:
        return existing
    try:
        return db.create_document(conn, name)
    except sqlite3.IntegrityError:
        # A concurrent upload created it first.
        return db.get_document_by_name(conn, name)  # type: ignore[return-value]


def _claim_version(
    conn: sqlite3.Connection,
    storage: Storage,
    *,
    document: dict[str, Any],
    version: int,
    sha256: str,
    uploaded_by: str,
    warnings: list[dict[str, str]],
) -> dict[str, Any]:
    """Insert the version row, refusing a version number that was already uploaded.

    A version whose conversion failed may be uploaded again: the failed row and
    its files are replaced.
    """
    label = f"V{version} of {document['name']}"
    existing = db.get_version(conn, document["id"], version)
    if existing is not None:
        if existing["status"] != db.STATUS_FAILED:
            raise VersionExists(f"{label} has already been uploaded. Upload a new version number instead.")
        db.delete_version(conn, existing["id"])
        storage.delete_prefix(version_key(document["id"], version, ""))
    try:
        return db.insert_version(
            conn,
            document_id=document["id"],
            version=version,
            sha256=sha256,
            uploaded_by=uploaded_by,
            warnings=warnings,
        )
    except sqlite3.IntegrityError:
        if db.get_document(conn, document["id"]) is None:
            raise DocumentGone(document["id"])
        # The UNIQUE (document_id, version) constraint caught a concurrent upload.
        raise VersionExists(f"{label} has already been uploaded. Upload a new version number instead.")


def upload_file(
    conn: sqlite3.Connection,
    storage: Storage,
    converter: Converter,
    *,
    parsed: ParsedName,
    data: bytes,
    uploaded_by: str,
) -> UploadResult:
    """Store a validated .xlsx as version ``parsed.version`` of its event's document.

    The caller must have validated ``data`` and parsed the file name already.
    Raises VersionExists if that version number is already taken.
    """
    warnings = find_formula_errors(data)
    document = find_or_create_document(conn, parsed.document_name)
    db.ensure_not_archived(conn, document["id"])
    version = _claim_version(
        conn, storage, document=document, version=parsed.version, sha256=sha256_hex(data),
        uploaded_by=uploaded_by, warnings=warnings,
    )
    reason = store_and_convert(conn, storage, converter, document_id=document["id"], version=version, data=data)
    result = dict(
        document=db.get_document(conn, document["id"]),
        version=db.get_version_by_id(conn, version["id"]),
    )
    n = parsed.version
    if reason is not None:
        return UploadResult(
            outcome="failed", message=f"V{n} failed: {reason}. Upload the same file again to retry.", **result
        )
    return UploadResult(outcome="created", message=f"V{n} added to {document['name']}.", **result)


def store_and_convert(
    conn: sqlite3.Connection,
    storage: Storage,
    converter: Converter,
    *,
    document_id: int,
    version: dict[str, Any],
    data: bytes,
) -> str | None:
    """Store the workbook and its PDF for ``version`` and mark it ready.

    Returns None on success, or the user-facing reason after marking the version failed.
    """
    n = version["version"]
    try:
        storage.put(version_key(document_id, n, ORIGINAL), data)
        pdf = converter.xlsx_to_pdf(prepare_for_pdf(data), filename=f"document-{document_id}-v{n}.xlsx")
        storage.put(version_key(document_id, n, FULL_PDF), pdf)
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ConversionError) else "Internal error while processing the file"
        log.exception("processing document %s version %s failed", document_id, n)
        db.set_version_status(conn, version["id"], db.STATUS_FAILED, error=reason)
        return reason
    db.set_version_status(conn, version["id"], db.STATUS_READY)
    return None


# --- versions created by Publish ---------------------------------------------

MAX_VERSION_ALLOCATION_ATTEMPTS = 5


def allocate_next_version(
    conn: sqlite3.Connection, *, document_id: int, sha256: str, user: str,
    warnings: list[dict[str, str]], edited_from: int | None, source: str,
) -> dict[str, Any]:
    """Insert the version after the newest one, retrying if a concurrent save or upload took it."""
    db.ensure_not_archived(conn, document_id)
    for _ in range(MAX_VERSION_ALLOCATION_ATTEMPTS):
        number = db.max_version(conn, document_id) + 1
        try:
            return db.insert_version(
                conn, document_id=document_id, version=number, sha256=sha256,
                uploaded_by=user, warnings=warnings, edited_from=edited_from, source=source,
            )
        except sqlite3.IntegrityError:
            if db.get_document(conn, document_id) is None:
                raise DocumentGone(document_id)
    raise RuntimeError("could not allocate a version number")
