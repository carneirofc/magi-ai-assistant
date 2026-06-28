"""Tests for the HTTP API channel (channels.api).

`create_app` is a pure factory over an injected `ConversationService`, so these
run against a fake service — no model, no Discord, no filesystem. Focus: the
wire contract (paths, bodies, validation) and the bearer-token gate.
"""

import base64
import json

from fastapi.testclient import TestClient

from magi.channels.api import create_app
from magi.core.conversation import ConversationDelta, ConversationReply


class _FakeConversation:
    """ConversationService stand-in recording what the app called."""

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


def _client(reply=None, auth_token=None):
    conversation = _FakeConversation(reply)
    return TestClient(create_app(conversation, auth_token=auth_token)), conversation


def test_healthz_is_open():
    client, _ = _client(auth_token="secret")
    assert client.get("/healthz").json() == {"status": "ok"}


def test_post_message_runs_a_turn_and_returns_the_reply():
    client, conversation = _client()

    resp = client.post(
        "/v1/sessions/win-1/messages", json={"user_id": "u1", "text": "hi"}
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "text": "the answer", "reasoning": None, "is_error": False, "media": [],
    }
    assert conversation.calls == [("handle", "u1", "win-1", "hi")]


def test_reply_media_is_serialized_on_the_wire():
    """Inline bytes ride as base64, by-reference media as its URL."""
    import base64

    from agno.media import Audio, Image

    reply = ConversationReply(
        text="here you go",
        images=(Image(content=b"png-bytes", mime_type="image/png"),),
        audio=(Audio(url="https://cdn.example/x.mp3", mime_type="audio/mpeg"),),
    )
    client, _ = _client(reply)

    resp = client.post("/v1/sessions/s/messages", json={"user_id": "u1", "text": "hi"})

    media = resp.json()["media"]
    assert len(media) == 2
    image = next(m for m in media if m["kind"] == "image")
    assert base64.b64decode(image["data_base64"]) == b"png-bytes"
    assert image["mime_type"] == "image/png" and image["url"] is None
    audio = next(m for m in media if m["kind"] == "audio")
    assert audio["url"] == "https://cdn.example/x.mp3" and audio["data_base64"] is None


def test_error_reply_travels_in_band_as_200():
    reply = ConversationReply(text="sorry, that failed", is_error=True)
    client, _ = _client(reply)

    resp = client.post("/v1/sessions/s/messages", json={"user_id": "u1", "text": "hi"})

    assert resp.status_code == 200
    assert resp.json()["is_error"] is True


def test_empty_text_is_rejected():
    client, conversation = _client()

    resp = client.post("/v1/sessions/s/messages", json={"user_id": "u1", "text": ""})

    assert resp.status_code == 422
    assert conversation.calls == []


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    """SSE frames as (event, payload) pairs."""
    frames = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        frames.append((lines["event"], json.loads(lines["data"])))
    return frames


