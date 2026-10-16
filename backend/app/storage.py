"""Blob storage abstraction.

Routes and services depend only on the ``Storage`` protocol, so the local
implementation can be swapped for S3 without touching them.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Protocol


class StorageError(Exception):
    pass


class ObjectNotFound(StorageError):
    pass


class Storage(Protocol):
    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete_prefix(self, prefix: str) -> None:
        """Delete every object whose key starts with ``prefix`` (a "directory", ending in /)."""
        ...


def document_prefix(document_id: int) -> str:
    return f"documents/{document_id}/"


def version_key(document_id: int, version: int, filename: str) -> str:
    return f"{document_prefix(document_id)}v{version}/{filename}"


class LocalStorage:
    """Stores objects as files under ``root``. Writes are atomic (temp file + rename)."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        rel = PurePosixPath(key)
        if rel.is_absolute() or ".." in rel.parts or not rel.parts:
            raise StorageError(f"invalid storage key: {key!r}")
        return self._root.joinpath(*rel.parts)

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as exc:
            raise ObjectNotFound(key) from exc

    def delete_prefix(self, prefix: str) -> None:
        if not prefix.endswith("/"):
            raise StorageError(f"prefix must end with '/': {prefix!r}")
        path = self._path(prefix.rstrip("/"))
        if path == self._root:
            raise StorageError("refusing to delete the storage root")
        if path.exists():
            shutil.rmtree(path)
