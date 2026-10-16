from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import db
from .auth import Principal, current_principal, require_known_role
from .converter import Converter
from .service import FULL_PDF, REDACTED_PDF, upload_version
from .storage import ObjectNotFound, Storage, version_key
from .validation import UploadRejected, validate_upload

router = APIRouter(prefix="/api")

READ_CHUNK = 1024 * 1024


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


class DocumentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.get("/documents")
def list_documents(conn: Conn) -> list[dict[str, Any]]:
    return db.list_documents(conn)


@router.post("/documents", status_code=status.HTTP_201_CREATED)
def create_document(body: DocumentCreate, conn: Conn) -> dict[str, Any]:
    name = body.name.strip()
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Name must not be blank")
    try:
        return db.create_document(conn, name)
    except sqlite3.IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A document named {name!r} already exists")


@router.get("/documents/{document_id}")
def get_document(document_id: int, conn: Conn) -> dict[str, Any]:
    return _document_or_404(conn, document_id)


@router.get("/documents/{document_id}/versions")
def list_versions(document_id: int, conn: Conn) -> list[dict[str, Any]]:
    _document_or_404(conn, document_id)
    return db.list_versions(conn, document_id)


@router.post("/documents/{document_id}/versions")
def upload(
    request: Request,
    document_id: int,
    conn: Conn,
    principal: CurrentPrincipal,
    file: Annotated[UploadFile, File()],
    storage: Annotated[Storage, Depends(get_storage)],
    converter: Annotated[Converter, Depends(get_converter)],
) -> JSONResponse:
    document = _document_or_404(conn, document_id)
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

    result = upload_version(
        conn, storage, converter, document=document, data=data, uploaded_by=principal.user
    )
    status_code = {
        "created": status.HTTP_201_CREATED,
        "unchanged": status.HTTP_200_OK,
        "failed": status.HTTP_502_BAD_GATEWAY,
    }[result.outcome]
    return JSONResponse(
        status_code=status_code,
        content={
            "outcome": result.outcome,
            "message": result.message,
            "version": result.version,
            "redacted_cells": result.redacted_cells,
        },
    )


def _safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "document"


@router.get("/documents/{document_id}/download")
def download(
    document_id: int,
    conn: Conn,
    principal: CurrentPrincipal,
    storage: Annotated[Storage, Depends(get_storage)],
    version: Annotated[int | None, Query(ge=1)] = None,
) -> Response:
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

    redacted = not principal.sees_unredacted
    key = version_key(document_id, row["version"], REDACTED_PDF if redacted else FULL_PDF)
    try:
        pdf = storage.get(key)
    except ObjectNotFound:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Stored PDF is missing")

    db.log_download(conn, version_id=row["id"], user=principal.user, redacted=redacted)

    suffix = "-redacted" if redacted else ""
    filename = f"{_safe_filename(document['name'])}-v{row['version']}{suffix}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Document-Version": str(row["version"]),
            "X-Redacted": "true" if redacted else "false",
        },
    )
