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


async def test_status_unwraps_and_compacts(monkeypatch):
    """The raw status payload is ~7k chars of theme/torrent settings; the tool
    returns only the conversational facts (version, user, adult setting)."""
    status = {
        "version": "2.0.0",
        "versionName": "Hakumei",
        "os": "windows",
        "serverReady": True,
        "isOffline": False,
        "serverHasPassword": False,
        "user": {"viewer": {"name": "carneirofc", "options": {"displayAdultContent": True}}},
        "settings": {
            "anilist": {"enableAdultContent": True},
            "torrent": {"noise": "x" * 5000},
        },
    }
    _patch_client(monkeypatch, [_FakeResponse(json_data={"data": status})])
    result = await seanime.seanime_status.entrypoint()
    assert "2.0.0" in result and "carneirofc" in result
    assert "Adult content: enabled" in result
    assert "noise" not in result and len(result) < 500  # trimmed, not dumped


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


async def test_search_anime_adult_include_omits_filter_and_flags_results(monkeypatch):
    page = {
        "Page": {
            "media": [
                {"id": 99, "title": {"romaji": "Some Adult Title"}, "isAdult": True},
                {"id": 100, "title": {"romaji": "Safe Title"}},
            ]
        }
    }
    client = _patch_client(monkeypatch, [_FakeResponse(json_data={"data": page})])
    result = await seanime.seanime_search_anime.entrypoint(search="title", adult="include")
    _, _, body = client.calls[0]
    # Key omitted entirely: AniList then returns both adult and non-adult
    # (isAdult=true would mean adult-ONLY and is coerced by a server setting).
    assert "isAdult" not in body
    assert '"isAdult":true' in result  # adult results are flagged
    assert "Safe Title" in result


async def test_search_anime_adult_only_sends_true(monkeypatch):
    page = {"Page": {"media": [{"id": 99, "title": {"romaji": "X"}, "isAdult": True}]}}
    client = _patch_client(monkeypatch, [_FakeResponse(json_data={"data": page})])
    await seanime.seanime_search_anime.entrypoint(search="x", adult="only")
    _, _, body = client.calls[0]
    assert body["isAdult"] is True


async def test_search_anime_filters_ride_in_the_body(monkeypatch):
    """Every user-stated filter must reach the API (genres normalized to
    AniList casing, status/sort as arrays, empty search omitted)."""
    page = {"Page": {"media": []}}
    client = _patch_client(monkeypatch, [_FakeResponse(json_data={"data": page})])
    await seanime.seanime_search_anime.entrypoint(
        search="",
        genres=["romance", "sci fi"],
        season="winter",
        year=2024,
        format="tv",
        status="finished",
        sort="score_desc",
        per_page=5,
    )
    _, _, body = client.calls[0]
    assert "search" not in body  # empty search breaks AniList results — omitted
    assert body["genres"] == ["Romance", "Sci-Fi"]
    assert body["season"] == "WINTER" and body["seasonYear"] == 2024
    assert body["format"] == "TV"
    assert body["status"] == ["FINISHED"] and body["sort"] == ["SCORE_DESC"]
    assert body["isAdult"] is False


async def test_search_anime_rejects_unknown_filter_values(monkeypatch):
    _patch_client(monkeypatch, [])
    assert "Unknown genre" in await seanime.seanime_search_anime.entrypoint(
        search="x", genres=["isekai"]
    )
    assert "Unknown season" in await seanime.seanime_search_anime.entrypoint(
        search="x", season="autumn"
    )
    assert "Unknown sort" in await seanime.seanime_search_anime.entrypoint(
        search="x", sort="BEST_FIRST"
    )
    assert "Unknown adult mode" in await seanime.seanime_search_anime.entrypoint(
        search="x", adult="maybe"
    )
    # Hentai while excluding adult contradicts itself — the tool says how to fix it.
    assert 'adult="only"' in await seanime.seanime_search_anime.entrypoint(
        search="", genres=["Hentai"]
    )


async def test_search_results_carry_genres_year_and_cover(monkeypatch):
    page = {
        "Page": {
            "media": [
                {
                    "id": 1,
                    "title": {"romaji": "T"},
                    "genres": ["Action", "Drama", "Romance", "Comedy"],
                    "coverImage": {"large": "https://img/cover.jpg"},
                    "startDate": {"year": 2019},
                }
            ]
        }
    }
    _patch_client(monkeypatch, [_FakeResponse(json_data={"data": page})])
    result = await seanime.seanime_search_manga.entrypoint(search="t")
    assert '"cover":"https://img/cover.jpg"' in result
    assert '"year":2019' in result
    assert '"genres":["Action","Drama","Romance"]' in result  # capped at 3


