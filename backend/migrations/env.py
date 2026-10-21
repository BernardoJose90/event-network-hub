"""Alembic environment. The migrations are plain SQL (the app has no ORM models).

The database comes from DATABASE_URL (the same variable the app reads). Concurrent runs, such
as two deploy jobs, wait for each other on an advisory lock instead of racing.
"""

from __future__ import annotations

import os
import re
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text

config = context.config
if config.config_file_name and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

MIGRATION_LOCK = 72_406_001  # pg_advisory_lock key, only used by migrations


def _url() -> str:
    url = config.attributes.get("database_url") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Set DATABASE_URL to the PostgreSQL database to migrate")
    return re.sub(r"^postgres(ql)?(\+\w+)?://", "postgresql+psycopg://", url)


def run_offline() -> None:
    """``alembic upgrade head --sql``: print the SQL instead of running it."""
    context.configure(url=_url(), literal_binds=True, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK})
        connection.commit()
        try:
            context.configure(connection=connection, transaction_per_migration=True)
            with context.begin_transaction():
                context.run_migrations()
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK})
            connection.commit()
    engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
