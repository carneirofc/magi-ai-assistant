"""Tests for the object-storage tools (agent/tools/storage).

These stub the S3 store (an in-memory dict) and `httpx.AsyncClient` so nothing
touches a real bucket or the network. The contract: store_file archives a
*sourced* URL and returns a reference; retrieve_file delivers the bytes through
the media outbox (or a presigned URL when too big / no outbox); list_files
reports what's kept — all scoped to the current user. The media URL allowlist
gates archiving exactly as it gates delivery.
"""

import httpx

import agent.tools.storage as storage_tools
from agent.tools.storage import build_storage_tools
from core.media import (
    close_allowed_media_urls,
    close_media_outbox,
    open_allowed_media_urls,
    open_media_outbox,
)
from core.memory.adapters import slug
from core.storage import ObjectInfo, StorageError, StoredObject


# --- fakes -------------------------------------------------------------------
class _Scope:
    def __init__(self, user_id: str):
        self.user_id = user_id


class _Memory:
    """Just enough of MemoryManager for the storage tools: a scope with a user id."""

    def __init__(self, user_id: str = "u1"):
        self._user_id = user_id

    def scope(self) -> _Scope:
        return _Scope(self._user_id)


class _FakeStore:
    """In-memory stand-in for S3Store with the same surface the tools call."""

    def __init__(self):
        self.objects: dict[str, tuple[bytes, str | None, dict[str, str]]] = {}

    def put_bytes(self, key, data, *, content_type=None, metadata=None):
        self.objects[key] = (data, content_type, dict(metadata or {}))
        return StoredObject(key=key, size=len(data), content_type=content_type, metadata=metadata or {})

    def get_bytes(self, key):
        if key not in self.objects:
            raise StorageError(f"missing {key}")
        return self.objects[key]

    def presigned_url(self, key, *, expires_in=None):
        return f"https://signed.example/{key}"

    def list(self, prefix, *, with_metadata=True, max_keys=100):
        return [
            ObjectInfo(key=k, size=len(v[0]), content_type=v[1], metadata=v[2])
            for k, v in self.objects.items()
            if k.startswith(prefix)
        ]

    def exists(self, key):
        return key in self.objects


class _FakeResponse:
    def __init__(self, *, content=b"", headers=None, status_code=200):
        self.content = content
        self.headers = headers or {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=httpx.Request("GET", "http://x"), response=self)


class _FakeClient:
    def __init__(self, response=None, raise_exc=None, **kwargs):
        self._response = response
        self._raise = raise_exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        if self._raise is not None:
            raise self._raise
        return self._response


def _patch_client(monkeypatch, **client_kwargs):
    monkeypatch.setattr(
        storage_tools.httpx, "AsyncClient", lambda **_: _FakeClient(**client_kwargs)
    )


def _tools():
    store = _FakeStore()
    store_file, retrieve_file, list_files = build_storage_tools(store, _Memory("u1"))
    return store, store_file, retrieve_file, list_files


def _prefix(user_id="u1"):
    return f"users/{slug(user_id)}/artifacts/"


# --- store_file --------------------------------------------------------------
async def test_store_file_archives_sourced_url(monkeypatch):
    store, store_file, _, _ = _tools()
    url = "https://cdn.example/pic.png"
    _patch_client(monkeypatch, response=_FakeResponse(content=b"png", headers={"content-type": "image/png"}))

    token = open_allowed_media_urls(f"keep this {url}")
    try:
        result = await store_file.entrypoint(source_url=url, note="a cat")
    finally:
        close_allowed_media_urls(token)

    assert result.get("success") is True
    ref = result.get("data")["reference"]
    assert ref and len(store.objects) == 1
    key = f"{_prefix()}{ref}"
    data, ctype, metadata = store.objects[key]
    assert data == b"png" and ctype == "image/png"
    assert metadata["filename"] == "pic.png" and metadata["note"] == "a cat" and metadata["source-url"] == url