async def test_search_empty_page_suggests_widening(monkeypatch):
    _patch_client(monkeypatch, [_FakeResponse(json_data={"data": {"Page": {"media": []}}})])
    result = await seanime.seanime_search_anime.entrypoint(search="zzz")
    assert "no results" in result


async def test_update_progress_posts_and_confirms(monkeypatch):
    client = _patch_client(monkeypatch, [_FakeResponse(json_data={"data": True})])
    result = await seanime.seanime_update_progress.entrypoint(
        media_id=21, episode_number=1091, total_episodes=1100
    )
    assert "1091" in result and "21" in result
    method, url, body = client.calls[0]
    assert method == "POST" and url.endswith("/api/v1/library/anime-entry/update-progress")
    assert body == {"mediaId": 21, "episodeNumber": 1091, "totalEpisodes": 1100}


async def test_library_overview_groups_by_genre(monkeypatch):
    collection = {
        "lists": [
            {
                "type": "CURRENT",
                "entries": [
                    {
                        "mediaId": 1,
                        "media": {
                            "title": {"romaji": "A"},
                            "genres": ["Action", "Drama"],
                            "episodes": 12,
                        },
                        "listData": {"progress": 5, "score": 8},
                    },
                    {
                        "mediaId": 2,
                        "media": {"title": {"romaji": "B"}, "genres": ["Action"], "episodes": 24},
                        "listData": {"progress": 24, "score": 9},
                    },
                ],
            }
        ]
    }
    _patch_client(monkeypatch, [_FakeResponse(json_data={"data": collection})])
    result = await seanime.seanime_library_overview.entrypoint(group_by="genre")
    assert "2 entries, 29 episodes of progress" in result
    assert "mean score 8.5 over 2 scored" in result
    assert "## Action (2)" in result and "## Drama (1)" in result
    assert "A, B" in result


async def test_library_overview_score_orders_numerically(monkeypatch):
    def entry(i, score):
        return {
            "mediaId": i,
            "media": {"title": {"romaji": f"T{i}"}, "episodes": 1},
            "listData": {"progress": 1, "score": score},
        }

    collection = {
        "lists": [{"type": "COMPLETED", "entries": [entry(1, 9), entry(2, 10), entry(3, 0)]}]
    }
    _patch_client(monkeypatch, [_FakeResponse(json_data={"data": collection})])
    result = await seanime.seanime_library_overview.entrypoint(group_by="score")
    # 10 before 9 (numeric, not lexical); unscored last.
    assert result.index("## score 10") < result.index("## score 9") < result.index("## unscored")


async def test_library_overview_rejects_unknown_dimension(monkeypatch):
    _patch_client(monkeypatch, [])
    result = await seanime.seanime_library_overview.entrypoint(group_by="studio")
    assert "Unknown group_by" in result


async def test_manga_collection_compacts_with_chapters(monkeypatch):
    collection = {
        "lists": [
            {
                "type": "CURRENT",
                "entries": [
                    {
                        "mediaId": 30002,
                        "media": {"title": {"romaji": "Berserk"}, "chapters": 380},
                        "listData": {"progress": 120, "score": 10},
                    }
                ],
            }
        ]
    }
    client = _patch_client(monkeypatch, [_FakeResponse(json_data={"data": collection})])
    result = await seanime.seanime_manga_collection.entrypoint()
    assert "Berserk (id 30002): 120/380, score 10" in result
    assert client.calls[0][1].endswith("/api/v1/manga/collection")


async def test_manga_entry_compacts_reading_state(monkeypatch):
    entry = {
        "mediaId": 30002,
        "media": {
            "title": {"romaji": "Berserk"},
            "format": "MANGA",
            "status": "RELEASING",
            "chapters": 380,
            "volumes": 42,
            "genres": ["Action", "Horror"],
        },
        "listData": {"progress": 120, "score": 10, "status": "CURRENT"},
    }
    _patch_client(monkeypatch, [_FakeResponse(json_data={"data": entry})])
    result = await seanime.seanime_manga_entry.entrypoint(media_id=30002)
    assert "Berserk" in result and "120/380 chapters" in result
    assert "380 chapters, 42 volumes" in result
    assert "Action, Horror" in result


async def test_search_manga_defaults_filter_adult_and_compact_chapters(monkeypatch):
    page = {
        "Page": {
            "media": [
                {
                    "id": 30002,
                    "title": {"romaji": "Berserk"},
                    "format": "MANGA",
                    "status": "RELEASING",
                    "chapters": 380,
                    "volumes": 42,
                }
            ]
        }
    }
    client = _patch_client(monkeypatch, [_FakeResponse(json_data={"data": page})])
    result = await seanime.seanime_search_manga.entrypoint(search="berserk")
    method, url, body = client.calls[0]
    assert method == "POST" and url.endswith("/api/v1/manga/anilist/list")
    assert body == {"search": "berserk", "page": 1, "perPage": 10, "isAdult": False}
    assert '"chapters":380' in result and "Berserk" in result


