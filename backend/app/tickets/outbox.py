"""Delivers queued reporter updates to the WhatsApp bot's callback URL.

Each update is POSTed as JSON with an ``X-Signature: sha256=<hex>`` header, an
HMAC-SHA256 of the raw body with BOT_CALLBACK_SECRET, so the bot can verify it
came from us. Failures are retried with exponential backoff, then marked failed.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone

import httpx
from starlette.concurrency import run_in_threadpool

from .. import db
from . import store

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 10
POLL_SECONDS = 5


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def deliver_due(db_path, url: str, secret: str, client: httpx.Client) -> int:
    """Send every due update once. Returns how many were sent."""
    conn = db.connect(db_path)
    sent = 0
    try:
        now = datetime.now(timezone.utc)
        for item in store.due_outbox(conn, now.isoformat(timespec="seconds")):
            body = item["payload"].encode()
            attempts = item["attempts"] + 1
            try:
                response = client.post(url, content=body, headers={
                    "Content-Type": "application/json", "X-Signature": sign(secret, body)})
                response.raise_for_status()
            except httpx.HTTPError as exc:
                error = str(exc)[:500]
                if attempts >= MAX_ATTEMPTS:
                    store.mark_outbox(conn, item["id"], status="failed", attempts=attempts, error=error)
                    log.error("bot update %s for ticket %s failed permanently: %s", item["id"], item["ticket_id"], error)
                else:
                    delay = min(10 * 2 ** (attempts - 1), 1800)
                    store.mark_outbox(conn, item["id"], status="pending", attempts=attempts, error=error,
                                      next_try_at=(now + timedelta(seconds=delay)).isoformat(timespec="seconds"))
                    log.warning("bot update %s for ticket %s failed (attempt %s): %s", item["id"],
                                item["ticket_id"], attempts, error)
                continue
            store.mark_outbox(conn, item["id"], status="sent", attempts=attempts)
            sent += 1
    finally:
        conn.close()
    return sent


async def run(settings) -> None:
    """Background loop started with the app when BOT_CALLBACK_URL is set."""
    with httpx.Client(timeout=10) as client:
        while True:
            try:
                await run_in_threadpool(deliver_due, settings.db_path, settings.bot_callback_url,
                                        settings.bot_callback_secret, client)
            except Exception:
                log.exception("bot outbox delivery failed")
            await asyncio.sleep(POLL_SECONDS)
