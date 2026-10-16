"""Upload pipeline: dedup, version allocation, conversion, storage."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass
from typing import Any

from . import db
from .converter import ConversionError, Converter
from .pdf_prep import prepare_for_pdf
from .storage import Storage, version_key
from .xlsx import find_formula_errors

log = logging.getLogger(__name__)

ORIGINAL = "original.xlsx"
FULL_PDF = "full.pdf"

MAX_VERSION_ALLOCATION_ATTEMPTS = 5


class VersionAllocationError(RuntimeError):
    pass


class DocumentGone(LookupError):
    """The document was deleted while a version was being uploaded."""


@dataclass
class UploadResult:
    outcome: str  # "created" | "unchanged" | "failed"
    version: dict[str, Any]
    message: str


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def find_duplicate(conn: sqlite3.Connection, document_id: int, digest: str) -> dict[str, Any] | None:
    """Return the latest non-failed version if it has the same content, else None.

    Failed versions are ignored so the same file can be re-uploaded to retry
    a conversion that failed for transient reasons.
    """
    latest = db.latest_version(
        conn, document_id, statuses=(db.STATUS_READY, db.STATUS_PROCESSING)
    )
    if latest is not None and latest["sha256"] == digest:
        return latest
    return None


def allocate_version(
    conn: sqlite3.Connection,
    *,
    document_id: int,
    sha256: str,
    uploaded_by: str,
    warnings: list[dict[str, str]],
) -> dict[str, Any]:
    """Insert the next version number, retrying if a concurrent upload took it first."""
    for attempt in range(1, MAX_VERSION_ALLOCATION_ATTEMPTS + 1):
        number = db.next_version_number(conn, document_id)
        try:
            return db.insert_version(
                conn,
                document_id=document_id,
                version=number,
                sha256=sha256,
                uploaded_by=uploaded_by,
                warnings=warnings,
            )
        except sqlite3.IntegrityError:
            if db.get_document(conn, document_id) is None:
                raise DocumentGone(document_id)
            log.info("version %s of document %s already taken (attempt %s), retrying",
                     number, document_id, attempt)
    raise VersionAllocationError("Could not allocate a version number; please retry the upload.")


def upload_version(
    conn: sqlite3.Connection,
    storage: Storage,
    converter: Converter,
    *,
    document: dict[str, Any],
    data: bytes,
    uploaded_by: str,
) -> UploadResult:
    """Store a validated .xlsx as a new version and generate its PDFs.

    The caller must have validated ``data`` already.
    """
    document_id = document["id"]
    digest = sha256_hex(data)

    duplicate = find_duplicate(conn, document_id, digest)
    if duplicate is not None:
        return UploadResult(
            outcome="unchanged",
            version=duplicate,
            message=f"File is unchanged from version {duplicate['version']}; no new version created.",
        )

    warnings = find_formula_errors(data)

    version = allocate_version(
        conn, document_id=document_id, sha256=digest, uploaded_by=uploaded_by, warnings=warnings
    )
    n = version["version"]
    try:
        storage.put(version_key(document_id, n, ORIGINAL), data)
        pdf = converter.xlsx_to_pdf(prepare_for_pdf(data), filename=f"{document['name']}-v{n}.xlsx")
        storage.put(version_key(document_id, n, FULL_PDF), pdf)
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ConversionError) else "Internal error while processing the file"
        log.exception("processing document %s version %s failed", document_id, n)
        db.set_version_status(conn, version["id"], db.STATUS_FAILED, error=reason)
        return UploadResult(
            outcome="failed",
            version=db.get_version_by_id(conn, version["id"]),  # type: ignore[arg-type]
            message=f"Version {n} failed: {reason}. The previous ready version is still served.",
        )

    db.set_version_status(conn, version["id"], db.STATUS_READY)
    return UploadResult(
        outcome="created",
        version=db.get_version_by_id(conn, version["id"]),  # type: ignore[arg-type]
        message=f"Version {n} created.",
    )
