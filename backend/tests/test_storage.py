from __future__ import annotations

from pathlib import Path

import pytest

from app.storage import LocalStorage, ObjectNotFound, StorageError, document_prefix, version_key


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
