"""The schema comes from the migrations; the app checks it at start-up and in /api/ready."""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from app import db
from app.config import Settings
from app.main import create_app

from .conftest import TEST_DATABASE_URL, _create_database, alembic_config

ADMIN = {"X-Role": "network-admin", "X-User": "alice"}


@pytest.fixture
def blank_url() -> str:
    """A second, empty database (not the shared, migrated one)."""
    base, _, name = TEST_DATABASE_URL.rpartition("/")
    url = f"{base}/{name.removesuffix('_test')}_blank_test"
    _create_database(url)
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE")
        c.execute("CREATE SCHEMA public")
    return url


def _settings(tmp_path: Path, url: str, **extra) -> Settings:
    return Settings(data_dir=tmp_path, gotenberg_url="http://unused", gotenberg_timeout_seconds=1,
                    max_upload_bytes=1024 * 1024, database_url=url, **extra)


def _tables(url: str) -> set[str]:
    with psycopg.connect(url) as c:
        return {r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}


def test_schema_version_is_the_newest_migration() -> None:
    assert ScriptDirectory.from_config(alembic_config()).get_current_head() == db.SCHEMA_VERSION


def test_migrations_upgrade_downgrade_and_upgrade_again(blank_url: str) -> None:
    command.upgrade(alembic_config(blank_url), "head")
    assert len(_tables(blank_url) - {"alembic_version"}) == 24
    command.downgrade(alembic_config(blank_url), "base")
    assert _tables(blank_url) == {"alembic_version"}
    command.upgrade(alembic_config(blank_url), "head")
    assert len(_tables(blank_url) - {"alembic_version"}) == 24


def test_check_schema(blank_url: str) -> None:
    conn = db.connect(blank_url)
    try:
        with pytest.raises(db.SchemaNotReady, match="no schema"):
            db.check_schema(conn)
        command.upgrade(alembic_config(blank_url), "head")
        assert db.check_schema(conn) == db.SCHEMA_VERSION
        with conn:
            conn.execute("UPDATE alembic_version SET version_num = '0000'")
        with pytest.raises(db.SchemaNotReady, match="needs"):
            db.check_schema(conn)
        with conn:  # newer than this release: the previous release keeps running during a rollout
            conn.execute("UPDATE alembic_version SET version_num = '9999'")
        assert db.check_schema(conn) == "9999"
    finally:
        conn.close()


def test_app_refuses_to_start_on_an_unmigrated_database(tmp_path: Path, blank_url: str) -> None:
    with pytest.raises(db.SchemaNotReady):
        with TestClient(create_app(_settings(tmp_path, blank_url))):
            pass


def test_the_app_creates_no_tables(tmp_path: Path) -> None:
    before = _tables(TEST_DATABASE_URL)
    with TestClient(create_app(_settings(tmp_path, TEST_DATABASE_URL))) as c:
        assert c.get("/api/documents").status_code == 200
    assert _tables(TEST_DATABASE_URL) == before


def test_health_and_ready_report_the_commit(tmp_path: Path, blank_url: str) -> None:
    app = create_app(_settings(tmp_path, TEST_DATABASE_URL, git_sha="abc1234"))
    with TestClient(app) as c:
        assert c.get("/api/health").json() == {"status": "ok", "commit": "abc1234"}
        assert c.get("/api/ready").json() == {"status": "ready", "commit": "abc1234", "schema": db.SCHEMA_VERSION}
        # the database falls behind (say, the migration job hasn't run): not ready, but still alive
        app.state.settings = _settings(tmp_path, blank_url, git_sha="abc1234")
        r = c.get("/api/ready")
        assert r.status_code == 503 and r.json()["commit"] == "abc1234" and "no schema" in r.json()["reason"]
        assert c.get("/api/health").status_code == 200
        app.state.settings = _settings(tmp_path, "postgresql://nobody:wrong@127.0.0.1:1/none_test")
        r = c.get("/api/ready")
        assert r.status_code == 503 and r.json()["reason"] == "database unavailable"
