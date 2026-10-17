from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from . import db
from .config import Settings, load_settings
from .converter import GotenbergConverter
from .collabora import CollaboraDiscovery
from .ictrequests.routes import admin_router as requests_admin_router, public_router as requests_public_router
from .records.routes import events_router, router as records_router
from .routes import router
from .sheet_routes import router as sheet_router
from .service import DocumentGone
from .storage import LocalStorage
from .tickets import outbox
from .tickets.routes import bot_router, router as tickets_router, settings_router as helpdesk_router
from .wopi import router as wopi_router

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

_TOKEN_IN_URL = re.compile(r"(access_token=)[^&\s\"]+")


class RedactAccessTokens(logging.Filter):
    """WOPI puts access tokens in URLs; keep them out of access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _TOKEN_IN_URL.sub(r"\1[REDACTED]", a) if isinstance(a, str) else a for a in record.args
            )
        if isinstance(record.msg, str):
            record.msg = _TOKEN_IN_URL.sub(r"\1[REDACTED]", record.msg)
        return True


logging.getLogger("uvicorn.access").addFilter(RedactAccessTokens())


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db.init_db(settings.db_path)
        converter = GotenbergConverter(
            settings.gotenberg_url, timeout_seconds=settings.gotenberg_timeout_seconds
        )
        app.state.settings = settings
        app.state.storage = LocalStorage(settings.files_dir)
        app.state.converter = converter
        app.state.discovery = CollaboraDiscovery(settings.collabora_url)
        worker = None
        if settings.bot_callback_url:
            if not settings.bot_callback_secret:
                raise RuntimeError("BOT_CALLBACK_SECRET is required when BOT_CALLBACK_URL is set")
            worker = asyncio.create_task(outbox.run(settings))
        try:
            yield
        finally:
            if worker:
                worker.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await worker
            converter.close()

    app = FastAPI(title="Event Network Hub", lifespan=lifespan)
    app.include_router(router)
    app.include_router(wopi_router)
    app.include_router(sheet_router)
    app.include_router(records_router)
    app.include_router(events_router)
    app.include_router(tickets_router)
    app.include_router(bot_router)
    app.include_router(helpdesk_router)
    app.include_router(requests_public_router)
    app.include_router(requests_admin_router)

    @app.exception_handler(DocumentGone)
    async def document_gone(_: Request, __: DocumentGone) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": "Document not found"})

    return app


app = create_app()
