from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from app.config import Settings
from app.storage import (
    LocalStorage, ObjectNotFound, S3Storage, StorageError, document_prefix, from_settings, version_key,
)

BUCKET = "event-network-hub-test"


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> Iterator:
    """An in-memory S3 (moto) with an empty bucket."""
    for name, value in (("AWS_ACCESS_KEY_ID", "testing"), ("AWS_SECRET_ACCESS_KEY", "testing"),
                        ("AWS_DEFAULT_REGION", "eu-west-1")):
        monkeypatch.setenv(name, value)
    with mock_aws():
        client = boto3.client("s3")
        client.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-west-1"})
        yield client


@pytest.fixture(params=["local", "s3"])
def store(request: pytest.FixtureRequest, tmp_path: Path):
    if request.param == "local":
        return LocalStorage(tmp_path / "root")
    return S3Storage(BUCKET, client=request.getfixturevalue("s3"))


def test_put_get_roundtrip(store) -> None:
    store.put(version_key(1, 1, "full.pdf"), b"%PDF")
    assert store.get(version_key(1, 1, "full.pdf")) == b"%PDF"
    store.put(version_key(1, 1, "full.pdf"), b"%PDF v2")  # replaced
    assert store.get(version_key(1, 1, "full.pdf")) == b"%PDF v2"


def test_missing_object(store) -> None:
    with pytest.raises(ObjectNotFound):
        store.get(version_key(1, 1, "full.pdf"))


def test_delete_prefix_removes_only_that_document(store) -> None:
    store.put(version_key(1, 1, "original.xlsx"), b"a")
    store.put(version_key(1, 2, "full.pdf"), b"b")
    store.put(version_key(2, 1, "full.pdf"), b"c")
    store.put(version_key(11, 1, "full.pdf"), b"d")  # documents/11/ shares documents/1's first characters

    store.delete_prefix(document_prefix(1))

    with pytest.raises(ObjectNotFound):
        store.get(version_key(1, 1, "original.xlsx"))
    with pytest.raises(ObjectNotFound):
        store.get(version_key(1, 2, "full.pdf"))
    assert store.get(version_key(2, 1, "full.pdf")) == b"c"
    assert store.get(version_key(11, 1, "full.pdf")) == b"d"
    store.delete_prefix(document_prefix(1))  # already gone: no error


@pytest.mark.parametrize("prefix", ["", "/", "documents", "../", "documents/../"])
def test_delete_prefix_rejects_dangerous_prefixes(store, prefix: str) -> None:
    with pytest.raises(StorageError):
        store.delete_prefix(prefix)


@pytest.mark.parametrize("key", ["../x", "/etc/passwd", "a/../../x"])
def test_rejects_keys_escaping_root(store, key: str) -> None:
    with pytest.raises(StorageError):
        store.put(key, b"x")


def test_s3_keys_go_under_the_prefix(s3) -> None:
    store = S3Storage(BUCKET, "/prod/", client=s3)
    store.put(version_key(1, 1, "full.pdf"), b"%PDF")
    assert [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET)["Contents"]] == ["prod/documents/1/v1/full.pdf"]
    other = S3Storage(BUCKET, "staging", client=s3)  # another environment in the same bucket
    with pytest.raises(ObjectNotFound):
        other.get(version_key(1, 1, "full.pdf"))
    other.put(version_key(1, 1, "full.pdf"), b"other")
    store.delete_prefix(document_prefix(1))
    assert other.get(version_key(1, 1, "full.pdf")) == b"other"


def test_s3_delete_prefix_deletes_more_than_one_page(s3) -> None:
    store = S3Storage(BUCKET, client=s3)
    for n in range(1, 1003):  # S3 lists and deletes at most 1000 keys per call
        s3.put_object(Bucket=BUCKET, Key=version_key(1, n, "full.pdf"), Body=b"x")
    store.delete_prefix(document_prefix(1))
    assert s3.list_objects_v2(Bucket=BUCKET).get("KeyCount") == 0


def test_backend_comes_from_settings(tmp_path: Path, s3) -> None:
    settings = Settings(data_dir=tmp_path, gotenberg_url="http://unused", gotenberg_timeout_seconds=1,
                        max_upload_bytes=1024, database_url="postgresql://unused")
    assert isinstance(from_settings(settings), LocalStorage)
    store = from_settings(replace(settings, storage_backend="s3", s3_bucket=BUCKET, s3_prefix="prod"))
    assert isinstance(store, S3Storage)
    store.put("documents/1/v1/full.pdf", b"%PDF")
    assert s3.get_object(Bucket=BUCKET, Key="prod/documents/1/v1/full.pdf")["Body"].read() == b"%PDF"
