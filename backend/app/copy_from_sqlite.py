"""Copy the SQLite database (and its files) into PostgreSQL (and the configured file storage).

    DATABASE_URL=postgresql://... python -m app.copy_from_sqlite --sqlite /data/portal.sqlite3 \\
        [--files-dir /data/files] [--dry-run]

The target must be migrated (alembic upgrade head) and empty. Every row keeps its id, so links,
version numbers and history stay the same, and new ids continue after the old ones. The copy is
one transaction: it is committed only if every table has as many rows as in SQLite, so a failed
run leaves the target empty and can simply be run again.

--files-dir also copies the stored workbooks and PDFs into the storage that STORAGE_BACKEND
names (S3 in the cluster), before the rows. --dry-run copies the rows, checks them and rolls
back, without touching the files.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any

import psycopg

from . import db, storage
from .config import load_settings

# Parents before children, so every foreign key finds its row.
TABLES = (
    "documents", "document_versions", "download_log", "event_venues", "event_groups", "event_lists", "records",
    "change_log", "published_snapshots", "workbook_snapshots", "comments", "tickets", "ticket_activity",
    "ticket_outbox", "helpdesk_settings", "request_links", "request_settings", "ict_requests", "notifications",
    "notification_reads", "notification_prefs", "push_subscriptions", "notification_deliveries", "event_watchers",
)
# Columns pointing at a row of the same table, which may come later: filled in after the inserts.
SELF_REFERENCES = {"comments": "parent_id", "tickets": "merged_into"}


class CopyError(Exception):
    pass


def _quote(name: str) -> str:
    return f'"{name}"'


def _target_columns(pg: psycopg.Connection, table: str) -> dict[str, str]:
    return {r[0]: r[1] for r in pg.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position", (table,))}


def _coerce(value: Any, data_type: str) -> Any:
    """SQLite lets any column hold any type; PostgreSQL doesn't."""
    if value is None:
        return None
    if data_type == "text":
        return value if isinstance(value, str) else str(value)
    if data_type in ("bigint", "integer"):
        return int(value)
    if data_type == "double precision":
        return float(value)
    return value


def copy_rows(source: sqlite3.Connection, pg: psycopg.Connection, log=print) -> dict[str, int]:
    """Copy every table inside the caller's transaction; returns the row count per table."""
    source_tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    target_tables = {r[0] for r in pg.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'")}
    if target_tables != set(TABLES):
        raise CopyError(f"the target's tables don't match this script: {sorted(target_tables ^ set(TABLES))}")
    for table in TABLES:
        if pg.execute(f"SELECT EXISTS (SELECT 1 FROM {table})").fetchone()[0]:
            raise CopyError(f"the target isn't empty ({table} has rows); copy into a freshly migrated database")
    sequences = dict(source.execute("SELECT name, seq FROM sqlite_sequence")) if "sqlite_sequence" in source_tables else {}

    counts = {}
    for table in TABLES:
        if table not in source_tables:  # created by a later build than the one that wrote the file
            counts[table] = 0
            log(f"  {table:<26} not in the SQLite file, left empty")
            continue
        target = _target_columns(pg, table)
        columns = [r[1] for r in source.execute(f"PRAGMA table_info({table})")]
        unknown = [c for c in columns if c not in target]
        if unknown:
            raise CopyError(f"{table}: columns {unknown} have nowhere to go in PostgreSQL")
        deferred = SELF_REFERENCES.get(table)
        order = "ORDER BY id" if "id" in columns else ""
        rows = source.execute(f"SELECT {', '.join(map(_quote, columns))} FROM {table} {order}").fetchall()
        values = [[None if c == deferred else _coerce(v, target[c]) for c, v in zip(columns, row)] for row in rows]
        if values:
            with pg.cursor() as cur:
                cur.executemany(f"INSERT INTO {table} ({', '.join(map(_quote, columns))}) "
                                f"VALUES ({', '.join(['%s'] * len(columns))})", values)
        if deferred:
            links = [(int(row[columns.index(deferred)]), row[columns.index("id")])
                     for row in rows if row[columns.index(deferred)] is not None]
            with pg.cursor() as cur:
                cur.executemany(f"UPDATE {table} SET {deferred} = %s WHERE id = %s", links)
        if target.get("id") == "bigint":
            # New ids continue after the highest id ever used (SQLite AUTOINCREMENT never reuses one).
            last = max(pg.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").fetchone()[0], sequences.get(table, 0))
            if last:
                pg.execute("SELECT setval(pg_get_serial_sequence(%s, 'id'), %s)", (table, last))
        counts[table] = len(rows)

    for table in TABLES:  # the check: every row arrived
        expected = counts[table]
        actual = pg.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        log(f"  {table:<26} {expected:>7} rows" + ("" if actual == expected else f"  MISMATCH: {actual} in PostgreSQL"))
        if actual != expected:
            raise CopyError(f"{table}: {expected} rows in SQLite but {actual} in PostgreSQL")
    return counts


def copy_files(files_dir: Path, target: storage.Storage, log=print) -> int:
    """Upload every stored file (documents/<id>/v<n>/<name>) under the same key."""
    root = files_dir.resolve()
    paths = [p for p in sorted(root.rglob("*")) if p.is_file() and not p.name.startswith(".tmp-")]
    for p in paths:
        target.put(p.relative_to(root).as_posix(), p.read_bytes())
    for p in paths:  # the check: every file reads back the same
        if target.get(p.relative_to(root).as_posix()) != p.read_bytes():
            raise CopyError(f"{p.relative_to(root)} didn't read back the same from the target storage")
    log(f"  {len(paths)} files copied and read back")
    return len(paths)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Copy the SQLite database (and files) into PostgreSQL.")
    parser.add_argument("--sqlite", required=True, type=Path, help="the SQLite file, e.g. /data/portal.sqlite3")
    parser.add_argument("--files-dir", type=Path, help="also copy the files under this folder, e.g. /data/files")
    parser.add_argument("--dry-run", action="store_true", help="copy and check the rows, then roll back")
    args = parser.parse_args(argv)
    if not args.sqlite.is_file():
        parser.error(f"{args.sqlite} doesn't exist")
    settings = load_settings()

    source = sqlite3.connect(f"file:{args.sqlite}?mode=ro", uri=True)
    try:
        check = db.connect(settings.database_url)
        try:
            db.check_schema(check)
        finally:
            check.close()
        with psycopg.connect(settings.database_url) as pg:
            if args.files_dir and not args.dry_run:
                print(f"Files from {args.files_dir} to {settings.storage_backend} storage:")
                copy_files(args.files_dir, storage.from_settings(settings))
            print(f"Rows from {args.sqlite}:")
            copy_rows(source, pg)
            if args.dry_run:
                pg.rollback()
                print("Dry run: every table checked out; rolled back, nothing was written.")
            else:
                pg.commit()
                print("Copied: every table has the same number of rows as in SQLite.")
    except (CopyError, db.SchemaNotReady, psycopg.Error) as exc:
        print(f"Not copied (nothing was written to PostgreSQL): {exc}", file=sys.stderr)
        return 1
    finally:
        source.close()
        db.close_pools()
    return 0


if __name__ == "__main__":
    sys.exit(main())
