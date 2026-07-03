"""Tests for the desktop client surface (magi.client).

Three layers, all in-process (no model, no network):
  * type mapping — wire/agno objects <-> the plain Reply/Media types,
  * EmbeddedClient over a fake ConversationService,
  * HttpClient over the real FastAPI app via httpx's ASGI transport,
  * SyncClient's blocking/threaded bridge over a fake async client.

The two backends are checked to agree on the same fake reply, since "one surface,
two backends" is the whole point.
"""

import base64

import httpx
import pytest
from agno.media import Audio, Image

from magi.channels.api import create_app
from magi.client import EmbeddedClient, HttpClient, SyncClient, connect
from magi.client.embedded import reply_from_conversation
from magi.client.types import (
    Delta,
    InboundImage,
    Media,
    Reply,
    inbound_to_wire,
    reply_from_wire,
)
from magi.core.conversation import ConversationDelta, ConversationReply


class _FakeConversation:
    """ConversationService stand-in recording what the client called."""

    def __init__(self, reply: ConversationReply | None = None):
        self.reply = reply or ConversationReply(text="the answer")
        self.calls: list[tuple] = []
        self.last_media: dict | None = None

    async def handle(self, *, user_id, session_id, text, media=None, extra_context=""):
        self.calls.append(("handle", user_id, session_id, text))
        self.last_media = media
        return self.reply

    async def handle_stream(self, *, user_id, session_id, text, media=None, extra_context=""):
        self.calls.append(("handle_stream", user_id, session_id, text))
        self.last_media = media
        for chunk in ("the ", "answer"):
            yield ConversationDelta(text=chunk)
        yield self.reply

    def flush(self, user_id, session_id):
        self.calls.append(("flush", user_id, session_id))
        return 7

    def context_stats(self, user_id, session_id):
        self.calls.append(("context_stats", user_id, session_id))
        return {"est_tokens": 42, "sections": {}}


# --- type mapping ------------------------------------------------------------
def test_reply_from_conversation_maps_media_bytes_and_urls():
    reply = ConversationReply(
        text="here you go",
        reasoning="because",
        images=(Image(content=b"png-bytes", mime_type="image/png"),),
        audio=(Audio(url="https://cdn.example/x.mp3", mime_type="audio/mpeg"),),
    )

    out = reply_from_conversation(reply)

    assert out.text == "here you go" and out.reasoning == "because"
    image = next(m for m in out.media if m.kind == "image")
    assert image.data == b"png-bytes" and image.url is None
    audio = next(m for m in out.media if m.kind == "audio")
    assert audio.url == "https://cdn.example/x.mp3" and audio.data is None


def test_media_data_uri_round_trips():
    m = Media(kind="image", mime_type="image/png", data=b"abc")
    assert m.data_uri == "data:image/png;base64," + base64.b64encode(b"abc").decode()
    assert Media(kind="file").data_uri is None


def test_reply_from_wire_decodes_base64():
    payload = {
        "text": "hi",
        "reasoning": None,
        "is_error": False,
        "media": [
            {"kind": "image", "mime_type": "image/png", "data_base64": base64.b64encode(b"x").decode()},
            {"kind": "audio", "url": "https://cdn/x.mp3"},
        ],
    }
    out = reply_from_wire(payload)
    assert out.media[0].data == b"x" and out.media[1].url == "https://cdn/x.mp3"


def test_inbound_to_wire_serializes_bytes_url_and_drops_empty():
    wire = inbound_to_wire(
        [
            InboundImage(data=b"x", mime_type="image/png"),
            InboundImage(url="https://cdn/p.png"),
            InboundImage(mime_type="image/png"),  # no payload — dropped
        ]
    )
    assert len(wire) == 2
    assert wire[0]["data_base64"] == base64.b64encode(b"x").decode()
    assert wire[1]["url"] == "https://cdn/p.png"


# --- EmbeddedClient ----------------------------------------------------------
async def test_embedded_send_scopes_user_and_maps_reply():
    conv = _FakeConversation()
    client = EmbeddedClient(conv, user_id="u1", session_id="win-1")

    reply = await client.send("hi")

    assert isinstance(reply, Reply) and reply.text == "the answer"
    # Same scoping the HTTP channel applies server-side ("api:u1").
    assert conv.calls == [("handle", "api:u1", "win-1", "hi")]


async def test_embedded_stream_yields_deltas_then_reply():
    conv = _FakeConversation()
    client = EmbeddedClient(conv, user_id="u1", session_id="s")

    items = [item async for item in client.stream("hi")]

    assert items[:2] == [Delta("the "), Delta("answer")]
    assert isinstance(items[-1], Reply) and items[-1].text == "the answer"
    assert conv.calls[0] == ("handle_stream", "api:u1", "s", "hi")


async def test_embedded_inbound_image_becomes_agno_image():
    conv = _FakeConversation()
    client = EmbeddedClient(conv, user_id="u1", session_id="s")

    await client.send("look", images=[InboundImage(data=b"\x89PNG", mime_type="image/png")])

    img = conv.last_media["images"][0]
    assert img.content == b"\x89PNG" and img.mime_type == "image/png" and img.format == "png"


async def test_embedded_flush_and_context_stats():
    conv = _FakeConversation()
    client = EmbeddedClient(conv, user_id="u1", session_id="s")

    assert await client.flush() == 7
    assert (await client.context_stats())["est_tokens"] == 42
    assert conv.calls == [("flush", "api:u1", "s"), ("context_stats", "api:u1", "s")]


