"""Tests for the media delivery tool (agent/tools/media) and the outbox.

These stub `httpx.AsyncClient` so nothing hits the network. The contract: a
fetched URL is classified by content-type and *staged* in the per-run outbox
(never returned as ToolResult media — outbox bytes must not enter the model's
context), with readable errors for bad URLs / failures and an honest message
when no outbox is open.
"""

import httpx

import agent.tools.media as media_tools
from agent.tools.media import send_media_from_url
from agent.tools.outputs import ToolOutput
from core.media import (
    allow_media_url,
    close_allowed_media_urls,
    close_media_outbox,
    collect_reply_media,
    is_view_only,
    open_allowed_media_urls,
    open_media_outbox,
    stage_media,
    view_only_id,
)


def _tool_text(result: dict) -> str:
    return result.get("message", "")


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
        media_tools.httpx, "AsyncClient", lambda **_: _FakeClient(**client_kwargs)
    )


async def test_image_is_staged_in_outbox_not_returned(monkeypatch):
    _patch_client(
        monkeypatch,
        response=_FakeResponse(content=b"png", headers={"content-type": "image/png"}),
    )
    token = open_media_outbox()
    result = await send_media_from_url.entrypoint(url="https://cdn.example/icon.png")
    outbox = close_media_outbox(token)

    assert isinstance(result, ToolOutput) and "Attached the image 'icon.png'" in _tool_text(result)
    assert len(outbox.images) == 1
    assert outbox.images[0].content == b"png"
    assert outbox.images[0].mime_type == "image/png"
    assert not outbox.audio and not outbox.files and not outbox.videos


async def test_audio_and_unknown_types_classify_correctly(monkeypatch):
    token = open_media_outbox()
    _patch_client(
        monkeypatch,
        response=_FakeResponse(content=b"mp3", headers={"content-type": "audio/mpeg"}),
    )
    await send_media_from_url.entrypoint(url="https://cdn.example/x.mp3")
    _patch_client(
        monkeypatch,
        response=_FakeResponse(content=b"%PDF", headers={"content-type": "application/pdf"}),
    )
    await send_media_from_url.entrypoint(url="https://cdn.example/doc.pdf")
    outbox = close_media_outbox(token)

    assert len(outbox.audio) == 1 and outbox.audio[0].format == "mpeg"
    assert len(outbox.files) == 1 and outbox.files[0].filename == "doc.pdf"


async def test_no_outbox_is_an_honest_failure(monkeypatch):
    _patch_client(
        monkeypatch,
        response=_FakeResponse(content=b"png", headers={"content-type": "image/png"}),
    )
    result = await send_media_from_url.entrypoint(url="https://cdn.example/icon.png")
    assert "not available" in _tool_text(result) and "https://cdn.example/icon.png" in _tool_text(result)


async def test_refuses_non_http_url():
    result = await send_media_from_url.entrypoint(url="file:///etc/passwd")
    assert "non-http" in _tool_text(result)


async def test_surfaces_http_and_network_errors(monkeypatch):
    _patch_client(
        monkeypatch,
        response=_FakeResponse(headers={"content-type": "image/png"}, status_code=404),
    )
    result = await send_media_from_url.entrypoint(url="https://x/missing.png")
    assert "404" in _tool_text(result)

    _patch_client(monkeypatch, raise_exc=httpx.ConnectError("boom"))
    result = await send_media_from_url.entrypoint(url="https://x/a.png")
    assert "Could not fetch" in _tool_text(result)


async def test_conversation_allowlist_refuses_unsourced_media_url(monkeypatch):
    _patch_client(
        monkeypatch,
        response=_FakeResponse(content=b"png", headers={"content-type": "image/png"}),
    )
    token = open_allowed_media_urls("thumbnail from seanime")
    try:
        result = await send_media_from_url.entrypoint(url="https://i.imgur.com/stale.png")
    finally:
        close_allowed_media_urls(token)

    assert "unsourced media URL" in _tool_text(result)


async def test_conversation_allowlist_allows_user_supplied_media_url(monkeypatch):
    _patch_client(
        monkeypatch,
        response=_FakeResponse(content=b"png", headers={"content-type": "image/png"}),
    )
    allowed = "https://cdn.example/user.png"
    allow_token = open_allowed_media_urls(f"send this {allowed}")
    outbox_token = open_media_outbox()
    try:
        result = await send_media_from_url.entrypoint(url=allowed)
        outbox = close_media_outbox(outbox_token)
    finally:
        close_allowed_media_urls(allow_token)

    assert "Attached the image" in _tool_text(result)
    assert len(outbox.images) == 1


async def test_conversation_allowlist_allows_source_registered_media_url(monkeypatch):
    _patch_client(
        monkeypatch,
        response=_FakeResponse(content=b"png", headers={"content-type": "image/png"}),
    )
    allowed = "https://cdn.example/seanime-cover.png"
    allow_token = open_allowed_media_urls("thumbnail from seanime")
    outbox_token = open_media_outbox()
    try:
        allow_media_url(allowed)
        result = await send_media_from_url.entrypoint(url=allowed)
        outbox = close_media_outbox(outbox_token)
    finally:
        close_allowed_media_urls(allow_token)

    assert "Attached the image" in _tool_text(result)
    assert len(outbox.images) == 1


async def test_oversized_file_is_rejected(monkeypatch):
    big = b"x" * (media_tools._MAX_FETCH_BYTES + 1)
    _patch_client(
        monkeypatch,
        response=_FakeResponse(content=big, headers={"content-type": "image/png"}),
    )
    token = open_media_outbox()
    result = await send_media_from_url.entrypoint(url="https://x/huge.png")
    outbox = close_media_outbox(token)
    assert "too large" in _tool_text(result)
    assert not outbox.images


# --- core.media collection ----------------------------------------------------
def test_collect_reply_media_merges_and_filters_view_only():
    from types import SimpleNamespace

    from agno.media import Image

    viewed = Image(id=view_only_id(), content=b"viewed")
    delivered = Image(content=b"delivered")
    response = SimpleNamespace(images=[viewed, delivered], videos=None, audio=None, files=None)

    token = open_media_outbox()
    stage_media(images=(Image(content=b"staged"),))
    outbox = close_media_outbox(token)

    media = collect_reply_media(response, outbox)
    contents = [i.content for i in media["images"]]
    assert contents == [b"delivered", b"staged"]
    assert is_view_only(viewed) and not is_view_only(delivered)


def test_stage_media_without_outbox_reports_false():
    from agno.media import Image

    assert stage_media(images=(Image(content=b"x"),)) is False
