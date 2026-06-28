"""Tests for the local filesystem object store (core/storage/local).

`LocalStore` is the no-server backend behind the same surface the storage tools
drive (put/get/exists/presign/list by raw key). These exercise the round-trip on
a real temp directory: bytes and their content-type + metadata survive a put/get,
listings are prefix-scoped and skip the JSON sidecars, presign hands back a
file:// URL, and a crafted key can't escape the archive root.
"""

from pathlib import Path

import pytest

from magi.core.storage import LocalStore, StorageError


def _store(tmp_path: Path) -> LocalStore:
    store = LocalStore(tmp_path / "artifacts")
    store.ensure_bucket()
    return store


def test_put_get_round_trips_bytes_and_metadata(tmp_path):
    store = _store(tmp_path)
    key = "users/u1/artifacts/abc123"
    meta = {"filename": "pic.png", "note": "a cat"}

    stored = store.put_bytes(key, b"png-bytes", content_type="image/png", metadata=meta)
    assert stored.key == key and stored.size == len(b"png-bytes")

    data, ctype, metadata = store.get_bytes(key)
    assert data == b"png-bytes"
    assert ctype == "image/png"
    assert metadata == meta


def test_put_writes_blob_and_sidecar_on_disk(tmp_path):
    store = _store(tmp_path)
    store.put_bytes("users/u1/artifacts/x", b"d", content_type="text/plain")
    blob = tmp_path / "artifacts" / "users" / "u1" / "artifacts" / "x"
    assert blob.is_file() and blob.read_bytes() == b"d"
    assert blob.with_name("x.meta.json").is_file()


def test_get_missing_raises_storage_error(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(StorageError):
        store.get_bytes("users/u1/artifacts/nope")


def test_exists(tmp_path):
    store = _store(tmp_path)
    assert not store.exists("users/u1/artifacts/k")
    store.put_bytes("users/u1/artifacts/k", b"x")
    assert store.exists("users/u1/artifacts/k")


def test_list_is_prefix_scoped_and_skips_sidecars(tmp_path):
    store = _store(tmp_path)
    store.put_bytes("users/u1/artifacts/one", b"a", content_type="image/png",
                    metadata={"filename": "one.png"})
    store.put_bytes("users/u1/artifacts/two", b"bb", content_type="text/plain")
    store.put_bytes("users/other/artifacts/three", b"ccc")

    entries = store.list("users/u1/artifacts/")
    keys = {e.key for e in entries}
    assert keys == {"users/u1/artifacts/one", "users/u1/artifacts/two"}
    one = next(e for e in entries if e.key.endswith("/one"))
    assert one.content_type == "image/png" and one.metadata["filename"] == "one.png"
    assert one.size == 1


def test_list_empty_prefix_returns_nothing(tmp_path):
    store = _store(tmp_path)
    assert store.list("users/ghost/artifacts/") == []


def test_list_respects_max_keys(tmp_path):
    store = _store(tmp_path)
    for i in range(5):
        store.put_bytes(f"users/u1/artifacts/k{i}", b"x")
    assert len(store.list("users/u1/artifacts/", max_keys=3)) == 3


def test_presigned_url_is_a_file_uri(tmp_path):
    store = _store(tmp_path)
    key = "users/u1/artifacts/big"
    store.put_bytes(key, b"xxxx")
    url = store.presigned_url(key)
    assert url.startswith("file://")
    assert url.endswith("/users/u1/artifacts/big")


def test_presigned_url_missing_raises(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(StorageError):
        store.presigned_url("users/u1/artifacts/gone")


@pytest.mark.parametrize("bad_key", ["../escape", "users/../../etc/passwd", "", "."])
def test_unsafe_keys_are_refused(tmp_path, bad_key):
    store = _store(tmp_path)
    with pytest.raises(StorageError):
        store.put_bytes(bad_key, b"x")


def test_get_tolerates_missing_sidecar(tmp_path):
    store = _store(tmp_path)
    blob = tmp_path / "artifacts" / "users" / "u1" / "artifacts" / "raw"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"loose")  # no .meta.json sidecar
    data, ctype, metadata = store.get_bytes("users/u1/artifacts/raw")
    assert data == b"loose" and ctype is None and metadata == {}