async def test_embedded_mcp_toolkits_connect_on_open_and_close():
    events: list[str] = []

    class _Toolkit:
        async def connect(self):
            events.append("connect")

        async def close(self):
            events.append("close")

    conv = _FakeConversation()
    client = EmbeddedClient(conv, user_id="u1", mcp_toolkits=[_Toolkit()])
    async with client:
        assert events == ["connect"]
    assert events == ["connect", "close"]


# --- HttpClient (over the real app via ASGI transport) -----------------------
def _http_client_over(conv: _FakeConversation, **kw) -> HttpClient:
    transport = httpx.ASGITransport(app=create_app(conv))
    ac = httpx.AsyncClient(transport=transport, base_url="http://test")
    return HttpClient("http://test", user_id="u1", session_id="win-1", client=ac, **kw)


async def test_http_send_hits_the_v1_contract():
    conv = _FakeConversation()
    client = _http_client_over(conv)

    reply = await client.send("hi")

    assert reply.text == "the answer"
    assert conv.calls == [("handle", "api:u1", "win-1", "hi")]
    await client.aclose()


async def test_http_stream_parses_sse_into_deltas_then_reply():
    conv = _FakeConversation()
    client = _http_client_over(conv)

    items = [item async for item in client.stream("hi")]

    assert items[:2] == [Delta("the "), Delta("answer")]
    assert isinstance(items[-1], Reply) and items[-1].text == "the answer"
    assert conv.calls[0] == ("handle_stream", "api:u1", "win-1", "hi")
    await client.aclose()


async def test_http_media_round_trips_through_the_wire():
    conv = _FakeConversation(
        ConversationReply(
            text="x",
            images=(Image(content=b"png-bytes", mime_type="image/png"),),
            audio=(Audio(url="https://cdn/x.mp3", mime_type="audio/mpeg"),),
        )
    )
    client = _http_client_over(conv)

    reply = await client.send("hi")

    image = next(m for m in reply.media if m.kind == "image")
    assert image.data == b"png-bytes"
    audio = next(m for m in reply.media if m.kind == "audio")
    assert audio.url == "https://cdn/x.mp3"
    await client.aclose()


async def test_http_flush_and_context_stats():
    conv = _FakeConversation()
    client = _http_client_over(conv)

    assert await client.flush() == 7
    assert (await client.context_stats())["est_tokens"] == 42
    await client.aclose()


def test_http_wires_bearer_header_on_its_own_client():
    """When it builds its own AsyncClient, the auth token rides every request."""
    client = HttpClient("http://test", user_id="u1", auth_token="secret")
    assert client._client.headers["authorization"] == "Bearer secret"


async def test_http_call_is_rejected_without_the_required_token():
    """The app gates /v1; an unauthenticated client's call surfaces as an error."""
    conv = _FakeConversation()
    transport = httpx.ASGITransport(app=create_app(conv, auth_token="secret"))
    ac = httpx.AsyncClient(transport=transport, base_url="http://test")
    client = HttpClient("http://test", user_id="u1", session_id="s", client=ac)

    with pytest.raises(httpx.HTTPStatusError):
        await client.send("hi")
    assert conv.calls == []
    await ac.aclose()


async def test_connect_builds_an_http_client():
    client = connect("http://127.0.0.1:8000/", user_id="u1", auth_token="t")
    assert isinstance(client, HttpClient)
    assert client.user_id == "u1" and client.session_id == "default"
    await client.aclose()


# --- backends agree ----------------------------------------------------------
async def test_embedded_and_http_agree_on_the_same_reply():
    reply = ConversationReply(text="same", reasoning="r")
    embedded = EmbeddedClient(_FakeConversation(reply), user_id="u1", session_id="s")
    http = _http_client_over(_FakeConversation(reply))

    e = await embedded.send("hi")
    h = await http.send("hi")
    assert e == h  # frozen dataclasses compare by value
    await http.aclose()


# --- SyncClient (blocking/threaded bridge) -----------------------------------
class _FakeAsyncClient:
    """Minimal async MagiClient for exercising SyncClient off any event loop."""

    def __init__(self):
        self.user_id = "u1"
        self.session_id = "s"
        self.opened = False
        self.closed = False

    async def aopen(self):
        self.opened = True
        return self

    async def aclose(self):
        self.closed = True

    async def send(self, text, *, images=()):
        return Reply(text=f"echo:{text}")

    async def stream(self, text, *, images=()):
        for chunk in ("a", "b"):
            yield Delta(text=chunk)
        yield Reply(text="ab")

    async def flush(self):
        return 3

    async def context_stats(self):
        return {"est_tokens": 1}


def test_sync_client_blocks_and_bridges_stream():
    fake = _FakeAsyncClient()
    with SyncClient(fake) as ui:
        assert fake.opened is True
        assert ui.user_id == "u1" and ui.session_id == "s"
        assert ui.send("hi").text == "echo:hi"
        assert list(ui.stream("go")) == [Delta("a"), Delta("b"), Reply("ab")]
        assert ui.flush() == 3
        assert ui.context_stats()["est_tokens"] == 1
    assert fake.closed is True


def test_sync_client_stream_reraises_errors_in_the_caller():
    class _Boom(_FakeAsyncClient):
        async def stream(self, text, *, images=()):
            yield Delta(text="partial")
            raise RuntimeError("stream blew up")

    with SyncClient(_Boom()) as ui:
        gen = ui.stream("go")
        assert next(gen) == Delta("partial")
        with pytest.raises(RuntimeError, match="stream blew up"):
            next(gen)
