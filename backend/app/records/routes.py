"""API for an event's structured requirements: /api/events/{document_id}/...

Reads: viewer or network-admin. Changes: network-admin only (TEST ONLY role
header, see auth.py). Every change is recorded in the change log.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from typing import Annotated, Any, Literal, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from .. import db
from ..naming import event_document_name, short_code
from ..validation import UploadRejected
from ..auth import ROLE_NETWORK_ADMIN, Principal, current_principal, require_admin, require_known_role
from ..service import ORIGINAL
from ..storage import ObjectNotFound, version_key
from . import published, service, store
from .importer import import_workbook
from .models import ListValue, NamedItem, NewRecord, RecordMove, RecordPatch
from .publish import SKIPPED_ACTIONS, changelog_between, changes_since_last_publish, publish

log = logging.getLogger(__name__)

def _not_archived(request: Request, document_id: int) -> None:
    """Archived events are read-only: every change is refused (revealing a password is a read)."""
    if request.method in ("GET", "HEAD") or request.url.path.endswith("/reveal"):
        return
    conn = db.connect(request.app.state.settings.database_url)
    try:
        db.ensure_not_archived(conn, document_id)
    except db.EventArchived as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    finally:
        conn.close()


router = APIRouter(prefix="/api/events/{document_id}", dependencies=[Depends(_not_archived)])
events_router = APIRouter(prefix="/api")  # creating events, event templates

RecordTypePath = Literal["wifi", "ethernet", "partner"]
ListKindPath = Literal["building", "location", "vlan", "bandwidth"]
GroupKindPath = Literal["wifi", "ethernet"]
T = TypeVar("T")


def _conn(request: Request) -> Iterator[db.Connection]:
    conn = db.connect(request.app.state.settings.database_url)
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[db.Connection, Depends(_conn)]


def _reader(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
    require_known_role(principal)
    return principal


def _editor(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
    require_admin(principal, "change requirements")
    return principal


Reader = Annotated[Principal, Depends(_reader)]
Editor = Annotated[Principal, Depends(_editor)]


def _document(conn: db.Connection, document_id: int) -> dict[str, Any]:
    document = db.get_document(conn, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return document


def _run(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except service.RecordError as exc:
        raise HTTPException(exc.status, str(exc))


# --- new events, templates ----------------------------------------------------------


def _name(fn: Callable[[], str]) -> str:
    try:
        return fn()
    except UploadRejected as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


class NewEvent(BaseModel):
    event_code: str = Field(max_length=40)
    template_id: int | None = None


@events_router.post("/events", status_code=status.HTTP_201_CREATED)
def create_event(body: NewEvent, conn: Conn, principal: Editor) -> dict[str, Any]:
    """Create an event without uploading a workbook: empty, or from a template."""
    name = _name(lambda: event_document_name(body.event_code))
    doc = _run(lambda: service.create_event(conn, name, user=principal.user, template_id=body.template_id))
    log.info("%s created event %s (document %s, template %s)", principal.user, name, doc, body.template_id)
    return db.get_document(conn, doc)  # type: ignore[return-value]


@events_router.get("/templates")
def list_templates(conn: Conn, principal: Reader) -> list[dict[str, Any]]:
    """Event templates, for "Start from"."""
    return [{"id": d["id"], "name": d["name"], "created_at": d["created_at"]}
            for d in db.list_documents(conn, kind="template")]


class NewTemplate(BaseModel):
    name: str = Field(max_length=40)
    event_id: int


@events_router.post("/templates", status_code=status.HTTP_201_CREATED)
def create_template(body: NewTemplate, conn: Conn, principal: Editor) -> dict[str, Any]:
    """Save an event as a template (Ethernet points 0, no partner orders, no Wi-Fi passwords)."""
    name = _name(lambda: short_code(body.name, "template name", "LIS"))
    doc = _run(lambda: service.save_template(conn, name, user=principal.user, event_id=body.event_id))
    log.info("%s saved document %s as template %s (document %s)", principal.user, body.event_id, name, doc)
    return db.get_document(conn, doc)  # type: ignore[return-value]


@events_router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(template_id: int, conn: Conn, principal: Editor) -> None:
    document = db.get_document(conn, template_id)
    if document is None or document["kind"] != "template":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found")
    db.delete_document(conn, template_id)  # templates have no stored files
    log.info("%s deleted template %s", principal.user, document["name"])


# --- overview, history ---------------------------------------------------------


def _viewer_view(conn: db.Connection, document_id: int, principal: Principal) -> dict[str, Any] | None:
    """Viewers see the latest published version (None: an admin, or nothing published yet)."""
    if principal.role == ROLE_NETWORK_ADMIN:
        return None
    return published.latest(conn, document_id)


@router.get("/overview")
def overview(document_id: int, conn: Conn, principal: Reader) -> dict[str, Any]:
    from .comments import open_counts  # comments imports this module

    document = _document(conn, document_id)
    open_comments = open_counts(conn, document_id)["open"] if document["kind"] == "event" else 0
    view = _viewer_view(conn, document_id, principal)
    if view is not None:
        latest = db.latest_version(conn, document_id, statuses=(db.STATUS_READY,))
        return {
            "document": document, "has_records": bool(view["counts"]), "counts": view["counts"],
            "venues": view["venues"], "groups": view["groups"], "lists": view["lists"], "flagged_list_values": 0,
            "totals": view["totals"], "unpublished_changes": 0,
            "latest_version": latest["version"] if latest else None, "open_comments": open_comments,
            "showing": {"version": view["version"], "published_at": view["published_at"]},
        }
    counts: dict[str, int] = {}
    for record in store.records(conn, document_id):
        counts[record["type"]] = counts.get(record["type"], 0) + 1
    last_publish = store.last_publish_log_id(conn, document_id)
    unpublished = [e for e in store.history(conn, document_id, after_id=last_publish or 0, limit=10_000)
                   if e["action"] not in SKIPPED_ACTIONS]
    latest = db.latest_version(conn, document_id, statuses=(db.STATUS_READY,))
    lists = store.lists(conn, document_id)
    return {
        "document": document,
        "has_records": bool(counts),
        "counts": counts,
        "venues": store.venues(conn, document_id),
        "groups": store.groups(conn, document_id),
        "lists": lists,
        "flagged_list_values": sum(1 for i in lists if i["flagged"]),
        "totals": service.totals(conn, document_id),
        "unpublished_changes": len(unpublished),
        "latest_version": latest["version"] if latest else None,
        "open_comments": open_comments,
        "showing": None,  # the live requirements (admins, or nothing published yet)
    }


@router.get("/history")
def history(document_id: int, conn: Conn, principal: Reader,
            entity: Annotated[str | None, Query(max_length=40)] = None,
            user: Annotated[str | None, Query(max_length=100)] = None,
            since_publish: bool = False,
            limit: Annotated[int, Query(ge=1, le=2000)] = 500) -> list[dict[str, Any]]:
    _document(conn, document_id)
    after = store.last_publish_log_id(conn, document_id) if since_publish else None
    return store.history(conn, document_id, entity=entity, user=user, after_id=after, limit=limit)


# --- import ----------------------------------------------------------------------


class ImportRequest(BaseModel):
    version: int | None = Field(default=None, ge=1)
    replace: bool = False


@router.post("/import")
def import_version(request: Request, document_id: int, conn: Conn, principal: Editor,
                   body: ImportRequest | None = None) -> dict[str, Any]:
    """Create the event's records from one of its versions (latest ready by default)."""
    body = body or ImportRequest()
    _document(conn, document_id)
    if body.version is not None:
        version = db.get_version(conn, document_id, body.version)
        if version is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Version {body.version} not found")
    else:
        version = db.latest_version(conn, document_id, statuses=(db.STATUS_READY,))
        if version is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Upload a workbook first")
    try:
        data = request.app.state.storage.get(version_key(document_id, version["version"], ORIGINAL))
    except ObjectNotFound:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Stored workbook is missing")
    try:
        result = import_workbook(data)
    except Exception:
        log.exception("could not read document %s V%s for import", document_id, version["version"])
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "This workbook could not be read")
    outcome = _run(lambda: service.apply_import(conn, document_id, result, user=principal.user,
                                                source=f"V{version['version']}", replace=body.replace))
    log.info("%s imported document %s V%s into records: %s", principal.user, document_id,
             version["version"], outcome["counts"])
    return outcome


