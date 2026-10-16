from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import db
from .config import Settings, load_settings
from .converter import GotenbergConverter
from .routes import router
from .storage import LocalStorage

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


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
        try:
            yield
        finally:
            converter.close()

    app = FastAPI(title="portal-pdf-test", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
