"""Tests for the Seanime media-server tools.

These stub `httpx.AsyncClient` so nothing hits the network. They pin the tool
contract: the SeaResponse envelope is unwrapped, server errors and unreachable
hosts come back as readable strings (never raises), the auth header rides only
when a token is configured, big payloads are clipped, and the library
collection is compacted to one line per entry instead of dumped as raw JSON.
"""

import httpx
import pytest

import agent.tools.seanime as seanime
from core.config import config, configure


class _FakeResponse:
    def __init__(self, *, json_data=None, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class _FakeClient:
    """Returns canned responses in order; records (method, url, json) calls."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.headers = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, url, json=None):
        self.calls.append((method, url, json))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _patch_client(monkeypatch, responses):
    client = _FakeClient(responses)

    def factory(**kwargs):
        client.headers = kwargs.get("headers")
        return client

    monkeypatch.setattr(seanime.httpx, "AsyncClient", factory)
    return client


async def test_status_unwraps_data_envelope(monkeypatch):
    _patch_client(
        monkeypatch,
        [_FakeResponse(json_data={"data": {"version": "2.0.0", "user": "carneirofc"}})],
    )
    result = await seanime.seanime_status.entrypoint()
    assert "2.0.0" in result and "carneirofc" in result


async def test_server_error_becomes_readable_string(monkeypatch):
    _patch_client(
        monkeypatch,
        [_FakeResponse(json_data={"error": "UNAUTHENTICATED"}, status_code=401)],
    )
    result = await seanime.seanime_status.entrypoint()
    assert "Seanime error" in result and "UNAUTHENTICATED" in result


async def test_unreachable_server_never_raises(monkeypatch):
    _patch_client(monkeypatch, [httpx.ConnectError("connection refused")])
    result = await seanime.seanime_status.entrypoint()
    assert "unreachable" in result
    assert config.seanime_base_url in result


async def test_token_header_only_when_configured(monkeypatch):
    before = config.seanime_token
    try:
        configure(seanime_token=None)
        client = _patch_client(monkeypatch, [_FakeResponse(json_data={"data": {}})])
        await seanime.seanime_status.entrypoint()
        assert "X-Seanime-Token" not in client.headers

        configure(seanime_token="hash123")
        client = _patch_client(monkeypatch, [_FakeResponse(json_data={"data": {}})])
        await seanime.seanime_status.entrypoint()
        assert client.headers["X-Seanime-Token"] == "hash123"
    finally:
        configure(seanime_token=before)


async def test_library_collection_compacts_entries(monkeypatch):
    collection = {
        "stats": {"totalEntries": 2, "totalFiles": 24, "totalSize": "12 GB"},
        "lists": [
            {
                "type": "CURRENT",
                "entries": [
                    {
                        "mediaId": 21,
                        "media": {"title": {"userPreferred": "One Piece"}, "episodes": 1100},
                        "listData": {"progress": 1090, "score": 9},
                    }
                ],
            },
            {
                "type": "PLANNING",
                "entries": [
                    {
                        "mediaId": 170942,
                        "media": {"title": {"romaji": "Frieren"}, "episodes": 28},
                        "listData": {},
                    }
                ],
            },
        ],
    }
    _patch_client(monkeypatch, [_FakeResponse(json_data={"data": collection})])
    result = await seanime.seanime_library_collection.entrypoint()
    assert "One Piece (id 21): 1090/1100, score 9" in result
    assert "Frieren (id 170942): 0/28" in result
    assert "CURRENT (1)" in result and "PLANNING (1)" in result
    # Compacted, not raw JSON.
    assert "userPreferred" not in result


async def test_search_anime_compacts_page(monkeypatch):
    page = {
        "Page": {
            "media": [
                {
                    "id": 16498,
                    "title": {"english": "Attack on Titan"},
                    "format": "TV",
                    "status": "FINISHED",
                    "episodes": 25,
                    "season": "SPRING",
                    "seasonYear": 2013,
                }
            ]
        }
    }
    client = _patch_client(monkeypatch, [_FakeResponse(json_data={"data": page})])
    result = await seanime.seanime_search_anime.entrypoint(search="titan")
    assert "16498" in result and "Attack on Titan" in result
    method, url, body = client.calls[0]
    assert method == "POST" and url.endswith("/api/v1/anilist/list-anime")
    # Adult titles are filtered out by default — explicit false, not omitted.
    assert body == {"search": "titan", "page": 1, "perPage": 10, "isAdult": False}


async def test_search_anime_include_adult_omits_filter_and_flags_results(monkeypatch):
    page = {
        "Page": {
            "media": [
                {"id": 99, "title": {"romaji": "Some Adult Title"}, "isAdult": True},
                {"id": 100, "title": {"romaji": "Safe Title"}},
            ]
        }
    }
    client = _patch_client(monkeypatch, [_FakeResponse(json_data={"data": page})])
    result = await seanime.seanime_search_anime.entrypoint(search="title", include_adult=True)
    _, _, body = client.calls[0]
    # Key omitted entirely: AniList then returns both adult and non-adult
    # (isAdult=true would mean adult-ONLY and is coerced by a server setting).
    assert "isAdult" not in body
    assert '"isAdult":true' in result  # adult results are flagged
    assert "Safe Title" in result


async def test_update_progress_posts_and_confirms(monkeypatch):
    client = _patch_client(monkeypatch, [_FakeResponse(json_data={"data": True})])
    result = await seanime.seanime_update_progress.entrypoint(
        media_id=21, episode_number=1091, total_episodes=1100
    )
    assert "1091" in result and "21" in result
    method, url, body = client.calls[0]
    assert method == "POST" and url.endswith("/api/v1/library/anime-entry/update-progress")
    assert body == {"mediaId": 21, "episodeNumber": 1091, "totalEpisodes": 1100}


async def test_huge_payload_is_clipped(monkeypatch):
    _patch_client(
        monkeypatch,
        [_FakeResponse(json_data={"data": {"blob": "x" * 100_000}})],
    )
    result = await seanime.seanime_anime_details.entrypoint(media_id=1)
    assert len(result) < seanime._MAX_CHARS + 100
    assert "truncated" in result
