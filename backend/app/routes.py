from __future__ import annotations

import logging
import re
import secrets
import sqlite3
import time
from collections.abc import Iterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse

from . import db, wopi_tokens
from .auth import Principal, current_principal, require_admin, require_known_role
from .collabora import EditorUnavailable
from .converter import XLSX_MEDIA_TYPE, Converter
from .naming import parse_upload_name
from .service import FULL_PDF, ORIGINAL, UploadResult, VersionExists, upload_file
from .storage import ObjectNotFound, Storage, document_prefix, version_key
from .validation import UploadRejected, validate_upload

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

READ_CHUNK = 1024 * 1024

# Download format -> (stored file, media type, extension)
DOWNLOAD_FORMATS = {
    "pdf": (FULL_PDF, "application/pdf", "pdf"),
    "xlsx": (ORIGINAL, XLSX_MEDIA_TYPE, "xlsx"),
}


# --- dependencies -----------------------------------------------------------


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = db.connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


def get_converter(request: Request) -> Converter:
    return request.app.state.converter


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]
CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def _document_or_404(conn: sqlite3.Connection, document_id: int) -> dict[str, Any]:
    document = db.get_document(conn, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return document


# --- routes -----------------------------------------------------------------


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _read_validated_upload(request: Request, file: UploadFile) -> bytes:
    max_bytes = request.app.state.settings.max_upload_bytes

    # Read at most max_bytes + 1 so oversized uploads are rejected without buffering them fully.
    buf = bytearray()
    while len(buf) <= max_bytes:
        chunk = file.file.read(READ_CHUNK)
        if not chunk:
            break
        buf.extend(chunk)
    data = bytes(buf)

    try:
        validate_upload(file.filename, data, max_bytes=max_bytes)
    except UploadRejected as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    return data


def _upload_response(result: UploadResult) -> JSONResponse:
    status_code = {
        "created": status.HTTP_201_CREATED,
        "failed": status.HTTP_502_BAD_GATEWAY,
    }[result.outcome]
    return JSONResponse(
        status_code=status_code,
        content={
            "outcome": result.outcome,
            "message": result.message,
            "document": result.document,
            "version": result.version,
        },
    )


@router.get("/documents")
def list_documents(conn: Conn) -> list[dict[str, Any]]:
    return db.list_documents(conn)


@router.post("/documents/upload")
def upload(
    request: Request,
    conn: Conn,
    principal: CurrentPrincipal,
    file: Annotated[UploadFile, File()],
    storage: Annotated[Storage, Depends(get_storage)],
    converter: Annotated[Converter, Depends(get_converter)],
) -> JSONResponse:
    """Upload ``<EVENT> _ Event_Network_Requirements_V<n>.xlsx``.

    The event code picks the document (created on first upload) and ``V<n>``
    becomes that version. A version number can only be uploaded once.
    """
    require_admin(principal, "upload documents")
    data = _read_validated_upload(request, file)
    try:
        parsed = parse_upload_name(file.filename)
    except UploadRejected as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))

    try:
        result = upload_file(
            conn, storage, converter, parsed=parsed, data=data, uploaded_by=principal.user
        )
    except (VersionExists, db.EventArchived) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return _upload_response(result)