async def test_manga_update_progress_posts_and_confirms(monkeypatch):
    client = _patch_client(monkeypatch, [_FakeResponse(json_data={"data": True})])
    result = await seanime.seanime_manga_update_progress.entrypoint(
        media_id=30002, chapter_number=121, total_chapters=380
    )
    assert "121" in result and "30002" in result
    method, url, body = client.calls[0]
    assert url.endswith("/api/v1/manga/update-progress")
    assert body == {"mediaId": 30002, "chapterNumber": 121, "totalChapters": 380}


async def test_episode_collection_lists_airdate_filler_downloaded(monkeypatch):
    episodes = {
        "hasMappingError": False,
        "episodes": [
            {
                "episodeNumber": 2,
                "episodeTitle": "Second",
                "isDownloaded": False,
                "episodeMetadata": {"airDate": "2026-01-12", "isFiller": True},
            },
            {
                "episodeNumber": 1,
                "episodeTitle": "First",
                "isDownloaded": True,
                "episodeMetadata": {"airDate": "2026-01-05"},
            },
        ],
    }
    _patch_client(monkeypatch, [_FakeResponse(json_data={"data": episodes})])
    result = await seanime.seanime_episode_collection.entrypoint(media_id=5)
    assert "2 main episode(s):" in result
    # Sorted by episode number despite response order.
    assert result.index("Ep 1") < result.index("Ep 2")
    assert "Ep 1: First (aired 2026-01-05, downloaded)" in result
    assert "Ep 2: Second (aired 2026-01-12, filler, not downloaded)" in result


async def test_anime_entry_compacts_files_and_next_episode(monkeypatch):
    entry = {
        "mediaId": 21,
        "media": {
            "title": {"romaji": "One Piece"},
            "format": "TV",
            "seasonYear": 1999,
            "status": "RELEASING",
            "episodes": 1100,
            "genres": ["Action", "Adventure"],
        },
        "listData": {"status": "CURRENT", "progress": 1090, "score": 9},
        "libraryData": {"mainFileCount": 2, "unwatchedCount": 1, "sharedPath": "J:/anime/one-piece"},
        "nextEpisode": {"episodeNumber": 1091},
        "downloadInfo": {"episodesToDownload": [{"episodeNumber": 1092}]},
        "episodes": [
            {
                "episodeNumber": 1090,
                "episodeTitle": "Big Fight",
                "type": "main",
                "localFile": {"name": "[Subs] One Piece - 1090.mkv"},
            },
            {
                "episodeNumber": 1091,
                "episodeTitle": "Bigger Fight",
                "type": "main",
                "localFile": {"name": "[Subs] One Piece - 1091.mkv"},
            },
        ],
    }
    _patch_client(monkeypatch, [_FakeResponse(json_data={"data": entry})])
    result = await seanime.seanime_anime_entry.entrypoint(media_id=21)
    assert "One Piece (id 21, TV, 1999, RELEASING)" in result
    assert "CURRENT, progress 1090/1100, score 9" in result
    assert "2 main file(s), 1 unwatched, folder J:/anime/one-piece" in result
    assert "Next to watch: Ep 1091" in result
    assert "1 aired episode(s) not downloaded yet." in result
    assert "[Subs] One Piece - 1091.mkv" in result
    # Compacted, not raw JSON.
    assert "localFile" not in result


def test_huge_payload_is_clipped():
    """The raw-render fallback (unexpected payload shapes) stays bounded."""
    result = seanime._render({"blob": "x" * 100_000})
    assert len(result) < seanime._MAX_CHARS + 100
    assert "truncated" in result