def test_stream_emits_deltas_then_done():
    client, conversation = _client()

    resp = client.post(
        "/v1/sessions/win-1/messages/stream", json={"user_id": "u1", "text": "hi"}
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert _parse_sse(resp.text) == [
        ("delta", {"text": "the "}),
        ("delta", {"text": "answer"}),
        ("done", {"text": "the answer", "reasoning": None, "is_error": False, "media": []}),
    ]
    assert conversation.calls == [("handle_stream", "u1", "win-1", "hi")]


def test_stream_requires_bearer_token_when_configured():
    client, conversation = _client(auth_token="secret")

    resp = client.post(
        "/v1/sessions/s/messages/stream", json={"user_id": "u1", "text": "hi"}
    )

    assert resp.status_code == 401
    assert conversation.calls == []


def test_flush_closes_the_session():
    client, conversation = _client()

    resp = client.post("/v1/sessions/win-1/flush", json={"user_id": "u1"})

    assert resp.status_code == 200
    assert resp.json() == {"dropped_turns": 7}
    assert conversation.calls == [("flush", "u1", "win-1")]


def test_context_stats_passthrough():
    client, conversation = _client()

    resp = client.get("/v1/sessions/win-1/context", params={"user_id": "u1"})

    assert resp.status_code == 200
    assert resp.json()["est_tokens"] == 42
    assert conversation.calls == [("context_stats", "u1", "win-1")]


def test_v1_requires_bearer_token_when_configured():
    client, conversation = _client(auth_token="secret")
    body = {"user_id": "u1", "text": "hi"}

    assert client.post("/v1/sessions/s/messages", json=body).status_code == 401
    assert (
        client.post(
            "/v1/sessions/s/messages", json=body, headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )
    assert conversation.calls == []

    ok = client.post(
        "/v1/sessions/s/messages", json=body, headers={"Authorization": "Bearer secret"}
    )
    assert ok.status_code == 200


def test_no_token_configured_means_open_v1():
    client, _ = _client(auth_token=None)

    resp = client.post("/v1/sessions/s/messages", json={"user_id": "u1", "text": "hi"})

    assert resp.status_code == 200


# --- CORS (browser clients) --------------------------------------------------
def test_no_cors_headers_without_origins():
    """Default app returns no CORS headers — same-origin / non-browser only."""
    client, _ = _client()
    resp = client.post(
        "/v1/sessions/s/messages",
        json={"user_id": "u1", "text": "hi"},
        headers={"Origin": "https://app.example.com"},
    )
    assert "access-control-allow-origin" not in resp.headers


def test_cors_headers_present_when_origins_configured():
    conversation = _FakeConversation()
    client = TestClient(create_app(conversation, cors_origins=["*"]))

    # Preflight: the browser asks before the real request.
    preflight = client.options(
        "/v1/sessions/s/messages",
        headers={
            "Origin": "https://app.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "*"

    # Actual request also carries the allow-origin header.
    resp = client.post(
        "/v1/sessions/s/messages",
        json={"user_id": "u1", "text": "hi"},
        headers={"Origin": "https://app.example.com"},
    )
    assert resp.headers["access-control-allow-origin"] == "*"


# --- MCP toolkit lifecycle ---------------------------------------------------
class _FakeToolkit:
    """Stands in for an agno MCP toolkit; records connect/close."""

    def __init__(self) -> None:
        self.events: list[str] = []

    async def connect(self) -> None:
        self.events.append("connect")

    async def close(self) -> None:
        self.events.append("close")


def test_mcp_toolkits_connect_at_startup_and_close_at_shutdown():
    toolkit = _FakeToolkit()
    app = create_app(_FakeConversation(), mcp_toolkits=[toolkit])

    # The lifespan runs on context enter/exit of the TestClient.
    with TestClient(app) as client:
        assert toolkit.events == ["connect"]
        client.post("/v1/sessions/s/messages", json={"user_id": "u1", "text": "hi"})
    assert toolkit.events == ["connect", "close"]


def test_mcp_connect_failure_does_not_crash_startup():
    class _Boom(_FakeToolkit):
        async def connect(self) -> None:  # type: ignore[override]
            raise RuntimeError("seanime mcp unreachable")

    app = create_app(_FakeConversation(), mcp_toolkits=[_Boom()])
    with TestClient(app) as client:
        resp = client.post("/v1/sessions/s/messages", json={"user_id": "u1", "text": "hi"})
        assert resp.status_code == 200


# --- OpenAI-compatible shim (stock chat UIs) ---------------------------------
def test_models_advertises_one_model():
    client, _ = _client()
    body = client.get("/v1/models").json()
    assert body["object"] == "list"
    assert [m["id"] for m in body["data"]] == ["chatbot"]


def test_chat_completions_forwards_only_the_last_user_turn():
    """OpenAI clients resend the whole transcript; memory carries the history, so
    only the latest user message is forwarded."""
    client, conversation = _client()

    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "chatbot",
            "messages": [
                {"role": "system", "content": "be nice"},
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
                {"role": "user", "content": "what's up"},
            ],
        },
    )

    assert resp.status_code == 200
    out = resp.json()
    assert out["object"] == "chat.completion"
    assert out["choices"][0]["message"] == {"role": "assistant", "content": "the answer"}
    assert out["choices"][0]["finish_reason"] == "stop"
    # Only the last user message reaches the brain.
    assert conversation.calls[0][0] == "handle"
    assert conversation.calls[0][3] == "what's up"


def test_chat_completions_array_content_is_flattened():
    client, conversation = _client()

    client.post(
        "/v1/chat/completions",
        json={
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "from parts"}]}
            ]
        },
    )

    assert conversation.calls[0][3] == "from parts"


def test_chat_completions_derives_a_stable_session_from_the_first_message():
    """Same chat (same first user message) → same server session across turns;
    an explicit X-Session-Id overrides the derivation."""
    client, conversation = _client()

    def session_of(messages, headers=None):
        conversation.calls.clear()
        client.post(
            "/v1/chat/completions", json={"messages": messages}, headers=headers or {}
        )
        return conversation.calls[0][2]

    first = session_of([{"role": "user", "content": "open"}])
    again = session_of(
        [
            {"role": "user", "content": "open"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "more"},
        ]
    )
    assert first == again  # stable across the chat's turns
    other = session_of([{"role": "user", "content": "different opener"}])
    assert other != first
    forced = session_of(
        [{"role": "user", "content": "open"}], headers={"X-Session-Id": "exact"}
    )
    assert forced == "exact"


def test_chat_completions_scopes_user_from_field_and_header():
    client, conversation = _client()

    client.post("/v1/chat/completions", json={"user": "field-user", "messages": [
        {"role": "user", "content": "hi"}]})
    assert conversation.calls[0][1] == "field-user"

    conversation.calls.clear()
    client.post(
        "/v1/chat/completions",
        json={"user": "field-user", "messages": [{"role": "user", "content": "hi"}]},
        headers={"X-User-Id": "header-user"},
    )
    assert conversation.calls[0][1] == "header-user"  # header wins


