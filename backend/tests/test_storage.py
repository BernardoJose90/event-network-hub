from __future__ import annotations

from pathlib import Path

import pytest

from app.storage import LocalStorage, ObjectNotFound, S3Storage, StorageError, document_prefix, version_key


def test_put_get_roundtrip(tmp_path: Path) -> None:
    s = LocalStorage(tmp_path)
    s.put(version_key(1, 1, "full.pdf"), b"%PDF")
    assert s.get(version_key(1, 1, "full.pdf")) == b"%PDF"


def test_delete_prefix_removes_only_that_document(tmp_path: Path) -> None:
    s = LocalStorage(tmp_path)
    s.put(version_key(1, 1, "original.xlsx"), b"a")
    s.put(version_key(1, 2, "full.pdf"), b"b")
    s.put(version_key(2, 1, "full.pdf"), b"c")

    s.delete_prefix(document_prefix(1))

    with pytest.raises(ObjectNotFound):
        s.get(version_key(1, 1, "original.xlsx"))
    assert s.get(version_key(2, 1, "full.pdf")) == b"c"
    s.delete_prefix(document_prefix(1))  # already gone: no error


@pytest.mark.parametrize("prefix", ["", "/", "documents", "../", "documents/../"])
def test_delete_prefix_rejects_dangerous_prefixes(tmp_path: Path, prefix: str) -> None:
    s = LocalStorage(tmp_path / "root")
    with pytest.raises(StorageError):
        s.delete_prefix(prefix)


@pytest.mark.parametrize("key", ["../x", "/etc/passwd", "a/../../x"])
def test_rejects_keys_escaping_root(tmp_path: Path, key: str) -> None:
    with pytest.raises(StorageError):
        LocalStorage(tmp_path).put(key, b"x")


# --- S3Storage (botocore's Stubber checks the exact S3 calls; no network) ------------------------


@pytest.fixture
def s3():
    import boto3
    from botocore.stub import Stubber

    client = boto3.client("s3", region_name="eu-west-2", aws_access_key_id="test", aws_secret_access_key="test")
    with Stubber(client) as stub:
        yield S3Storage("hub-bucket", client=client), stub
        stub.assert_no_pending_responses()


def test_s3_put_and_get(s3) -> None:
    import io

    from botocore.response import StreamingBody

    storage, stub = s3
    key = version_key(1, 1, "full.pdf")
    stub.add_response("put_object", {}, {"Bucket": "hub-bucket", "Key": key, "Body": b"%PDF"})
    stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(b"%PDF"), 4)},
                      {"Bucket": "hub-bucket", "Key": key})
    storage.put(key, b"%PDF")
    assert storage.get(key) == b"%PDF"


def test_s3_missing_object_raises(s3) -> None:
    storage, stub = s3
    stub.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)
    with pytest.raises(ObjectNotFound):
        storage.get(version_key(1, 1, "full.pdf"))


def test_s3_delete_prefix_deletes_only_that_document(s3) -> None:
    storage, stub = s3
    keys = [version_key(1, 1, "original.xlsx"), version_key(1, 2, "full.pdf")]
    stub.add_response("list_objects_v2", {"Contents": [{"Key": k} for k in keys]},
                      {"Bucket": "hub-bucket", "Prefix": "documents/1/"})
    stub.add_response("delete_objects", {},
                      {"Bucket": "hub-bucket", "Delete": {"Objects": [{"Key": k} for k in keys], "Quiet": True}})
    storage.delete_prefix(document_prefix(1))


@pytest.mark.parametrize("prefix", ["", "/", "documents", "../", "documents/../"])
def test_s3_refuses_bad_prefixes(s3, prefix: str) -> None:
    storage, _ = s3
    with pytest.raises(StorageError):
        storage.delete_prefix(prefix)
