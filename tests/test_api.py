"""Tests for the HTTP API channel (channels.api).

`create_app` is a pure factory over an injected `ConversationService`, so these
run against a fake service — no model, no Discord, no filesystem. Focus: the
wire contract (paths, bodies, validation) and the bearer-token gate.
"""

import json

from fastapi.testclient import TestClient

from channels.api import create_app
from core.conversation import ConversationDelta, ConversationReply


class _FakeConversation:
    """ConversationService stand-in recording what the app called."""

    def __init__(self, reply: ConversationReply | None = None):
        self.reply = reply or ConversationReply(text="the answer")
        self.calls: list[tuple] = []

    async def handle(self, *, user_id, session_id, text, media=None, extra_context=""):
        self.calls.append(("handle", user_id, session_id, text))
        return self.reply

    async def handle_stream(self, *, user_id, session_id, text, media=None, extra_context=""):
        self.calls.append(("handle_stream", user_id, session_id, text))
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
