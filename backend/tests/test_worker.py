"""The worker process (app/worker.py): one at a time, by a PostgreSQL advisory lock."""

from __future__ import annotations

import asyncio
from pathlib import Path

import psycopg
import pytest

from app import worker
from app.config import Settings

from .conftest import TEST_DATABASE_URL


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, gotenberg_url="http://unused", gotenberg_timeout_seconds=1,
                    max_upload_bytes=1024, database_url=TEST_DATABASE_URL)


def test_only_one_worker_holds_the_lock() -> None:
    first = worker.try_lock(TEST_DATABASE_URL)
    assert first is not None
    try:
        assert worker.try_lock(TEST_DATABASE_URL) is None  # a second worker waits
    finally:
        first.close()  # the first one stops...
    second = worker.try_lock(TEST_DATABASE_URL)  # ...and the standby takes over
    assert second is not None
    second.close()


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the real loops with ones that record that they ran."""
    ran: list[str] = []

    async def job() -> None:
        ran.append("job")
        await asyncio.Event().wait()

    monkeypatch.setattr(worker, "_jobs", lambda settings: [job()])
    monkeypatch.setattr(worker, "LOCK_CHECK_SECONDS", 0.1)
    monkeypatch.setattr(worker, "LOCK_RETRY_SECONDS", 0.1)
    return ran


async def _until(condition, timeout: float = 5) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.05)


def test_stops_cleanly_and_releases_the_lock(tmp_path: Path, jobs: list[str]) -> None:
    async def scenario() -> int:
        stop = asyncio.Event()
        task = asyncio.create_task(worker.run(_settings(tmp_path), stop))
        await _until(lambda: jobs)
        assert worker.try_lock(TEST_DATABASE_URL) is None
        stop.set()  # SIGTERM
        return await task

    assert asyncio.run(scenario()) == 0
    lock = worker.try_lock(TEST_DATABASE_URL)
    assert lock is not None
    lock.close()


def test_standby_waits_until_the_lock_is_free(tmp_path: Path, jobs: list[str]) -> None:
    holder = worker.try_lock(TEST_DATABASE_URL)

    async def scenario() -> int:
        stop = asyncio.Event()
        task = asyncio.create_task(worker.run(_settings(tmp_path), stop))
        await asyncio.sleep(0.5)
        assert jobs == []  # waiting: the other worker is running
        holder.close()
        await _until(lambda: jobs)
        stop.set()
        return await task

    assert asyncio.run(scenario()) == 0


def test_exits_when_the_lock_connection_is_lost(tmp_path: Path, jobs: list[str]) -> None:
    async def scenario() -> int:
        task = asyncio.create_task(worker.run(_settings(tmp_path), asyncio.Event()))
        await _until(lambda: jobs)
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:  # e.g. a database failover
            admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                          "WHERE application_name = 'event-network-hub-worker'")
        return await asyncio.wait_for(task, 5)

    assert asyncio.run(scenario()) == 1
