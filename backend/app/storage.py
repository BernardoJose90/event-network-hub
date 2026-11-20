"""Blob storage abstraction.

Routes and services depend only on the ``Storage`` protocol: ``S3Storage`` when a bucket is
configured (the cluster, and MinIO locally), ``LocalStorage`` otherwise (unit tests).
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


class S3Storage:
    """Stores objects in an S3 bucket (MinIO locally, through ``endpoint_url``).

    In the cluster, credentials come from the pod's EKS Pod Identity role; boto3 finds them itself.
    The bucket is versioned, so a delete leaves a recoverable older version behind.
    """

    def __init__(self, bucket: str, *, endpoint_url: str | None = None, client=None) -> None:
        if client is None:
            import boto3

            client = boto3.client("s3", endpoint_url=endpoint_url)
        self._s3 = client
        self._bucket = bucket

    @staticmethod
    def _key(key: str) -> str:
        rel = PurePosixPath(key)
        if rel.is_absolute() or ".." in rel.parts or not rel.parts:
            raise StorageError(f"invalid storage key: {key!r}")
        return str(rel)

    def put(self, key: str, data: bytes) -> None:
        self._s3.put_object(Bucket=self._bucket, Key=self._key(key), Body=data)  # atomic: whole object or nothing

    def get(self, key: str) -> bytes:
        try:
            return self._s3.get_object(Bucket=self._bucket, Key=self._key(key))["Body"].read()
        except self._s3.exceptions.NoSuchKey as exc:
            raise ObjectNotFound(key) from exc

    def delete_prefix(self, prefix: str) -> None:
        if not prefix.endswith("/"):
            raise StorageError(f"prefix must end with '/': {prefix!r}")
        prefix = self._key(prefix.rstrip("/")) + "/"  # also refuses "" (the whole bucket)
        for page in self._s3.get_paginator("list_objects_v2").paginate(Bucket=self._bucket, Prefix=prefix):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:  # at most 1000 per page, the delete_objects limit
                self._s3.delete_objects(Bucket=self._bucket, Delete={"Objects": keys, "Quiet": True})


def from_settings(settings) -> Storage:
    if settings.s3_bucket:
        return S3Storage(settings.s3_bucket, endpoint_url=settings.s3_endpoint_url)
    return LocalStorage(settings.files_dir)
