"""PostgreSQL access for the rest of the app.

The stores were written against Python's sqlite3 module, so this keeps that small interface:

- ``conn.execute(sql, params)`` with ``?`` placeholders, returning a cursor (``fetchone``,
  ``fetchall``, iteration, ``rowcount``);
- rows that work both as ``row["name"]`` and ``row[0]``, and convert with ``dict(row)``;
- ``with conn:`` as a transaction: commit on success, roll back on an exception. Nested
  ``with conn:`` blocks are savepoints, so an inner failure (a duplicate caught and retried)
  leaves the outer transaction usable. Statements outside any ``with conn:`` commit on their own.

Connections are opened per request or per job and closed afterwards, as before. In the cluster the
password is read from the RDS-managed Secrets Manager secret (cached), and re-read once when the
server rejects it, which is what happens after RDS rotates it.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import threading
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlsplit

import psycopg

log = logging.getLogger(__name__)

IntegrityError = psycopg.IntegrityError


# --- connection settings -----------------------------------------------------------------------


@dataclass(frozen=True)
class Database:
    """Where the database is. Either a password, or ``secret_arn`` (an RDS-managed secret)."""

    host: str = "localhost"
    port: int = 5432
    dbname: str = "event_network_hub"
    user: str = "postgres"
    password: str | None = field(default=None, repr=False)
    secret_arn: str | None = None  # RDS-managed secret holding {"username", "password"}
    sslmode: str = "prefer"  # "require" in the cluster (RDS enforces TLS)
    search_path: str | None = None  # tests: one schema per test

    @classmethod
    def from_env(cls) -> Database:
        """DATABASE_URL (local), or DB_HOST/DB_PORT/DB_NAME/DB_USER + DB_SECRET_ARN (RDS)."""
        url = os.environ.get("DATABASE_URL")
        if url:
            return cls.from_url(url)
        return cls(
            host=os.environ.get("DB_HOST", "localhost"),
            port=int(os.environ.get("DB_PORT") or 5432),
            dbname=os.environ.get("DB_NAME", "event_network_hub"),
            user=os.environ.get("DB_USER", "postgres"),
            password=os.environ.get("DB_PASSWORD") or None,
            secret_arn=os.environ.get("DB_SECRET_ARN") or None,
            sslmode=os.environ.get("DB_SSLMODE", "require"),
        )

    @classmethod
    def from_url(cls, url: str) -> Database:
        parts = urlsplit(url)
        if parts.scheme not in ("postgresql", "postgres"):
            raise ValueError("DATABASE_URL must start with postgresql://")
        query = dict(p.split("=", 1) for p in parts.query.split("&") if "=" in p)
        return cls(
            host=parts.hostname or "localhost",
            port=parts.port or 5432,
            dbname=unquote(parts.path.lstrip("/")) or "event_network_hub",
            user=unquote(parts.username or "postgres"),
            password=unquote(parts.password) if parts.password else None,
            sslmode=query.get("sslmode", "prefer"),
        )


_secret_lock = threading.Lock()
_secret_cache: dict[str, tuple[str, str]] = {}


def _credentials(database: Database, *, refresh: bool = False) -> tuple[str, str | None]:
    if not database.secret_arn:
        return database.user, database.password
    with _secret_lock:
        if refresh or database.secret_arn not in _secret_cache:
            import boto3  # only needed in the cluster

            value = boto3.client("secretsmanager").get_secret_value(SecretId=database.secret_arn)
            secret = json.loads(value["SecretString"])
            _secret_cache[database.secret_arn] = (secret["username"], secret["password"])
        return _secret_cache[database.secret_arn]


def connect(database: Database) -> Connection:
    try:
        return Connection(_open(database, _credentials(database)))
    except psycopg.OperationalError as exc:
        if database.secret_arn and "password authentication failed" in str(exc):
            log.info("database password rejected; re-reading the secret (rotated?)")
            return Connection(_open(database, _credentials(database, refresh=True)))
        raise


def _open(database: Database, credentials: tuple[str, str | None]) -> psycopg.Connection:
    user, password = credentials
    kwargs: dict[str, Any] = {
        "host": database.host, "port": database.port, "dbname": database.dbname, "user": user,
        "sslmode": database.sslmode, "connect_timeout": 10, "application_name": "event-network-hub",
    }
    if password:
        kwargs["password"] = password
    if database.search_path:
        kwargs["options"] = f"-c search_path={database.search_path}"
    return psycopg.connect(autocommit=True, row_factory=_row_factory, **kwargs)


# --- rows, cursors, connections ----------------------------------------------------------------


class Row(tuple):
    """A result row: ``row[0]``, ``row["name"]``, ``row.keys()`` and ``dict(row)``."""

    _keys: tuple[str, ...]

    def __new__(cls, keys: tuple[str, ...], values: Sequence[Any]) -> Row:
        row = super().__new__(cls, values)
        row._keys = keys
        return row

    def __getitem__(self, key):  # type: ignore[override]
        if isinstance(key, str):
            try:
                return super().__getitem__(self._keys.index(key))
            except ValueError:
                raise IndexError(f"no column named {key!r}") from None
        return super().__getitem__(key)

    def keys(self) -> list[str]:
        return list(self._keys)


def _row_factory(cursor: psycopg.Cursor) -> Any:
    keys = tuple(c.name for c in cursor.description) if cursor.description else ()
    return lambda values: Row(keys, values)


@functools.lru_cache(maxsize=1024)
def _translate(sql: str) -> str:
    """``?`` placeholders -> ``%s``; a literal ``%`` -> ``%%``. Text in '...' keeps its ``?``."""
    out, quoted = [], False
    for ch in sql:
        if ch == "'":
            quoted = not quoted
            out.append(ch)
        elif ch == "%":
            out.append("%%")
        elif ch == "?" and not quoted:
            out.append("%s")
        else:
            out.append(ch)
    return "".join(out)


class Cursor:
    def __init__(self, cursor: psycopg.Cursor) -> None:
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def fetchone(self) -> Row | None:
        return self._cursor.fetchone() if self._cursor.description else None

    def fetchall(self) -> list[Row]:
        return self._cursor.fetchall() if self._cursor.description else []

    def __iter__(self) -> Iterator[Row]:
        return iter(self.fetchall())


class Connection:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn
        self._transactions: list[Any] = []

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Cursor:
        cursor = self._conn.cursor()
        cursor.execute(_translate(sql), tuple(params))
        return Cursor(cursor)

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        rows = [tuple(p) for p in seq]
        if rows:
            with self._conn.cursor() as cursor:
                cursor.executemany(_translate(sql), rows)

    def executescript(self, sql: str) -> None:
        """Run several statements (schema scripts). No parameters."""
        self._conn.execute(sql)  # type: ignore[arg-type]

    def __enter__(self) -> Connection:
        tx = self._conn.transaction()
        tx.__enter__()
        self._transactions.append(tx)
        return self

    def __exit__(self, *exc: Any) -> None:
        self._transactions.pop().__exit__(*exc)

    def close(self) -> None:
        self._conn.close()