async def test_details_compacts_description_relations_and_recommendations(monkeypatch):
    details = {
        "description": "Line one.<br><br>Bold <b>text</b> &amp; more.",
        "genres": ["Adventure", "Fantasy"],
        "averageScore": 91,
        "popularity": 442197,
        "startDate": {"year": 2023},
        "duration": 24,
        "studios": {"nodes": [{"name": "MADHOUSE", "id": 11}]},
        "trailer": {"id": "abc123", "site": "youtube"},
        "rankings": [{"context": "highest rated all time", "rank": 1}],
        "tags": [
            {"name": "Magic", "rank": 90},
            {"name": "Spoilery", "rank": 99, "isMediaSpoiler": True},
            {"name": "Lewd", "rank": 50, "isAdult": True},
        ],
        "relations": {
            "edges": [
                {"relationType": "SOURCE", "node": {"id": 7, "title": {"romaji": "M"}, "format": "MANGA"}}
            ]
        },
        "recommendations": {
            "edges": [
                {"node": {"mediaRecommendation": {"id": 9, "title": {"romaji": "R"}, "meanScore": 85}}}
            ]
        },
        "siteUrl": "https://anilist.co/anime/1",
    }
    _patch_client(monkeypatch, [_FakeResponse(json_data={"data": details})])
    result = await seanime.seanime_anime_details.entrypoint(media_id=1)
    assert "Line one.\n\nBold text & more." in result  # HTML stripped
    assert "score 91/100" in result and "24 min/ep" in result
    assert "MADHOUSE" in result and "https://youtu.be/abc123" in result
    assert "Ranked #1 highest rated all time" in result
    assert "Magic" in result and "Spoilery" not in result  # spoiler tags dropped
    assert "Lewd [adult]" in result  # adult tags flagged
    assert "- SOURCE: M (MANGA, id 7)" in result
    assert "- R (id 9, score 85)" in result
    assert "https://anilist.co/anime/1" in result


async def test_missing_episodes_groups_per_anime(monkeypatch):
    payload = {
        "episodes": [
            {
                "episodeNumber": 4,
                "baseAnime": {"id": 118, "title": {"romaji": "El Hazard 2"}},
                "episodeMetadata": {"airDate": "1997-10-25", "overview": "noise " * 200},
            },
            {
                "episodeNumber": 5,
                "baseAnime": {"id": 118, "title": {"romaji": "El Hazard 2"}},
                "episodeMetadata": {"airDate": "1997-11-25"},
            },
        ],
        "silencedEpisodes": [{"episodeNumber": 1}],
    }
    _patch_client(monkeypatch, [_FakeResponse(json_data={"data": payload})])
    result = await seanime.seanime_missing_episodes.entrypoint()
    assert "2 missing episode(s):" in result
    assert "- El Hazard 2 (id 118): Ep 4 (aired 1997-10-25), Ep 5 (aired 1997-11-25)" in result
    assert "1 silenced episode(s)" in result
    assert "noise" not in result  # metadata prose trimmed


async def test_schedule_lists_upcoming_and_counts_past(monkeypatch):
    payload = [
        {"mediaId": 1, "title": "Old", "dateTime": "2000-01-01T00:00:00Z", "episodeNumber": 1},
        {
            "mediaId": 2,
            "title": "Soon",
            "dateTime": "2999-01-01T00:00:00Z",
            "episodeNumber": 10,
            "isSeasonFinale": True,
        },
    ]
    _patch_client(monkeypatch, [_FakeResponse(json_data={"data": payload})])
    result = await seanime.seanime_upcoming_schedule.entrypoint()
    assert "1 upcoming episode(s) (1 past not listed):" in result
    assert "Soon — Ep 10 (id 2) [season finale]" in result
    assert "Old" not in result


async def test_continuity_history_compacts_map(monkeypatch):
    payload = {
        "21": {
            "mediaId": 21,
            "episodeNumber": 1090,
            "currentTime": 600.0,
            "duration": 1440.0,
            "timeUpdated": "2026-06-10T20:00:00Z",
        }
    }
    _patch_client(monkeypatch, [_FakeResponse(json_data={"data": payload})])
    result = await seanime.seanime_continuity_history.entrypoint()
    assert "media 21: episode 1090, stopped at 42%" in result


async def test_continuity_history_empty(monkeypatch):
    _patch_client(monkeypatch, [_FakeResponse(json_data={"data": {}})])
    result = await seanime.seanime_continuity_history.entrypoint()
    assert "no watch history" in result


async def test_collection_and_entry_flag_adult_titles(monkeypatch):
    collection = {
        "lists": [
            {
                "type": "CURRENT",
                "entries": [
                    {
                        "mediaId": 99,
                        "media": {
                            "title": {"romaji": "Lewd Show"},
                            "episodes": 12,
                            "isAdult": True,
                        },
                        "listData": {"progress": 3},
                    }
                ],
            }
        ]
    }
    _patch_client(monkeypatch, [_FakeResponse(json_data={"data": collection})])
    result = await seanime.seanime_library_collection.entrypoint()
    assert "Lewd Show [adult] (id 99)" in result


async def test_anime_entry_shows_cover_url(monkeypatch):
    entry = {
        "mediaId": 21,
        "media": {
            "title": {"romaji": "One Piece"},
            "coverImage": {"large": "https://img/op.jpg"},
        },
        "listData": {},
    }
    _patch_client(monkeypatch, [_FakeResponse(json_data={"data": entry})])
    result = await seanime.seanime_anime_entry.entrypoint(media_id=21)
    assert "Cover: https://img/op.jpg" in result