@router.get("/documents/{document_id}")
def get_document(document_id: int, conn: Conn) -> dict[str, Any]:
    return _document_or_404(conn, document_id)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: int,
    conn: Conn,
    principal: CurrentPrincipal,
    storage: Annotated[Storage, Depends(get_storage)],
) -> Response:
    """Delete a document with all its versions, stored files and download log rows.

    Events must be archived first, so deleting is always a deliberate second step.
    """
    require_admin(principal, "delete documents")
    document = _document_or_404(conn, document_id)
    if document["kind"] == "event" and not document["archived_at"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "Archive the event before deleting it.")
    # Database first: once the rows are gone nothing can reference the files, so
    # a failure while removing files leaves orphans rather than broken links.
    db.delete_document(conn, document_id)
    try:
        storage.delete_prefix(document_prefix(document_id))
    except Exception:
        log.exception("document %s deleted but its files could not be removed", document_id)
    log.info("document %s deleted by %s", document_id, principal.user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/documents/{document_id}/archive")
def archive_document(document_id: int, conn: Conn, principal: CurrentPrincipal) -> dict[str, Any]:
    """Archive an event: it leaves the main list and becomes read-only. Nothing is deleted."""
    require_admin(principal, "archive events")
    document = _document_or_404(conn, document_id)
    if document["kind"] != "event":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only events can be archived")
    if not document["archived_at"]:
        db.set_document_archived(conn, document_id, user=principal.user)
        log.info("document %s archived by %s", document_id, principal.user)
    return db.get_document(conn, document_id)  # type: ignore[return-value]


@router.post("/documents/{document_id}/restore")
def restore_document(document_id: int, conn: Conn, principal: CurrentPrincipal) -> dict[str, Any]:
    require_admin(principal, "restore events")
    document = _document_or_404(conn, document_id)
    if document["archived_at"]:
        db.set_document_archived(conn, document_id, user=None)
        log.info("document %s restored by %s", document_id, principal.user)
    return db.get_document(conn, document_id)  # type: ignore[return-value]


@router.get("/documents/{document_id}/versions")
def list_versions(document_id: int, conn: Conn) -> list[dict[str, Any]]:
    _document_or_404(conn, document_id)
    return db.list_versions(conn, document_id)


def _safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "document"


@router.post("/documents/{document_id}/versions/{version}/edit")
def start_edit(request: Request, document_id: int, version: int, conn: Conn, principal: CurrentPrincipal) -> dict[str, Any]:
    """Open a version in the in-browser editor. Saving there creates the next version.

    Returns the editor URL and a short-lived access token; the page POSTs the
    token to the URL inside an iframe (the standard WOPI host page flow).
    """
    require_admin(principal, "edit documents")
    _document_or_404(conn, document_id)
    try:
        db.ensure_not_archived(conn, document_id)
    except db.EventArchived as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    row = db.get_version(conn, document_id, version)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Version {version} not found")
    if row["status"] != db.STATUS_READY:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Version {version} is {row['status']}")
    if row["source"] == "publish":
        # Published versions are a fixed record of the requirements; change those and publish again.
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"V{version} was published and can't be edited. Change the requirements and publish again.")

    settings = request.app.state.settings
    discovery = request.app.state.discovery
    try:
        discovery.xlsx_edit_urlsrc()  # fail before creating a session if the editor is down
    except EditorUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))

    session_id = secrets.token_urlsafe(16)
    db.create_edit_session(
        conn, session_id=session_id, document_id=document_id, base_version=version, user=principal.user
    )
    expires_at = int(time.time()) + settings.wopi_token_ttl_seconds
    token = wopi_tokens.issue(
        settings.wopi_token_secret,
        wopi_tokens.EditGrant(session_id=session_id, document_id=document_id, user=principal.user,
                              expires_at=expires_at),
    )
    wopi_src = f"{settings.wopi_base_url}/wopi/files/{session_id}"
    log.info("%s opened document %s V%s for editing (session %s)", principal.user, document_id, version, session_id)
    return {
        "editor_url": discovery.editor_url(wopi_src),
        "access_token": token,
        "access_token_ttl": expires_at * 1000,  # WOPI expects milliseconds since the epoch
        "base_version": version,
    }


@router.get("/documents/{document_id}/download")
def download(
    document_id: int,
    conn: Conn,
    principal: CurrentPrincipal,
    storage: Annotated[Storage, Depends(get_storage)],
    version: Annotated[int | None, Query(ge=1)] = None,
    format: Annotated[Literal["pdf", "xlsx"], Query()] = "pdf",
) -> Response:
    """Download a ready version as the converted PDF or the original .xlsx upload."""
    require_known_role(principal)
    document = _document_or_404(conn, document_id)

    if version is None:
        row = db.latest_version(conn, document_id, statuses=(db.STATUS_READY,))
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No ready version available")
    else:
        row = db.get_version(conn, document_id, version)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Version {version} not found")
        if row["status"] != db.STATUS_READY:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Version {version} is {row['status']}")

    stored_name, media_type, extension = DOWNLOAD_FORMATS[format]
    try:
        content = storage.get(version_key(document_id, row["version"], stored_name))
    except ObjectNotFound:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Stored {extension} file is missing")

    db.log_download(conn, version_id=row["id"], user=principal.user, format=format)

    filename = f"{_safe_filename(document['name'])}-v{row['version']}.{extension}"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Document-Version": str(row["version"]),
        },
    )
