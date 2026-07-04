"""Tests for the on-demand image-fetch tool.

These stub `httpx.AsyncClient` so nothing hits the network; we only verify the
tool's branching: refuse non-http URLs, surface fetch/HTTP errors, reject
non-image and oversized responses, and on success hand back a `ToolResult`
carrying the raw bytes as an `Image` (which is what makes the model *see* it).
"""

import httpx
from agno.tools.function import ToolResult

import magi.agent.tools.vision as vision
from magi.agent.tools.vision import view_image_from_url


class _FakeResponse:
    def __init__(self, *, content=b"", headers=None, status_code=200):
        self.content = content
        self.headers = headers or {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error", request=httpx.Request("GET", "http://x"), response=self
            )


class _FakeClient:
    """Stand-in for httpx.AsyncClient that returns a canned response (or raises)."""

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
        vision.httpx, "AsyncClient", lambda **_: _FakeClient(**client_kwargs)
    )


async def test_refuses_non_http_url():
    result = await view_image_from_url.entrypoint(url="ftp://example.com/x.png")
    assert isinstance(result, ToolResult)
    assert result.images is None
    assert "non-http" in result.content.lower()


async def test_success_returns_image_bytes(monkeypatch):
    payload = b"\x89PNG\r\n\x1a\nfake-bytes"
    _patch_client(
        monkeypatch,
        response=_FakeResponse(content=payload, headers={"content-type": "image/png"}),
    )

    result = await view_image_from_url.entrypoint(
        url="https://cdn.discordapp.com/app-icons/1/abc.png?size=256"
    )

    assert isinstance(result, ToolResult)
    assert result.images and len(result.images) == 1
    image = result.images[0]
    assert image.content == payload
    assert image.mime_type == "image/png"
    assert image.format == "png"
    # Marked view-only: model input, never reposted to the user by the reply
    # media collection (core/media.py).
    from magi.core.media import is_view_only

    assert is_view_only(image)


async def test_rejects_non_image_content_type(monkeypatch):
    _patch_client(
        monkeypatch,
        response=_FakeResponse(content=b"<html>", headers={"content-type": "text/html"}),
    )

    result = await view_image_from_url.entrypoint(url="https://example.com/page")

    assert result.images is None
    assert "did not return an image" in result.content


async def test_rejects_oversized_image(monkeypatch):
    big = b"x" * (vision._MAX_IMAGE_BYTES + 1)
    _patch_client(
        monkeypatch,
        response=_FakeResponse(content=big, headers={"content-type": "image/jpeg"}),
    )

    result = await view_image_from_url.entrypoint(url="https://example.com/huge.jpg")

    assert result.images is None
    assert "too large" in result.content


async def test_surfaces_http_status_error(monkeypatch):
    _patch_client(
        monkeypatch,
        response=_FakeResponse(headers={"content-type": "image/png"}, status_code=404),
    )

    result = await view_image_from_url.entrypoint(url="https://example.com/missing.png")

    assert result.images is None
    assert "404" in result.content


async def test_surfaces_network_error(monkeypatch):
    _patch_client(monkeypatch, raise_exc=httpx.ConnectError("boom"))

    result = await view_image_from_url.entrypoint(url="https://example.com/x.png")

    assert result.images is None
    assert "could not fetch" in result.content.lower()


async def test_refuses_unsourced_url_in_conversation(monkeypatch):
    """A URL the model invented is on no allowlist → refuse before the network."""
    from magi.core.media import close_allowed_media_urls, open_allowed_media_urls

    # Never reached if the guard works; make any fetch loudly wrong.
    _patch_client(monkeypatch, raise_exc=AssertionError("should not fetch"))
    token = open_allowed_media_urls("describe the image I attached")
    try:
        result = await view_image_from_url.entrypoint(
            url="https://files.catbox.moe/2026-06-28T08-52-44-1000x1333.jpg"
        )
    finally:
        close_allowed_media_urls(token)

    assert result.images is None
    assert "unsourced image URL" in result.content


async def test_allows_user_supplied_url_in_conversation(monkeypatch):
    """A URL the user typed (seeded into the allowlist) fetches normally."""
    from magi.core.media import close_allowed_media_urls, open_allowed_media_urls

    allowed = "https://cdn.example/user.png"
    _patch_client(
        monkeypatch,
        response=_FakeResponse(content=b"png", headers={"content-type": "image/png"}),
    )
    token = open_allowed_media_urls(f"look at {allowed}")
    try:
        result = await view_image_from_url.entrypoint(url=allowed)
    finally:
        close_allowed_media_urls(token)

    assert result.images and len(result.images) == 1


async def test_allows_attached_by_reference_url(monkeypatch):
    """An image attached by reference (seeded via extra_urls) stays viewable."""
    from magi.core.media import close_allowed_media_urls, open_allowed_media_urls

    attached = "https://cdn.example/attached.png"
    _patch_client(
        monkeypatch,
        response=_FakeResponse(content=b"png", headers={"content-type": "image/png"}),
    )
    token = open_allowed_media_urls("what is this?", extra_urls=[attached])
    try:
        result = await view_image_from_url.entrypoint(url=attached)
    finally:
        close_allowed_media_urls(token)

    assert result.images and len(result.images) == 1