# --- records -------------------------------------------------------------------------


@router.get("/records/{record_type}")
def list_records(document_id: int, record_type: RecordTypePath, conn: Conn, principal: Reader,
                 ) -> list[dict[str, Any]]:
    _document(conn, document_id)
    view = _viewer_view(conn, document_id, principal)
    if view is not None:
        return published.records(view, record_type)
    records = store.records(conn, document_id, record_type)
    numbers = service.row_numbers(records)  # the ID column: row numbers in display order
    return [service.public(r) | {"data": service.public(r)["data"] | {"ref_id": numbers[r["id"]]}}
            if r["id"] in numbers else service.public(r) for r in records]


@router.post("/records/{record_type}", status_code=status.HTTP_201_CREATED)
def create_record(document_id: int, record_type: RecordTypePath, body: NewRecord, conn: Conn,
                  principal: Editor) -> dict[str, Any]:
    _document(conn, document_id)
    record = _run(lambda: service.create_record(
        conn, document_id, record_type, venue_id=body.venue_id, group_id=body.group_id,
        before_id=body.before_id, data=body.data, user=principal.user))
    return service.public(record)


@router.patch("/records/{record_type}/{record_id}")
def update_record(document_id: int, record_type: RecordTypePath, record_id: int, body: RecordPatch,
                  conn: Conn, principal: Editor) -> dict[str, Any]:
    _document(conn, document_id)
    record = _run(lambda: service.update_record(conn, document_id, record_type, record_id,
                                                revision=body.revision, changes=body.data, user=principal.user))
    return service.public(record)


