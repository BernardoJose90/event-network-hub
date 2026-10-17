"""Application settings and domain constants.

Runtime settings come from environment variables (see .env.example). Domain
constants that define behaviour (formula errors) live here so there is
exactly one place to change them.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

# Every uploaded file name must contain this, e.g. "LIS26 _ Event_Network_Requirements_V1.xlsx".
REQUIRED_NAME = "Event_Network_Requirements"

# Spreadsheet error values surfaced to users as warnings.
FORMULA_ERRORS: frozenset[str] = frozenset({"#REF!", "#NAME?", "#DIV/0!", "#VALUE!", "#N/A"})


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    gotenberg_url: str
    gotenberg_timeout_seconds: float
    max_upload_bytes: int
    # In-browser editing (Collabora Online over WOPI)
    collabora_url: str = "http://collabora:9980"  # how the backend reaches Collabora
    wopi_base_url: str = "http://backend:8000"  # how Collabora reaches the backend
    app_origin: str = "http://localhost:5173"  # the page that embeds the editor
    wopi_token_secret: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    wopi_token_ttl_seconds: int = 10 * 60 * 60
    # Help desk: WhatsApp bot integration (both unset = integration off)
    bot_api_token: str | None = field(default=None, repr=False)  # the bot sends it as a Bearer token
    bot_callback_url: str | None = None  # where status updates for reporters are POSTed
    bot_callback_secret: str = field(default="", repr=False)  # HMAC key for X-Signature on those updates
    app_public_url: str = "http://localhost:5173"  # links to tickets in bot replies

    @property
    def db_path(self) -> Path:
        return self.data_dir / "portal.sqlite3"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"


def load_settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("DATA_DIR", "/data")),
        gotenberg_url=os.environ.get("GOTENBERG_URL", "http://gotenberg:3000").rstrip("/"),
        gotenberg_timeout_seconds=float(os.environ.get("GOTENBERG_TIMEOUT_SECONDS", "90")),
        max_upload_bytes=int(os.environ.get("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024))),
        collabora_url=os.environ.get("COLLABORA_URL", "http://collabora:9980").rstrip("/"),
        wopi_base_url=os.environ.get("WOPI_BASE_URL", "http://backend:8000").rstrip("/"),
        app_origin=os.environ.get("APP_ORIGIN", "http://localhost:5173").rstrip("/"),
        # Unset means a random per-process secret: editor sessions end on restart.
        wopi_token_secret=os.environ.get("WOPI_TOKEN_SECRET") or secrets.token_urlsafe(32),
        wopi_token_ttl_seconds=int(os.environ.get("WOPI_TOKEN_TTL_SECONDS", str(10 * 60 * 60))),
        bot_api_token=os.environ.get("BOT_API_TOKEN") or None,
        bot_callback_url=(os.environ.get("BOT_CALLBACK_URL") or "").rstrip("/") or None,
        bot_callback_secret=os.environ.get("BOT_CALLBACK_SECRET", ""),
        app_public_url=os.environ.get("APP_PUBLIC_URL", "http://localhost:5173").rstrip("/"),
    )