async def test_store_file_refuses_unsourced_url(monkeypatch):
    _, store_file, _, _ = _tools()
    _patch_client(monkeypatch, response=_FakeResponse(content=b"png", headers={"content-type": "image/png"}))
    token = open_allowed_media_urls("a message that does not contain the url")
    try:
        result = await store_file.entrypoint(source_url="https://i.imgur.com/stale.png")
    finally:
        close_allowed_media_urls(token)
    assert result.get("success") is False and "unsourced" in result.get("message")


async def test_store_file_refuses_non_http():
    _, store_file, _, _ = _tools()
    result = await store_file.entrypoint(source_url="file:///etc/passwd")
    assert result.get("success") is False and "non-http" in result.get("message")


async def test_store_file_surfaces_storage_error(monkeypatch):
    store, store_file, _, _ = _tools()
    url = "https://cdn.example/x.png"
    _patch_client(monkeypatch, response=_FakeResponse(content=b"png", headers={"content-type": "image/png"}))

    def _boom(*a, **k):
        raise StorageError("backend down")

    monkeypatch.setattr(store, "put_bytes", _boom)
    token = open_allowed_media_urls(f"keep {url}")
    try:
        result = await store_file.entrypoint(source_url=url)
    finally:
        close_allowed_media_urls(token)
    assert result.get("success") is False and "backend down" in result.get("message")


# --- retrieve_file -----------------------------------------------------------
async def test_retrieve_file_stages_image_in_outbox():
    store, _, retrieve_file, _ = _tools()
    key = f"{_prefix()}abc123def456"
    store.objects[key] = (b"png", "image/png", {"filename": "pic.png"})

    token = open_media_outbox()
    result = await retrieve_file.entrypoint(reference="abc123def456")
    outbox = close_media_outbox(token)

    assert result.get("success") is True and "Attached the image 'pic.png'" in result.get("message")
    assert len(outbox.images) == 1 and outbox.images[0].content == b"png"


async def test_retrieve_file_unknown_reference_fails():
    _, _, retrieve_file, _ = _tools()
    token = open_media_outbox()
    result = await retrieve_file.entrypoint(reference="nope")
    close_media_outbox(token)
    assert result.get("success") is False and "No archived file" in result.get("message")


async def test_retrieve_file_too_large_returns_presigned_url(monkeypatch):
    store, _, retrieve_file, _ = _tools()
    monkeypatch.setattr(storage_tools, "_MAX_ATTACH_BYTES", 2)
    key = f"{_prefix()}big"
    store.objects[key] = (b"xxxx", "application/pdf", {"filename": "big.pdf"})

    token = open_media_outbox()
    result = await retrieve_file.entrypoint(reference="big")
    outbox = close_media_outbox(token)

    assert result.get("success") is True and "too large" in result.get("message")
    assert result.get("data")["url"] == f"https://signed.example/{key}"
    assert not outbox.files  # nothing attached when handed off as a link


async def test_retrieve_file_without_outbox_is_honest():
    store, _, retrieve_file, _ = _tools()
    key = f"{_prefix()}xyz"
    store.objects[key] = (b"png", "image/png", {"filename": "pic.png"})
    result = await retrieve_file.entrypoint(reference="xyz")
    assert result.get("success") is False and "not available" in result.get("message")
    assert result.get("data")["url"] == f"https://signed.example/{key}"


# --- list_files --------------------------------------------------------------
async def test_list_files_reports_scoped_entries():
    store, _, _, list_files = _tools()
    store.objects[f"{_prefix()}one"] = (b"a", "image/png", {"filename": "one.png", "note": "first"})
    store.objects[f"{_prefix()}two"] = (b"bb", "text/plain", {"filename": "two.txt"})
    store.objects["users/other/artifacts/three"] = (b"ccc", "image/png", {"filename": "three.png"})

    result = await list_files.entrypoint()
    data = result.get("data")
    assert data["count"] == 2
    refs = {e["reference"] for e in data["files"]}
    assert refs == {"one", "two"}


async def test_list_files_empty():
    _, _, _, list_files = _tools()
    result = await list_files.entrypoint()
    assert result.get("data")["count"] == 0 and "No files archived" in result.get("message")