@router.delete("/records/{record_type}/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_record(document_id: int, record_type: RecordTypePath, record_id: int, conn: Conn, principal: Editor,
                  revision: Annotated[int, Query(ge=1)]) -> None:
    _document(conn, document_id)
    _run(lambda: service.delete_record(conn, document_id, record_type, record_id, revision=revision,
                                       user=principal.user))


@router.post("/records/{record_type}/{record_id}/move")
def move_record(document_id: int, record_type: RecordTypePath, record_id: int, body: RecordMove, conn: Conn,
                principal: Editor) -> dict[str, Any]:
    _document(conn, document_id)
    record = _run(lambda: service.move_record(conn, document_id, record_type, record_id, revision=body.revision,
                                              before_id=body.before_id, group_id=body.group_id,
                                              user=principal.user))
    return service.public(record)


@router.post("/records/wifi/{record_id}/reveal")
def reveal_password(document_id: int, record_id: int, conn: Conn, principal: Reader) -> dict[str, Any]:
    """Return a Wi-Fi password and log who revealed it (viewers: the published one)."""
    _document(conn, document_id)
    view = _viewer_view(conn, document_id, principal)
    if view is not None:
        return {"password": _run(lambda: service.reveal_published_secret(
            conn, document_id, published.wifi(view, record_id), user=principal.user))}
    return {"password": _run(lambda: service.reveal_secret(conn, document_id, record_id, user=principal.user))}


class RevisionOnly(BaseModel):
    revision: int = Field(ge=1)


# --- lists, venues, groups -------------------------------------------------------------


@router.post("/lists/{kind}", status_code=status.HTTP_201_CREATED)
def add_list_value(document_id: int, kind: ListKindPath, body: ListValue, conn: Conn, principal: Editor) -> list:
    _document(conn, document_id)
    _run(lambda: service.add_list_value(conn, document_id, kind, body.value, user=principal.user))
    return store.lists(conn, document_id)


@router.patch("/lists/item/{item_id}")
def rename_list_value(document_id: int, item_id: int, body: ListValue, conn: Conn, principal: Editor) -> list:
    _document(conn, document_id)
    _run(lambda: service.rename_list_value(conn, document_id, item_id, body.value, user=principal.user))
    return store.lists(conn, document_id)


@router.post("/lists/item/{item_id}/accept")
def accept_list_value(document_id: int, item_id: int, conn: Conn, principal: Editor) -> list:
    _document(conn, document_id)
    _run(lambda: service.accept_list_value(conn, document_id, item_id, user=principal.user))
    return store.lists(conn, document_id)


