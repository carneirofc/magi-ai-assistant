"""Tests for the Danbooru/Civitai lookup tools.

These stub `httpx.AsyncClient` so nothing hits the network. We verify the two
behaviours that keep the bot un-banned — the per-host minimum request gap and
the single Retry-After backoff on 429 — plus each tool's contract: verified
tags with post counts, error lines instead of raises, and HTML-stripped
Civitai notes.
"""

import dataclasses
import time

import httpx
import pytest

import magi.agent.tools.danbooru as danbooru
from magi.core.config import Config


def _tool_text(result: dict) -> str:
    data = result.get("data") or {}
    return " ".join(str(part) for part in (result.get("message", ""), data.get("text", ""), data) if part)


def _tools(config: Config) -> dict:
    """Build the danbooru tools with `config` and index them by name."""
    return {t.name: t for t in danbooru.build_danbooru_tools(config)}


class _FakeResponse:
    def __init__(self, *, json_data=None, status_code=200, headers=None):
        self._json = json_data
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("GET", "http://x"),
                response=self,
            )


class _FakeClient:
    """Returns canned responses in order; records requested URLs and params."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        self.calls.append((url, params))
        return self._responses.pop(0)


@pytest.fixture(autouse=True)
def fast_throttles(monkeypatch):
    """No real waiting between requests during tests."""
    monkeypatch.setattr(danbooru._danbooru_throttle, "gap_s", 0.0)
    monkeypatch.setattr(danbooru._civitai_throttle, "gap_s", 0.0)


@pytest.fixture
def no_local_data(monkeypatch):
    """Point the local-store paths at nothing so these tests exercise the API
    path even when the real artifact CSVs exist on the host."""
    monkeypatch.setattr(danbooru, "_stores", {})
    return dataclasses.replace(
        Config(),
        danbooru_tags_csv="missing/tags.csv",
        danbooru_wiki_csv="missing/wiki.csv",
    )


def _patch_client(monkeypatch, responses):
    client = _FakeClient(responses)
    monkeypatch.setattr(danbooru.httpx, "AsyncClient", lambda **_: client)
    return client


async def test_throttle_enforces_minimum_gap():
    throttle = danbooru._Throttle(0.05)
    start = time.monotonic()
    await throttle.wait()
    await throttle.wait()
    assert time.monotonic() - start >= 0.05


async def test_429_backs_off_per_retry_after_then_succeeds(monkeypatch, no_local_data):
    client = _patch_client(
        monkeypatch,
        [
            _FakeResponse(status_code=429, headers={"Retry-After": "2"}),
            _FakeResponse(json_data=[]),
        ],
    )
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(danbooru.asyncio, "sleep", fake_sleep)

    result = await _tools(no_local_data)["danbooru_search_tags"].entrypoint(query="maid")

    assert "No tags match" in _tool_text(result)
    assert sleeps == [2.0]
    assert len(client.calls) == 2


async def test_second_429_returns_error_line(monkeypatch, no_local_data):
    _patch_client(
        monkeypatch,
        [_FakeResponse(status_code=429), _FakeResponse(status_code=429)],
    )

    async def fake_sleep(s):
        pass

    monkeypatch.setattr(danbooru.asyncio, "sleep", fake_sleep)

    result = await _tools(no_local_data)["danbooru_search_tags"].entrypoint(query="maid")

    assert "failed" in _tool_text(result) and "429" in _tool_text(result)


async def test_search_tags_wildcards_query_and_formats_counts(monkeypatch, no_local_data):
    client = _patch_client(
        monkeypatch,
        [
            _FakeResponse(
                json_data=[
                    {"name": "maid", "category": 0, "post_count": 123456},
                    {"name": "maid_headdress", "category": 0, "post_count": 65432},
                ]
            )
        ],
    )

    result = await _tools(no_local_data)["danbooru_search_tags"].entrypoint(query="Maid")

    _, params = client.calls[0]
    assert params["search[name_matches]"] == "*maid*"
    assert "- maid (general, 123456 posts)" in _tool_text(result)
    assert "- maid_headdress (general, 65432 posts)" in _tool_text(result)


async def test_search_artists_filters_category_and_hits_live_api(monkeypatch, no_local_data):
    client = _patch_client(
        monkeypatch,
        [
            _FakeResponse(
                json_data=[
                    {"name": "wlop", "category": 1, "post_count": 4321},
                ]
            )
        ],
    )

    result = await _tools(no_local_data)["danbooru_search_artists"].entrypoint(query="WLOP")

    _, params = client.calls[0]
    assert params["search[name_matches]"] == "*wlop*"
    assert params["search[category]"] == 1
    assert "- wlop (artist, 4321 posts)" in _tool_text(result)


async def test_search_artists_reports_no_match(monkeypatch, no_local_data):
    _patch_client(monkeypatch, [_FakeResponse(json_data=[])])

    result = await _tools(no_local_data)["danbooru_search_artists"].entrypoint(query="nobody_xyz")

    assert "No artist tags match 'nobody_xyz'" in _tool_text(result)


async def test_wiki_normalizes_title_and_returns_body(monkeypatch, no_local_data):
    client = _patch_client(
        monkeypatch,
        [
            _FakeResponse(
                json_data={"title": "list_of_uniforms", "body": "[[school_uniform]]"}
            )
        ],
    )

    result = await _tools(no_local_data)["danbooru_wiki"].entrypoint(title="List of Uniforms")

    url, _ = client.calls[0]
    assert url.endswith("/wiki_pages/list_of_uniforms.json")
    assert "[[school_uniform]]" in _tool_text(result)


async def test_related_tags_reports_empty_result(monkeypatch, no_local_data):
    _patch_client(monkeypatch, [_FakeResponse(json_data={"related_tags": []})])

    result = await _tools(no_local_data)["danbooru_related_tags"].entrypoint(tag="collarbone")

    assert "No related tags" in _tool_text(result)


async def test_post_tags_lists_each_posts_tag_strings(monkeypatch, no_local_data):
    _patch_client(
        monkeypatch,
        [
            _FakeResponse(
                json_data=[
                    {
                        "id": 1,
                        "score": 50,
                        "rating": "g",
                        "tag_string_character": "hatsune_miku",
                        "tag_string_copyright": "vocaloid",
                        "tag_string_general": "1girl collarbone smile",
                    }
                ]
            )
        ],
    )

    result = await _tools(no_local_data)["danbooru_post_tags"].entrypoint(tags="collarbone")

    assert "Post 1" in _tool_text(result)
    assert "characters: hatsune_miku" in _tool_text(result)
    assert "general: 1girl collarbone smile" in _tool_text(result)


async def test_civitai_model_strips_html_and_lists_versions(monkeypatch, no_local_data):
    _patch_client(
        monkeypatch,
        [
            _FakeResponse(
                json_data={
                    "name": "MatureRitual",
                    "type": "Checkpoint",
                    "tags": ["illustrious"],
                    "description": "<p>Use <b>CFG 5</b> and Euler a.</p>",
                    "modelVersions": [
                        {"id": 2730987, "name": "v9", "baseModel": "Illustrious"}
                    ],
                }
            )
        ],
    )

    result = await _tools(no_local_data)["civitai_model"].entrypoint(model_id=994401)

    assert "MatureRitual" in _tool_text(result)
    assert "- 2730987: v9 (base: Illustrious)" in _tool_text(result)
    assert "Use CFG 5 and Euler a." in _tool_text(result)
    assert "<p>" not in _tool_text(result)


async def test_civitai_version_reports_trained_words(monkeypatch, no_local_data):
    _patch_client(
        monkeypatch,
        [
            _FakeResponse(
                json_data={
                    "name": "v9",
                    "baseModel": "Illustrious",
                    "trainedWords": ["mature female"],
                    "description": "",
                    "model": {"name": "MatureRitual"},
                }
            )
        ],
    )

    result = await _tools(no_local_data)["civitai_model_version"].entrypoint(version_id=2730987)

    assert "MatureRitual" in _tool_text(result) and "v9" in _tool_text(result)
    assert "Trained words: mature female" in _tool_text(result)


async def test_fetch_error_degrades_to_error_line(monkeypatch, no_local_data):
    class _BoomClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None):
            raise httpx.ConnectError("boom")

    monkeypatch.setattr(danbooru.httpx, "AsyncClient", lambda **_: _BoomClient())

    result = await _tools(no_local_data)["danbooru_wiki"].entrypoint(title="collarbone")

    assert _tool_text(result).startswith("Could not fetch wiki page 'collarbone'")
