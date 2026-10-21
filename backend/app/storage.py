"""Blob storage abstraction.

Routes and services depend only on the ``Storage`` protocol: files are kept on a local volume
(LocalStorage, for running locally) or in S3 (S3Storage, in the cluster, where pods have no
permanent disk). STORAGE_BACKEND picks one (see from_settings).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Protocol


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


def _check_key(key: str) -> PurePosixPath:
    rel = PurePosixPath(key)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
        raise StorageError(f"invalid storage key: {key!r}")
    return rel


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
        return self._root.joinpath(*_check_key(key).parts)

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
    """Stores objects in an S3 bucket, under ``prefix``.

    Credentials come from the environment, never from settings: on EKS, the pod's IAM role
    (IRSA or EKS Pod Identity). Encryption at rest is the bucket's default encryption.
    """

    def __init__(self, bucket: str, prefix: str = "", *, client: Any = None, endpoint_url: str | None = None) -> None:
        if client is None:
            import boto3

            client = boto3.client("s3", endpoint_url=endpoint_url)
        self._client = client
        self._bucket = bucket
        self._prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""

    def _key(self, key: str) -> str:
        return self._prefix + str(_check_key(key))

    def put(self, key: str, data: bytes) -> None:
        self._client.put_object(Bucket=self._bucket, Key=self._key(key), Body=data)

    def get(self, key: str) -> bytes:
        from botocore.exceptions import ClientError

        try:
            return self._client.get_object(Bucket=self._bucket, Key=self._key(key))["Body"].read()
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                raise ObjectNotFound(key) from exc
            raise

    def delete_prefix(self, prefix: str) -> None:
        if not prefix.endswith("/"):
            raise StorageError(f"prefix must end with '/': {prefix!r}")
        full = self._key(prefix.rstrip("/")) + "/"
        pages = self._client.get_paginator("list_objects_v2").paginate(Bucket=self._bucket, Prefix=full)
        for page in pages:
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if not keys:
                continue
            result = self._client.delete_objects(Bucket=self._bucket, Delete={"Objects": keys, "Quiet": True})
            if result.get("Errors"):
                raise StorageError(f"could not delete {len(result['Errors'])} objects under {full!r}")


def from_settings(settings: Any) -> Storage:
    if settings.storage_backend == "s3":
        return S3Storage(settings.s3_bucket, settings.s3_prefix, endpoint_url=settings.s3_endpoint_url)
    return LocalStorage(settings.files_dir)