@router.delete("/lists/item/{item_id}")
def delete_list_value(document_id: int, item_id: int, conn: Conn, principal: Editor) -> list:
    _document(conn, document_id)
    _run(lambda: service.delete_list_value(conn, document_id, item_id, user=principal.user))
    return store.lists(conn, document_id)


@router.post("/venues", status_code=status.HTTP_201_CREATED)
def add_venue(document_id: int, body: NamedItem, conn: Conn, principal: Editor) -> list:
    _document(conn, document_id)
    _run(lambda: service.add_named(conn, document_id, "venue", body.name, user=principal.user))
    return store.venues(conn, document_id)


@router.patch("/venues/{venue_id}")
def rename_venue(document_id: int, venue_id: int, body: NamedItem, conn: Conn, principal: Editor) -> list:
    _document(conn, document_id)
    _run(lambda: service.rename_named(conn, document_id, "venue", venue_id, body.name, user=principal.user))
    return store.venues(conn, document_id)


@router.delete("/venues/{venue_id}")
def delete_venue(document_id: int, venue_id: int, conn: Conn, principal: Editor) -> list:
    _document(conn, document_id)
    _run(lambda: service.delete_named(conn, document_id, "venue", venue_id, user=principal.user))
    return store.venues(conn, document_id)


@router.post("/groups/{kind}", status_code=status.HTTP_201_CREATED)
def add_group(document_id: int, kind: GroupKindPath, body: NamedItem, conn: Conn, principal: Editor) -> list:
    _document(conn, document_id)
    _run(lambda: service.add_named(conn, document_id, "group", body.name, user=principal.user, kind=kind))
    return store.groups(conn, document_id)


@router.patch("/groups/{group_id}")
def rename_group(document_id: int, group_id: int, body: NamedItem, conn: Conn, principal: Editor) -> list:
    _document(conn, document_id)
    _run(lambda: service.rename_named(conn, document_id, "group", group_id, body.name, user=principal.user))
    return store.groups(conn, document_id)


@router.delete("/groups/{group_id}")
def delete_group(document_id: int, group_id: int, conn: Conn, principal: Editor) -> list:
    _document(conn, document_id)
    _run(lambda: service.delete_named(conn, document_id, "group", group_id, user=principal.user))
    return store.groups(conn, document_id)


# --- publish -----------------------------------------------------------------------------


class PublishBody(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


@router.post("/publish", status_code=status.HTTP_201_CREATED)
def publish_version(request: Request, document_id: int, conn: Conn, principal: Editor,
                    body: PublishBody | None = None) -> dict[str, Any]:
    """Freeze the records as the next version (Excel in the workbook layout + PDF report).
    The optional note is shown at the top of the version's changelog."""
    _document(conn, document_id)
    note = (body.note or "").strip() or None if body else None
    version, failure = _run(lambda: publish(conn, request.app.state.storage, request.app.state.converter,
                                            doc=document_id, user=principal.user, note=note))
    if failure:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"V{version['version']} could not be finished: {failure}. Try publishing again.")
    log.info("%s published document %s as V%s", principal.user, document_id, version["version"])
    return {"version": version, "message": f"Published V{version['version']}."}


@router.get("/changelog")
def version_changelog(request: Request, document_id: int, conn: Conn, principal: Reader,
                      to: Annotated[str, Query(pattern=r"^(draft|\d{1,4})$")] = "draft",
                      from_: Annotated[int | None, Query(alias="from", ge=1)] = None) -> dict[str, Any]:
    """Item-level changes: added, removed and changed items (with old -> new values) and
    Ethernet point totals. to=<version> compares any version (published, uploaded or edited) with the
    version before it (or from=<version>); to=draft shows what the next publish would contain."""
    _document(conn, document_id)
    return _run(lambda: changelog_between(conn, request.app.state.storage, document_id, to=to, from_version=from_))


@router.get("/changes")
def unpublished_changes(document_id: int, conn: Conn, principal: Reader) -> list[dict[str, Any]]:
    """Changes since the last publish, oldest first (what the next version will contain)."""
    _document(conn, document_id)
    return changes_since_last_publish(conn, document_id)
