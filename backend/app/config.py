"""Application settings and domain constants.

Runtime settings come from environment variables (see .env.example). Domain
constants that define behaviour (formula errors) live here so there is
exactly one place to change them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Every uploaded file name must contain this, e.g. "LIS26 _ Event_Network_Requirements_V1.xlsx".
REQUIRED_NAME = "Event_Network_Requirements"



@dataclass(frozen=True)
class Settings:
    data_dir: Path
    gotenberg_url: str
    gotenberg_timeout_seconds: float
    max_upload_bytes: int
    # Help desk: WhatsApp bot integration (both unset = integration off)
    bot_api_token: str | None = field(default=None, repr=False)  # the bot sends it as a Bearer token
    bot_callback_url: str | None = None  # where status updates for reporters are POSTed
    bot_callback_secret: str = field(default="", repr=False)  # HMAC key for X-Signature on those updates
    app_public_url: str = "http://localhost:5173"  # links in bot replies, alerts and emails
    # Notifications. Browser push needs VAPID keys (generated and kept in DATA_DIR locally if unset).
    vapid_public_key: str | None = None
    vapid_private_key: str | None = field(default=None, repr=False)  # PEM
    vapid_subject: str = "mailto:ict@websummit.com"
    slack_webhook_url: str | None = field(default=None, repr=False)  # Slack incoming webhook (a secret)
    smtp_host: str | None = None  # the organisation's email provider
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = field(default=None, repr=False)
    smtp_from: str = "Event Network Hub <no-reply@websummit.com>"
    smtp_starttls: bool = True
    escalation_emails: tuple[str, ...] = ()  # emailed when a P1/P2 ticket misses its acknowledge SLA
    # Paused features keep their data but have no pages, links, API or background jobs.
    helpdesk_enabled: bool = True
    reports_enabled: bool = True
    websummit_theme_enabled: bool = True  # off: the pages use a plain, neutral look

    @property
    def db_path(self) -> Path:
        return self.data_dir / "portal.sqlite3"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"


def _flag(name: str, default: bool = True) -> bool:
    value = (os.environ.get(name) or "").strip().lower()
    return default if not value else value in ("1", "true", "yes", "on")


def load_settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("DATA_DIR", "/data")),
        gotenberg_url=os.environ.get("GOTENBERG_URL", "http://gotenberg:3000").rstrip("/"),
        gotenberg_timeout_seconds=float(os.environ.get("GOTENBERG_TIMEOUT_SECONDS", "90")),
        max_upload_bytes=int(os.environ.get("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024))),
        bot_api_token=os.environ.get("BOT_API_TOKEN") or None,
        bot_callback_url=(os.environ.get("BOT_CALLBACK_URL") or "").rstrip("/") or None,
        bot_callback_secret=os.environ.get("BOT_CALLBACK_SECRET", ""),
        app_public_url=os.environ.get("APP_PUBLIC_URL", "http://localhost:5173").rstrip("/"),
        vapid_public_key=os.environ.get("VAPID_PUBLIC_KEY") or None,
        vapid_private_key=(os.environ.get("VAPID_PRIVATE_KEY") or "").replace("\\n", "\n") or None,
        vapid_subject=os.environ.get("VAPID_SUBJECT") or "mailto:ict@websummit.com",
        slack_webhook_url=os.environ.get("SLACK_WEBHOOK_URL") or None,
        helpdesk_enabled=_flag("FEATURE_HELPDESK"),
        reports_enabled=_flag("FEATURE_REPORTS"),
        websummit_theme_enabled=_flag("FEATURE_WEBSUMMIT_THEME"),
        smtp_host=os.environ.get("SMTP_HOST") or None,
        smtp_port=int(os.environ.get("SMTP_PORT") or 587),
        smtp_username=os.environ.get("SMTP_USERNAME") or None,
        smtp_password=os.environ.get("SMTP_PASSWORD") or None,
        smtp_from=os.environ.get("SMTP_FROM") or "Event Network Hub <no-reply@websummit.com>",
        smtp_starttls=(os.environ.get("SMTP_STARTTLS", "true").lower() != "false"),
        escalation_emails=tuple(e.strip() for e in (os.environ.get("ESCALATION_EMAILS") or "").split(",") if e.strip()),
    )
