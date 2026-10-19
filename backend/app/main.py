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
from .ictrequests.routes import admin_router as requests_admin_router, public_router as requests_public_router
from .notifications import keys as notification_keys, service as notifications, worker as notification_worker
from .notifications.routes import router as notifications_router, watchers_router
from .records.comments import router as comments_router
from .records.routes import events_router, router as records_router
from .reports import router as reports_router
from .routes import router
from .service import DocumentGone
from .storage import LocalStorage
from .tickets import outbox
from .tickets.routes import bot_router, router as tickets_router, settings_router as helpdesk_router

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

_TOKEN_IN_URL = re.compile(r"(access_token=)[^&\s\"]+")


class RedactAccessTokens(logging.Filter):
    """Keep any access_token query parameter out of access logs."""

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
        vapid, app.state.vapid_public_key = notification_keys.load(settings)
        notifications.configure(notifications.Channels(
            push=True, slack=bool(settings.slack_webhook_url), email=bool(settings.smtp_host),
            escalation_emails=settings.escalation_emails, public_url=settings.app_public_url))
        app.state.notification_sender = notification_worker.Sender(settings, vapid)
        notifier = asyncio.create_task(notification_worker.run(settings, app.state.notification_sender))
        worker = None
        if settings.bot_callback_url and settings.helpdesk_enabled:
            if not settings.bot_callback_secret:
                raise RuntimeError("BOT_CALLBACK_SECRET is required when BOT_CALLBACK_URL is set")
            worker = asyncio.create_task(outbox.run(settings))
        try:
            yield
        finally:
            notifier.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await notifier
            if worker:
                worker.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await worker
            converter.close()

    app = FastAPI(title="Event Network Hub", lifespan=lifespan)
    app.include_router(router)
    app.include_router(records_router)
    app.include_router(events_router)
    app.include_router(comments_router)
    if settings.helpdesk_enabled:  # paused: no API (404), no bot intake, no SLA checks
        app.include_router(tickets_router)
        app.include_router(bot_router)
        app.include_router(helpdesk_router)
    app.include_router(requests_public_router)
    app.include_router(requests_admin_router)
    if settings.reports_enabled:
        app.include_router(reports_router)
    app.include_router(notifications_router)
    app.include_router(watchers_router)

    @app.get("/api/features")
    def features() -> dict[str, bool]:
        """Which optional areas are on (FEATURE_HELPDESK, FEATURE_REPORTS); the pages hide paused ones."""
        return {"helpdesk": settings.helpdesk_enabled, "reports": settings.reports_enabled}

    @app.exception_handler(DocumentGone)
    async def document_gone(_: Request, __: DocumentGone) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": "Document not found"})

    return app


app = create_app()
