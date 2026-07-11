"""Tests for the voice sidecar client (core.voice).

`VoiceService` is pure I/O over httpx, so these run against a fake
`AsyncClient` — no sidecar, no network. Focus: request shaping (payloads,
mood-style merging, multipart form), response handling (mime resolution, the
verbose_json → json fallback), and the typed failure contract
(`VoiceUnavailable` vs `VoiceUpstreamError`).
"""

import json

import httpx
import pytest

from magi.core import voice as voice_mod
from magi.core.voice import (
    VoiceService,
    VoiceUnavailable,
    VoiceUpstreamError,
    build_voice_service,
)


class _FakeResponse:
    def __init__(self, status_code=200, content=b"", json_body=None, headers=None):
        self.status_code = status_code
        self.content = content
        self._json = json_body
        self.headers = headers or {}
        self.text = (
            json.dumps(json_body) if json_body is not None else content.decode("utf-8", "replace")
        )

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json


class _FakeClient:
    """httpx.AsyncClient stand-in: pops canned responses, records each post."""

    calls: list[dict] = []  # rebound per test via _install

    def __init__(self, responses):
        self._responses = responses

    def __call__(self, **kwargs):  # the AsyncClient(**kwargs) constructor call
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        type(self).calls.append({"url": url, **kwargs})
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _install(monkeypatch, responses):
    client = _FakeClient(list(responses))
    _FakeClient.calls = []
    monkeypatch.setattr(voice_mod.httpx, "AsyncClient", client)
    return _FakeClient.calls


def _tts_service(**overrides):
    kwargs = dict(
        tts_base_url="http://tts:1/v1",
        tts_voice="af_heart",
        tts_format="mp3",
        tts_mood_styles={"wry": {"speed": 0.95, "input": "EVIL", "model": "EVIL"}},
    )
    kwargs.update(overrides)
    return VoiceService(**kwargs)


def _stt_service(**overrides):
    kwargs = dict(stt_base_url="http://stt:1/v1", stt_model="whisper-1")
    kwargs.update(overrides)
    return VoiceService(**kwargs)


# --- synthesize ----------------------------------------------------------------


async def test_synthesize_requires_tts_configured():
    with pytest.raises(VoiceUnavailable):
        await VoiceService().synthesize("hi")


async def test_synthesize_posts_the_speech_request_and_returns_audio(monkeypatch):
    calls = _install(
        monkeypatch,
        [_FakeResponse(content=b"mp3-bytes", headers={"content-type": "audio/mpeg"})],
    )

    audio, mime = await _tts_service().synthesize("hello there")

    assert (audio, mime) == (b"mp3-bytes", "audio/mpeg")
    assert calls[0]["url"] == "http://tts:1/v1/audio/speech"
    assert calls[0]["json"] == {
        "model": "tts-1", "voice": "af_heart", "input": "hello there", "response_format": "mp3",
    }


async def test_synthesize_merges_the_mood_style_but_never_text_or_model(monkeypatch):
    calls = _install(monkeypatch, [_FakeResponse(content=b"x")])

    await _tts_service().synthesize("hello", mood="wry")

    payload = calls[0]["json"]
    assert payload["speed"] == 0.95  # the style landed
    assert payload["input"] == "hello" and payload["model"] == "tts-1"  # protected keys


async def test_synthesize_unknown_mood_falls_back_to_the_base_voice(monkeypatch):
    calls = _install(monkeypatch, [_FakeResponse(content=b"x")])

    await _tts_service().synthesize("hello", mood="no-such-mood")

    assert "speed" not in calls[0]["json"]


async def test_synthesize_resolves_mime_from_format_when_the_sidecar_omits_it(monkeypatch):
    _install(monkeypatch, [_FakeResponse(content=b"x", headers={})])

    _, mime = await _tts_service().synthesize("hello")

    assert mime == "audio/mpeg"


async def test_synthesize_maps_failures_onto_the_typed_contract(monkeypatch):
    _install(monkeypatch, [_FakeResponse(status_code=500, content=b"boom")])
    with pytest.raises(VoiceUpstreamError):
        await _tts_service().synthesize("hello")

    _install(monkeypatch, [httpx.ConnectError("refused")])
    with pytest.raises(VoiceUnavailable):
        await _tts_service().synthesize("hello")


# --- transcribe ------------------------------------------------------------------


async def test_transcribe_requires_stt_configured():
    with pytest.raises(VoiceUnavailable):
        await VoiceService().transcribe(b"bytes")


async def test_transcribe_posts_multipart_and_reads_verbose_json(monkeypatch):
    calls = _install(
        monkeypatch,
        [_FakeResponse(json_body={"text": " hi there ", "language": "en", "duration": 2.5})],
    )

    result = await _stt_service().transcribe(b"opus", filename="a.ogg", mime="audio/ogg")

    assert (result.text, result.language, result.duration) == ("hi there", "en", 2.5)
    assert calls[0]["url"] == "http://stt:1/v1/audio/transcriptions"
    assert calls[0]["files"] == {"file": ("a.ogg", b"opus", "audio/ogg")}
    assert calls[0]["data"] == {"model": "whisper-1", "response_format": "verbose_json"}


async def test_transcribe_falls_back_to_plain_json_once(monkeypatch):
    calls = _install(
        monkeypatch,
        [_FakeResponse(status_code=422, content=b"no verbose"), _FakeResponse(json_body={"text": "ok"})],
    )

    result = await _stt_service().transcribe(b"webm")

    assert result.text == "ok" and result.language is None and result.duration is None
    assert calls[1]["data"]["response_format"] == "json"


async def test_transcribe_rejects_a_sidecar_with_no_text(monkeypatch):
    _install(monkeypatch, [_FakeResponse(json_body={"segments": []})])
    with pytest.raises(VoiceUpstreamError):
        await _stt_service().transcribe(b"webm")

    _install(monkeypatch, [_FakeResponse(content=b"<html>")])
    with pytest.raises(VoiceUpstreamError):
        await _stt_service().transcribe(b"webm")


# --- composition -----------------------------------------------------------------


def test_build_voice_service_is_none_when_both_sides_are_off():
    assert build_voice_service() is None  # defaults: tts_enabled = stt_enabled = False


def test_build_voice_service_enables_only_the_configured_sides():
    from magi.core.config import config, configure

    old = (config.tts_enabled, config.stt_enabled)
    configure(tts_enabled=True, stt_enabled=False)
    try:
        service = build_voice_service()
        assert service is not None
        assert service.tts_enabled and not service.stt_enabled
    finally:
        configure(tts_enabled=old[0], stt_enabled=old[1])
