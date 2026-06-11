"""Seanime tools: query and control the local Seanime media server.

Seanime (https://seanime.rahim.app) manages a local anime library, AniList
progress tracking, and streaming. These tools talk to its HTTP API at
`config.seanime_base_url` — a *fixed* base URL set in code, so unlike the
generic http tools the model never picks the host (no SSRF surface). When the
server runs with a password, `config.seanime_token` rides as `X-Seanime-Token`.

Each docstring is the model's contract — it reads them to decide WHEN to call a
tool and WHAT each argument means. Keep them precise.

Every endpoint answers with Seanime's `SeaResponse` envelope
(`{"data": ...}` or `{"error": ..., "details": ...}`); `_call` unwraps it and
renders the data as JSON, capped so a big payload can't blow up the context.
The library collection gets a purpose-built compact rendering instead — the raw
JSON is megabytes of nested media objects.
"""

import json
from typing import Any, Final, Optional

import httpx
from agno.tools import tool
from agno.utils.log import log_info, log_warning

from core.config import config

_TIMEOUT_S: Final[float] = 30.0
# Cap on what a tool returns to the model. ~3k tokens — enough for any single
# entry or search page; the collection endpoint compacts instead of truncating.
_MAX_CHARS: Final[int] = 12_000


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if config.seanime_token:
        headers["X-Seanime-Token"] = config.seanime_token
    return headers


def _clip(text: str) -> str:
    if len(text) <= _MAX_CHARS:
        return text
    return text[:_MAX_CHARS] + f"\n…[truncated {len(text) - _MAX_CHARS} chars]"


def _render(data: Any) -> str:
    if data is None:
        return "(no data)"
    if isinstance(data, str):
        return _clip(data)
    return _clip(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


async def _call(method: str, path: str, body: Optional[dict] = None) -> Any | str:
    """One request against the Seanime API. Returns the unwrapped `data` payload,
    or a model-readable error string (never raises)."""
    url = config.seanime_base_url.rstrip("/") + path
    log_info(f"seanime: {method} {path}")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S, headers=_headers()) as client:
            resp = await client.request(method, url, json=body)
    except httpx.HTTPError as exc:
        log_warning(f"seanime: {method} {path} failed: {exc}")
        return (
            f"Seanime is unreachable at {config.seanime_base_url} ({exc}). "
            "Is the server running?"
        )

    try:
        payload = resp.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict) and payload.get("error"):
        details = payload.get("details")
        suffix = f" — {details}" if details else ""
        return f"Seanime error (HTTP {resp.status_code}): {payload['error']}{suffix}"
    if resp.status_code >= 400:
        return f"Seanime returned HTTP {resp.status_code} for {path}."
    return payload.get("data") if isinstance(payload, dict) else payload


def _is_error(result: Any) -> bool:
    """`_call` signals failure with a plain string; payloads are dict/list/scalars."""
    return isinstance(result, str)


def _title(media: dict) -> str:
    titles = media.get("title") or {}
    return (
        titles.get("userPreferred")
        or titles.get("english")
        or titles.get("romaji")
        or f"media {media.get('id', '?')}"
    )


def _compact_collection(data: dict) -> str:
    """One line per library entry — the raw collection JSON is megabytes."""
    lines: list[str] = []
    stats = data.get("stats") or {}
    if stats:
        lines.append(
            f"Library: {stats.get('totalEntries', '?')} entries, "
            f"{stats.get('totalFiles', '?')} files, {stats.get('totalSize', '?')}"
        )
    for lst in data.get("lists") or []:
        entries = lst.get("entries") or []
        if not entries:
            continue
        lines.append(f"\n## {lst.get('type') or lst.get('status') or 'list'} ({len(entries)})")
        for e in entries:
            media = e.get("media") or {}
            list_data = e.get("listData") or {}
            progress = list_data.get("progress", 0)
            episodes = media.get("episodes") or "?"
            score = list_data.get("score")
            score_part = f", score {score}" if score else ""
            lines.append(
                f"- {_title(media)} (id {e.get('mediaId')}): {progress}/{episodes}{score_part}"
            )
    unmatched = data.get("unmatchedGroups") or []
    if unmatched:
        lines.append(f"\n{len(unmatched)} unmatched group(s) — files not linked to any anime yet.")
    return _clip("\n".join(lines)) if lines else "(library is empty)"


@tool
async def seanime_status() -> str:
    """Get the Seanime server status: version, logged-in AniList user, settings
    flags. Call this first when the user asks whether Seanime is up, who is
    logged in, or before debugging any other Seanime call that failed."""
    result = await _call("GET", "/api/v1/status")
    return result if _is_error(result) else _render(result)