def test_chat_completions_rejects_no_user_message():
    client, conversation = _client()
    resp = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "system", "content": "only system"}]},
    )
    assert resp.status_code == 400
    assert conversation.calls == []


def test_chat_completions_folds_reply_media_into_markdown():
    from agno.media import Image

    reply = ConversationReply(
        text="here you go",
        images=(Image(url="https://cdn.example/x.png", mime_type="image/png"),),
    )
    client, _ = _client(reply)

    resp = client.post(
        "/v1/chat/completions", json={"messages": [{"role": "user", "content": "pic"}]}
    )

    content = resp.json()["choices"][0]["message"]["content"]
    assert "here you go" in content
    assert "![image](https://cdn.example/x.png)" in content


def _parse_data_sse(body: str) -> list:
    """OpenAI stream frames (bare `data:` lines) as parsed payloads / the [DONE] marker."""
    out = []
    for block in body.strip().split("\n\n"):
        data = block.split("data: ", 1)[1]
        out.append(data if data == "[DONE]" else json.loads(data))
    return out


def test_chat_completions_streams_openai_chunks():
    client, conversation = _client()

    resp = client.post(
        "/v1/chat/completions",
        json={"stream": True, "messages": [{"role": "user", "content": "hi"}]},
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    frames = _parse_data_sse(resp.text)
    assert frames[-1] == "[DONE]"
    assert frames[0]["choices"][0]["delta"] == {"role": "assistant"}
    contents = [
        f["choices"][0]["delta"].get("content")
        for f in frames[1:-1]
        if isinstance(f, dict)
    ]
    assert "".join(c for c in contents if c) == "the answer"
    assert frames[-2]["choices"][0]["finish_reason"] == "stop"
    assert conversation.calls[0][0] == "handle_stream"


def test_chat_completions_requires_bearer_token_when_configured():
    client, conversation = _client(auth_token="secret")
    resp = client.post(
        "/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]}
    )
    assert resp.status_code == 401
    assert conversation.calls == []
    assert client.get("/v1/models").status_code == 401


# --- inbound images (client → agent) -----------------------------------------
_PNG_B64 = base64.b64encode(b"\x89PNGfake").decode()


def test_native_message_accepts_inbound_image_base64():
    client, conversation = _client()

    resp = client.post(
        "/v1/sessions/s/messages",
        json={
            "user_id": "u1",
            "text": "what is this",
            "images": [{"data_base64": _PNG_B64, "mime_type": "image/png"}],
        },
    )

    assert resp.status_code == 200
    images = conversation.last_media["images"]
    assert len(images) == 1
    assert images[0].content == b"\x89PNGfake"
    assert images[0].mime_type == "image/png"
    assert images[0].format == "png"


def test_native_message_decodes_data_uri_in_url():
    client, conversation = _client()

    client.post(
        "/v1/sessions/s/messages",
        json={
            "user_id": "u1",
            "text": "look",
            "images": [{"url": f"data:image/jpeg;base64,{_PNG_B64}"}],
        },
    )

    img = conversation.last_media["images"][0]
    assert img.content == b"\x89PNGfake"
    assert img.mime_type == "image/jpeg"


def test_native_message_passes_http_url_by_reference():
    client, conversation = _client()

    client.post(
        "/v1/sessions/s/messages",
        json={
            "user_id": "u1",
            "text": "look",
            "images": [{"url": "https://cdn.example/p.png"}],
        },
    )

    img = conversation.last_media["images"][0]
    assert img.url == "https://cdn.example/p.png"
    assert img.content is None


def test_native_message_allows_image_only_turn():
    client, conversation = _client()

    resp = client.post(
        "/v1/sessions/s/messages",
        json={"user_id": "u1", "text": "", "images": [{"data_base64": _PNG_B64}]},
    )

    assert resp.status_code == 200
    assert conversation.calls[0][3] == ""
    assert len(conversation.last_media["images"]) == 1


def test_chat_completions_forwards_inbound_image():
    client, conversation = _client()

    resp = client.post(
        "/v1/chat/completions",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "what's this"},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{_PNG_B64}"},
                        },
                    ],
                }
            ]
        },
    )

    assert resp.status_code == 200
    assert conversation.calls[0][3] == "what's this"
    img = conversation.last_media["images"][0]
    assert img.content == b"\x89PNGfake"
    assert img.mime_type == "image/png"


def test_chat_completions_allows_image_only_message():
    client, conversation = _client()

    resp = client.post(
        "/v1/chat/completions",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{_PNG_B64}"},
                        }
                    ],
                }
            ]
        },
    )

    assert resp.status_code == 200
    assert conversation.calls[0][3] == ""
    assert len(conversation.last_media["images"]) == 1
