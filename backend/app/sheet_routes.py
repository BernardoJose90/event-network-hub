"""API for the live sheet: the in-app, editable copy of a document's workbook.

Network admins only (TEST ONLY role header, see auth.py).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, ValidationError

from . import db
from .auth import Principal, current_principal, require_admin
from .converter import XLSX_MEDIA_TYPE, ConversionError
from .pdf_prep import prepare_for_pdf
from .routes import _safe_filename
from .service import ORIGINAL
from .sheets.exporter import export_workbook
from .sheets.importer import import_workbook
from .sheets.model import Workbook
from .storage import ObjectNotFound, version_key

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents/{document_id}/sheet")

MAX_SHEET_BODY_BYTES = 5 * 1024 * 1024


def _conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = db.connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(_conn)]


def _admin(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
    require_admin(principal, "use the live sheet")
    return principal


Admin = Annotated[Principal, Depends(_admin)]


def _document(conn: sqlite3.Connection, document_id: int) -> dict[str, Any]:
    document = db.get_document(conn, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return document


def _response(conn: sqlite3.Connection, sheet: dict[str, Any]) -> dict[str, Any]:
    latest = db.latest_version(conn, sheet["document_id"], statuses=(db.STATUS_READY,))
    return {
        "data": Workbook.model_validate_json(sheet["data"]).model_dump(exclude_none=True),
        "revision": sheet["revision"],
        "base_version": sheet["base_version"],
        "latest_version": latest["version"] if latest else None,
        "updated_by": sheet["updated_by"],
        "updated_at": sheet["updated_at"],
    }


@router.get("")
def get_sheet(document_id: int, conn: Conn, principal: Admin) -> dict[str, Any]:
    _document(conn, document_id)
    sheet = db.get_live_sheet(conn, document_id)
    if sheet is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This document has no live sheet yet")
    return _response(conn, sheet)


class StartRequest(BaseModel):
    from_version: int | None = Field(default=None, ge=1)


@router.post("")
def start_sheet(
    request: Request, document_id: int, conn: Conn, principal: Admin, body: StartRequest | None = None
) -> dict[str, Any]:
    """Create the live sheet from a version (latest ready by default), or start it again."""
    _document(conn, document_id)
    if body is not None and body.from_version is not None:
        version = db.get_version(conn, document_id, body.from_version)
        if version is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Version {body.from_version} not found")
    else:
        version = db.latest_version(conn, document_id, statuses=(db.STATUS_READY,))
        if version is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "The document has no ready version to start from")
    if version["status"] != db.STATUS_READY:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Version {version['version']} is {version['status']}")
    try:
        data = request.app.state.storage.get(version_key(document_id, version["version"], ORIGINAL))
    except ObjectNotFound:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Stored workbook is missing")
    try:
        workbook = import_workbook(data)
    except Exception:
        log.exception("could not import document %s V%s into a live sheet", document_id, version["version"])
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "This workbook could not be opened as a live sheet")
    sheet = db.put_live_sheet(
        conn, document_id=document_id, base_version=version["version"],
        data=workbook.model_dump_json(exclude_none=True), user=principal.user,
    )
    log.info("%s started the live sheet of document %s from V%s", principal.user, document_id, version["version"])
    return _response(conn, sheet)


class SaveRequest(BaseModel):
    data: Workbook
    revision: int = Field(ge=1)


@router.put("")
async def save_sheet(request: Request, document_id: int, principal: Admin) -> dict[str, Any]:
    """Save edits. ``revision`` must match the stored one, otherwise 409 (someone else saved)."""
    declared = request.headers.get("content-length")
    if declared and int(declared) > MAX_SHEET_BODY_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "The sheet is too large to save")
    raw = await request.body()
    if len(raw) > MAX_SHEET_BODY_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "The sheet is too large to save")

    try:
        body = SaveRequest.model_validate_json(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Invalid sheet: {first.get('msg', 'bad data')}")

    conn = db.connect(request.app.state.settings.db_path)
    try:
        _document(conn, document_id)
        current = db.get_live_sheet(conn, document_id)
        if current is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "This document has no live sheet yet")
        saved = db.save_live_sheet(
            conn, document_id=document_id, data=body.data.model_dump_json(exclude_none=True),
            expected_revision=body.revision, user=principal.user,
        )
        if saved is None:
            current = db.get_live_sheet(conn, document_id) or current
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{current['updated_by']} changed this sheet at {current['updated_at']}. "
                "Reload to see their changes; your unsaved edits will be lost.",
            )
        return {"revision": saved["revision"], "updated_at": saved["updated_at"]}
    finally:
        conn.close()


@router.get("/download")
def download_sheet(
    request: Request, document_id: int, conn: Conn, principal: Admin,
    format: Annotated[Literal["xlsx", "pdf"], Query()] = "xlsx",
) -> Response:
    document = _document(conn, document_id)
    sheet = db.get_live_sheet(conn, document_id)
    if sheet is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This document has no live sheet yet")
    xlsx = export_workbook(Workbook.model_validate_json(sheet["data"]))
    if format == "pdf":
        try:
            content = request.app.state.converter.xlsx_to_pdf(prepare_for_pdf(xlsx), filename=f"live-{document_id}.xlsx")
        except ConversionError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
        media_type = "application/pdf"
    else:
        content, media_type = xlsx, XLSX_MEDIA_TYPE
    log.info("%s downloaded the live sheet of document %s as %s (revision %s)",
             principal.user, document_id, format, sheet["revision"])
    filename = f"{_safe_filename(document['name'])}-live.{format}"
    return Response(
        content=content, media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )
