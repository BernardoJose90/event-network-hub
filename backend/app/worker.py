"""The background worker: notification deliveries and scheduled checks (notifications/worker.py)
and the WhatsApp bot outbox (tickets/outbox.py).

Run it as its own process, ``python -m app.worker``, so the API can run as several pods without
sending every alert once per pod. Only one worker does the work at a time: it holds a PostgreSQL
advisory lock on a connection of its own, and any other worker waits for the lock (a standby).
The lock goes with that connection, so if the connection is lost the worker stops at once
(exit 1) rather than risk running alongside the standby that has taken over.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

import psycopg

from . import db
from .config import Settings, load_settings
from .notifications import keys as notification_keys, service as notifications, worker as notification_worker
from .tickets import outbox

log = logging.getLogger("app.worker")

WORKER_LOCK = 72_406_002  # pg_advisory_lock key (migrations use 72_406_001)
LOCK_RETRY_SECONDS = 15
LOCK_CHECK_SECONDS = 10


class LockLost(RuntimeError):
    pass


def _lock_connection(database_url: str) -> psycopg.Connection:
    # Keepalives notice a dead network within about a minute, not the OS default of hours.
    return psycopg.connect(database_url, autocommit=True, application_name="event-network-hub-worker",
                           keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3)


def try_lock(database_url: str) -> psycopg.Connection | None:
    """The connection holding the worker lock, or None if another worker has it."""
    conn = _lock_connection(database_url)
    try:
        if conn.execute("SELECT pg_try_advisory_lock(%s)", (WORKER_LOCK,)).fetchone()[0]:
            return conn
    except BaseException:
        conn.close()
        raise
    conn.close()
    return None


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), seconds)
    except TimeoutError:
        pass


async def _watch_lock(lock: psycopg.Connection) -> None:
    while True:
        await asyncio.sleep(LOCK_CHECK_SECONDS)
        try:
            await asyncio.to_thread(lambda: lock.execute("SELECT 1"))
        except psycopg.Error as exc:
            raise LockLost(f"lost the connection holding the worker lock: {exc}") from exc


def check_config(settings: Settings) -> None:
    if settings.bot_callback_url and not settings.bot_callback_secret:
        raise RuntimeError("BOT_CALLBACK_SECRET is required when BOT_CALLBACK_URL is set")


def _jobs(settings: Settings) -> list:
    vapid, _ = notification_keys.load(settings)
    jobs = [notification_worker.run(settings, notification_worker.Sender(settings, vapid))]
    if settings.bot_callback_url and settings.helpdesk_enabled:
        jobs.append(outbox.run(settings))
    return jobs


async def run(settings: Settings, stop: asyncio.Event) -> int:
    conn = db.connect(settings.database_url)
    try:
        db.check_schema(conn)
    finally:
        conn.close()
    check_config(settings)
    notifications.configure(notifications.channels_for(settings))
    jobs = _jobs(settings)

    lock = None
    while lock is None and not stop.is_set():
        lock = await asyncio.to_thread(try_lock, settings.database_url)
        if lock is None:
            log.info("another worker is running; waiting to take over")
            await _sleep_or_stop(stop, LOCK_RETRY_SECONDS)
    if lock is None:
        for job in jobs:
            job.close()
        return 0

    log.info("worker started (commit %s)", settings.git_sha)
    tasks = [asyncio.create_task(job) for job in jobs]
    tasks.append(asyncio.create_task(_watch_lock(lock)))
    stopping = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait([*tasks, stopping], return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in [*tasks, stopping]:
            task.cancel()
        await asyncio.gather(*tasks, stopping, return_exceptions=True)
        lock.close()  # releases the lock for a standby
    failed = [t for t in done if t is not stopping and not t.cancelled() and t.exception()]
    for task in failed:
        log.error("worker stopping: %s", task.exception())
    return 1 if failed else 0


def main() -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    async def start() -> int:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        return await run(load_settings(), stop)

    try:
        return asyncio.run(start())
    finally:
        db.close_pools()


if __name__ == "__main__":
    sys.exit(main())