@tool
async def seanime_library_collection() -> str:
    """The user's local anime library, grouped by AniList list status (current,
    planning, completed, ...): one line per entry with title, AniList media id,
    watch progress, and score. Use it to answer "what am I watching", "what's in
    my library", or to find an anime's media id for the other tools."""
    result = await _call("GET", "/api/v1/library/collection")
    if _is_error(result):
        return result
    if isinstance(result, dict):
        return _compact_collection(result)
    return _render(result)


@tool
async def seanime_anime_entry(media_id: int) -> str:
    """Full library details for one anime by AniList media id: episodes on disk,
    next episode to watch, AniList progress and metadata. Get the id from
    `seanime_library_collection` or `seanime_search_anime`."""
    result = await _call("GET", f"/api/v1/library/anime-entry/{int(media_id)}")
    return result if _is_error(result) else _render(result)


@tool
async def seanime_missing_episodes() -> str:
    """Episodes that have aired but are not in the local library yet, per anime
    the user is watching. Use for "what am I missing" / "what can I download"."""
    result = await _call("GET", "/api/v1/library/missing-episodes")
    return result if _is_error(result) else _render(result)


@tool
async def seanime_upcoming_schedule() -> str:
    """The airing schedule for anime in the user's collection: which episodes
    air on which date. Use for "what airs this week / today / next"."""
    result = await _call("GET", "/api/v1/library/schedule")
    return result if _is_error(result) else _render(result)


@tool
async def seanime_continuity_history() -> str:
    """The user's recent watch history (continuity): what was watched last and
    where playback stopped. Use for "what was I watching" / "where did I leave off"."""
    result = await _call("GET", "/api/v1/continuity/history")
    return result if _is_error(result) else _render(result)


@tool
async def seanime_search_anime(
    search: str, page: int = 1, per_page: int = 10, include_adult: bool = False
) -> str:
    """Search AniList for anime by title through Seanime (works regardless of
    whether the anime is in the library). Returns matching media with ids,
    formats, and airing status. Use to resolve a title the user mentions to an
    AniList media id.

    Adult (18+) titles are excluded by default, so an adult title will simply
    not appear. Retry with `include_adult=True` when the user is clearly asking
    about adult content or a title they named isn't found."""
    body = {"search": search, "page": int(page), "perPage": int(per_page)}
    if not include_adult:
        # Explicit false = non-adult only. When including, OMIT the key: AniList
        # then returns both, and we sidestep the server-side EnableAdultContent
        # coercion that `isAdult: true` is subject to (true = adult-ONLY anyway).
        body["isAdult"] = False
    result = await _call("POST", "/api/v1/anilist/list-anime", body)
    if _is_error(result):
        return result
    # Strip the page envelope down to the media list; trailers/covers are noise.
    if isinstance(result, dict):
        media = (result.get("Page") or {}).get("media") or result.get("media")
        if isinstance(media, list):
            compact = [
                {
                    "id": m.get("id"),
                    "title": _title(m),
                    "format": m.get("format"),
                    "status": m.get("status"),
                    "episodes": m.get("episodes"),
                    "season": f"{m.get('season', '')} {m.get('seasonYear', '')}".strip(),
                    # Flag adult titles so the reply can say so; omit when not.
                    **({"isAdult": True} if m.get("isAdult") else {}),
                }
                for m in media
                if isinstance(m, dict)
            ]
            return _render(compact)
    return _render(result)


@tool
async def seanime_anime_details(media_id: int) -> str:
    """AniList details for one anime by media id: description, genres, studios,
    relations, recommendations. Library status is NOT included — use
    `seanime_anime_entry` for what's on disk and watch progress."""
    result = await _call("GET", f"/api/v1/anilist/media-details/{int(media_id)}")
    return result if _is_error(result) else _render(result)


@tool
async def seanime_update_progress(
    media_id: int, episode_number: int, total_episodes: int = 0
) -> str:
    """Mark an episode as watched: updates the user's AniList progress for the
    anime to `episode_number`. This CHANGES the user's AniList list — only call
    it when the user explicitly asks to mark/update progress, and confirm the
    episode number from the conversation, never guess it."""
    body = {
        "mediaId": int(media_id),
        "episodeNumber": int(episode_number),
        "totalEpisodes": int(total_episodes),
    }
    result = await _call("POST", "/api/v1/library/anime-entry/update-progress", body)
    if _is_error(result):
        return result
    return f"Progress updated: media {media_id} marked at episode {episode_number}."


# Read-mostly library/AniList access + the one deliberate mutation (progress).
SEANIME_TOOLS: Final[list[Any]] = [
    seanime_status,
    seanime_library_collection,
    seanime_anime_entry,
    seanime_missing_episodes,
    seanime_upcoming_schedule,
    seanime_continuity_history,
    seanime_search_anime,
    seanime_anime_details,
    seanime_update_progress,
]
