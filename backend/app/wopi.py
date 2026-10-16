"""WOPI host endpoints called by Collabora Online (not by the browser).

Only the three operations Collabora needs for editing are implemented:
CheckFileInfo, GetFile and PutFile. Every request carries an ``access_token``
minted by ``POST /api/documents/{id}/versions/{n}/edit``; the WOPI file id is
the edit session id the token was issued for.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from . import db
from .converter import Converter
from .service import ORIGINAL, DocumentGone, save_edit
from .storage import ObjectNotFound, Storage, version_key
from .validation import UploadRejected, validate_upload
from .wopi_tokens import InvalidToken, verify

log = logging.getLogger(__name__)

router = APIRouter(prefix="/wopi")


def _conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = db.connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(_conn)]
Token = Annotated[str, Query(alias="access_token")]


def _authorize(request: Request, conn: sqlite3.Connection, file_id: str, access_token: str):
    try:
        grant = verify(request.app.state.settings.wopi_token_secret, access_token)
    except InvalidToken:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired access token")
    if grant.session_id != file_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token is not valid for this file")
    session = db.get_edit_session(conn, file_id)
    document = db.get_document(conn, grant.document_id) if session else None
    if session is None or document is None or session["document_id"] != grant.document_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return grant, session, document


def _current(conn: sqlite3.Connection, storage: Storage, session: dict[str, Any]) -> tuple[int, bytes, dict]:
    """The session's latest content: its saved version if it has saved, else the version it opened."""
    for number in (session["saved_version"], session["base_version"]):
        if number is None:
            continue
        version = db.get_version(conn, session["document_id"], number)
        if version is None:
            continue
        try:
            return number, storage.get(version_key(session["document_id"], number, ORIGINAL)), version
        except ObjectNotFound:
            continue
    raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")


def _base_file_name(document_name: str, version: int) -> str:
    # "LIS25 | Event_Network_Requirements" -> "LIS25 _ Event_Network_Requirements_V6.xlsx"
    return re.sub(r'[\\/:*?"<>|]', "_", f"{document_name}_V{version}") + ".xlsx"


@router.get("/files/{file_id}")
def check_file_info(request: Request, file_id: str, conn: Conn, access_token: Token) -> dict[str, Any]:
    grant, session, document = _authorize(request, conn, file_id, access_token)
    number, data, version = _current(conn, request.app.state.storage, session)
    return {
        "BaseFileName": _base_file_name(document["name"], number),
        "Size": len(data),
        "Version": version["sha256"],
        "LastModifiedTime": version["uploaded_at"],
        "OwnerId": "portal-pdf-test",
        "UserId": grant.user,
        "UserFriendlyName": grant.user,
        "UserCanWrite": True,
        "UserCanNotWriteRelative": True,  # no "Save As" to another file
        "SupportsUpdate": True,
        "SupportsLocks": False,
        "PostMessageOrigin": request.app.state.settings.app_origin,
    }


@router.get("/files/{file_id}/contents")
def get_file(request: Request, file_id: str, conn: Conn, access_token: Token) -> Response:
    _, session, _ = _authorize(request, conn, file_id, access_token)
    _, data, _ = _current(conn, request.app.state.storage, session)
    return Response(content=data, media_type="application/octet-stream")


@router.post("/files/{file_id}/contents")
async def put_file(request: Request, file_id: str, access_token: Token) -> JSONResponse:
    settings = request.app.state.settings
    data = await request.body()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large")

    def save() -> dict[str, Any]:
        conn = db.connect(settings.db_path)
        try:
            grant, session, _ = _authorize(request, conn, file_id, access_token)
            try:
                validate_upload("edited.xlsx", data, max_bytes=settings.max_upload_bytes)
            except UploadRejected as exc:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
            storage: Storage = request.app.state.storage
            converter: Converter = request.app.state.converter
            try:
                version = save_edit(conn, storage, converter, session=session, data=data, user=grant.user)
            except DocumentGone:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
            log.info(
                "edit session %s saved document %s as V%s (%s, autosave=%s, exit=%s)",
                file_id, session["document_id"], version["version"], version["status"],
                request.headers.get("X-COOL-WOPI-IsAutosave"), request.headers.get("X-COOL-WOPI-IsExitSave"),
            )
            return version
        finally:
            conn.close()

    version = await run_in_threadpool(save)
    return JSONResponse({"LastModifiedTime": version["uploaded_at"]})
