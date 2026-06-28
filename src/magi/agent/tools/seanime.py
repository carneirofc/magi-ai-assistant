"""Seanime tools: query and control the local Seanime media server.

Seanime (https://seanime.rahim.app) manages a local anime library, manga
reading, AniList progress tracking, and streaming. These tools talk to its
HTTP API at `config.seanime_base_url` — a *fixed* base URL set in code, so
unlike the generic http tools the model never picks the host (no SSRF
surface). When the server runs with a password, `config.seanime_token` rides
as `X-Seanime-Token`.

Each docstring is the model's contract — it reads them to decide WHEN to call
a tool and WHAT each argument means. Keep them precise.

Every endpoint answers with Seanime's `SeaResponse` envelope
(`{"data": ...}` or `{"error": ..., "details": ...}`); `_call` unwraps it and
renders the data as JSON, capped so a big payload can't blow up the context.
Collection-shaped payloads (anime library, manga library, entries, episode
lists) get purpose-built compact renderings instead — the raw JSON is
megabytes of nested media objects. Grouping/summarization is computed *here*,
deterministically, so "group my library by genre" costs one tool call, not a
full collection dump into the model's context.

Tools are shaped as use-case workflows for the model, not 1:1 endpoint
mirrors: one call per conversational job — resolve a named title local-first
(`seanime_find`), list/summarize the library (`seanime_library`), deep-dive
one id (`seanime_media_info`), discover by filters (`seanime_browse_*`).
Multi-step lookups are orchestrated here in code, so the model can't mix up
"the user's library" with "the global AniList catalog" — every rendering
labels which one it came from.
"""

import json
import re
from collections import defaultdict
from html import unescape
from typing import Annotated, Any, Final, Literal, Optional
from urllib.parse import quote

import httpx
from agno.tools import tool
from agno.utils.log import log_info, log_warning
from pydantic import Field

from magi.agent.tools.outputs import FlexiblePayload, ToolOutput, fail, ok
from magi.core.config import config
from magi.core.media import allow_media_url

_TIMEOUT_S: Final[float] = 30.0
# Cap on what a tool returns to the model. ~3k tokens — enough for any single
# entry or search page; collection endpoints compact instead of truncating.
_MAX_CHARS: Final[int] = 12_000
# Per-group title cap in overviews and per-entry episode cap; beyond it the
# rest is counted, not listed.
_MAX_TITLES_PER_GROUP: Final[int] = 40
_MAX_EPISODE_LINES: Final[int] = 60
# Caps for the compact details rendering.
_DESC_MAX_CHARS: Final[int] = 700
_TAG_LIMIT: Final[int] = 8
_RELATION_LIMIT: Final[int] = 8
_RECOMMENDATION_LIMIT: Final[int] = 5

# --- AniList filter vocabularies (validated here so the API never sees junk;
# values verified against the live Seanime endpoints) -------------------------
_SEASONS: Final[frozenset[str]] = frozenset({"WINTER", "SPRING", "SUMMER", "FALL"})
_ANIME_FORMATS: Final[frozenset[str]] = frozenset(
    {"TV", "TV_SHORT", "MOVIE", "SPECIAL", "OVA", "ONA", "MUSIC"}
)
_MANGA_FORMATS: Final[frozenset[str]] = frozenset({"MANGA", "NOVEL", "ONE_SHOT"})
_STATUSES: Final[frozenset[str]] = frozenset(
    {"FINISHED", "RELEASING", "NOT_YET_RELEASED", "CANCELLED", "HIATUS"}
)
_SORTS: Final[frozenset[str]] = frozenset(
    {
        "SCORE_DESC", "SCORE", "POPULARITY_DESC", "POPULARITY", "TRENDING_DESC",
        "FAVOURITES_DESC", "START_DATE_DESC", "START_DATE", "END_DATE_DESC",
        "EPISODES_DESC", "CHAPTERS_DESC", "VOLUMES_DESC",
        "TITLE_ROMAJI", "TITLE_ENGLISH", "UPDATED_AT_DESC",
    }
)
# AniList's fixed genre list, keyed by lowercase for normalization.
_GENRES: Final[dict[str, str]] = {
    g.lower(): g
    for g in (
        "Action", "Adventure", "Comedy", "Drama", "Ecchi", "Fantasy", "Hentai",
        "Horror", "Mahou Shoujo", "Mecha", "Music", "Mystery", "Psychological",
        "Romance", "Sci-Fi", "Slice of Life", "Sports", "Supernatural", "Thriller",
    )
}
_GENRE_ALIASES: Final[dict[str, str]] = {
    "sci fi": "Sci-Fi",
    "scifi": "Sci-Fi",
    "science fiction": "Sci-Fi",
    "magical girl": "Mahou Shoujo",
    "slice-of-life": "Slice of Life",
}


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


def _drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _drop_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_drop_none(v) for v in value]
    return value


def _json_result(**fields: Any) -> str:
    return _render(_drop_none(fields))


class SeanimeData(FlexiblePayload):
    pass


SeanimeOutput = ToolOutput[SeanimeData]


def _data(**fields: object) -> SeanimeData:
    return SeanimeData(**fields)


def _tool_result(message: str, value: Any) -> SeanimeOutput:
    """Wrap compact Seanime renderings in a structured tool envelope."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = {"text": value}
        return ok(message, SeanimeData(**parsed) if isinstance(parsed, dict) else _data(text=_render(parsed)))
    return ok(message, SeanimeData(**value) if isinstance(value, dict) else _data(text=_render(value)))


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


def _adult_mark(media: dict) -> str:
    """' [adult]' when the media is flagged 18+ — every rendering shows it so
    the model can warn or filter as the user asked."""
    return " [adult]" if media.get("isAdult") else ""


def _cover_url(media: dict) -> Optional[str]:
    cover = media.get("coverImage") or {}
    return cover.get("large") or cover.get("extraLarge") or cover.get("medium")


def _image_proxy_url(url: str | None) -> Optional[str]:
    if not url:
        return None
    return f"{config.seanime_base_url.rstrip('/')}/api/v1/image-proxy?url={quote(url, safe='')}"


def _cover_delivery_urls(media: dict) -> tuple[Optional[str], Optional[str]]:
    original = _cover_url(media)
    cover = _image_proxy_url(original)
    allow_media_url(cover)
    allow_media_url(original)
    return cover, original


# --- search filter normalization ------------------------------------------------
def _norm_enum(
    value: Optional[str], allowed: frozenset[str], label: str
) -> tuple[Optional[str], Optional[str]]:
    """Uppercase + validate one enum filter. Returns (normalized, error)."""
    if not value:
        return None, None
    norm = re.sub(r"[\s-]+", "_", value.strip().upper())
    if norm not in allowed:
        return None, f"Unknown {label} {value!r}; use one of: {', '.join(sorted(allowed))}."
    return norm, None


def _norm_genres(genres: Optional[list[str]]) -> tuple[list[str], Optional[str]]:
    """Map genre inputs to AniList's canonical names. Returns (genres, error)."""
    normalized: list[str] = []
    for raw in genres or []:
        key = str(raw).strip().lower()
        canonical = _GENRES.get(key) or _GENRE_ALIASES.get(key)
        if canonical is None:
            return [], (
                f"Unknown genre {raw!r}. AniList genres: {', '.join(sorted(_GENRES.values()))}. "
                "(For finer themes like 'isekai' use a plain search term instead.)"
            )
        if canonical not in normalized:
            normalized.append(canonical)
    return normalized, None


def _build_search_body(
    *,
    kind: str,
    search: str,
    page: int,
    per_page: int,
    genres: Optional[list[str]],
    season: Optional[str],
    year: Optional[int],
    media_format: Optional[str],
    status: Optional[str],
    sort: Optional[str],
    adult: str,
) -> tuple[Optional[dict], Optional[str]]:
    """Validate + assemble the AniList list request body (anime and manga share
    the shape; anime uses season/seasonYear, manga a plain year). Returns
    (body, error) — exactly one is set."""
    body: dict[str, Any] = {"page": int(page), "perPage": int(per_page)}
    # An empty search string makes AniList return nothing — omit the key so
    # pure filter browsing ("top rated 2024 TV anime") works.
    if search and search.strip():
        body["search"] = search.strip()

    genre_list, err = _norm_genres(genres)
    if err:
        return None, err
    if genre_list:
        body["genres"] = genre_list

    formats = _ANIME_FORMATS if kind == "anime" else _MANGA_FORMATS
    for value, allowed, label, key in (
        (media_format, formats, f"{kind} format", "format"),
        (status, _STATUSES, "status", "status"),
        (sort, _SORTS, "sort", "sort"),
    ):
        norm, err = _norm_enum(value, allowed, label)
        if err:
            return None, err
        if norm:
            # status/sort ride as arrays on the API; format is scalar.
            body[key] = norm if key == "format" else [norm]

    if kind == "anime":
        norm, err = _norm_enum(season, _SEASONS, "season")
        if err:
            return None, err
        if norm:
            body["season"] = norm
        if year:
            body["seasonYear"] = int(year)
    elif year:
        body["year"] = int(year)

    mode = (adult or "exclude").strip().lower()
    if mode not in ("exclude", "include", "only"):
        return None, 'Unknown adult mode; use "exclude" (default), "include", or "only".'
    if "Hentai" in genre_list and mode == "exclude":
        return None, (
            'The Hentai genre is adult-only and excluded by default; '
            'call again with adult="only".'
        )
    if mode == "exclude":
        # Explicit false = non-adult only.
        body["isAdult"] = False
    elif mode == "only":
        # true = adult-ONLY (subject to the server's enableAdultContent setting).
        body["isAdult"] = True
    # "include": OMIT the key — AniList then returns both, and we sidestep the
    # server-side EnableAdultContent coercion that `isAdult: true` is subject to.
    return body, None


# --- collection rendering (shared by anime + manga: same lists/entries shape) --
def _entry_records(data: dict, unit: str) -> list[dict]:
    """Flatten a collection into one record per entry, with the fields the
    compact and overview renderings group on. `unit` is "episodes"/"chapters"
    (which media field counts as the total)."""
    records = []
    for lst in data.get("lists") or []:
        status = lst.get("type") or lst.get("status") or "UNKNOWN"
        for e in lst.get("entries") or []:
            media = e.get("media") or {}
            list_data = e.get("listData") or {}
            records.append(
                {
                    "id": e.get("mediaId"),
                    "title": _title(media) + _adult_mark(media),
                    "status": status,
                    "progress": list_data.get("progress") or 0,
                    "score": list_data.get("score") or 0,
                    "genres": media.get("genres") or [],
                    "format": media.get("format") or "UNKNOWN",
                    "year": media.get("seasonYear")
                    or (media.get("startDate") or {}).get("year"),
                    "total": media.get(unit),
                }
            )
    return records


def _compact_collection(data: dict, unit: str) -> str:
    """Selected library fields as compact JSON; raw collection JSON is megabytes."""
    stats = data.get("stats") or {}
    lists: list[dict[str, Any]] = []
    for lst in data.get("lists") or []:
        entries = lst.get("entries") or []
        if not entries:
            continue
        compact_entries: list[dict[str, Any]] = []
        for e in entries:
            media = e.get("media") or {}
            list_data = e.get("listData") or {}
            compact_entries.append(
                {
                    "id": e.get("mediaId"),
                    "title": _title(media),
                    "adult": bool(media.get("isAdult")) or None,
                    "progress": list_data.get("progress", 0),
                    "total": media.get(unit),
                    "unit": unit,
                    "score": list_data.get("score") or None,
                }
            )
        lists.append(
            {
                "status": lst.get("type") or lst.get("status") or "UNKNOWN",
                "count": len(entries),
                "entries": compact_entries,
            }
        )
    unmatched = data.get("unmatchedGroups") or []
    return _json_result(
        type="library",
        stats={
            "entries": stats.get("totalEntries"),
            "files": stats.get("totalFiles"),
            "size": stats.get("totalSize"),
        }
        if stats
        else None,
        lists=lists,
        unmatched_groups=len(unmatched) if unmatched else None,
    )


def _group_keys(record: dict, group_by: str) -> list[str]:
    if group_by == "genre":
        return record["genres"] or ["(no genre)"]
    if group_by == "year":
        return [str(record["year"]) if record["year"] else "(unknown year)"]
    if group_by == "score":
        return [f"score {record['score']}" if record["score"] else "unscored"]
    return [str(record.get(group_by) or "UNKNOWN")]


def _overview(records: list[dict], group_by: str, unit: str) -> str:
    """Deterministic grouping + summary over flattened entries."""
    if not records:
        return "(library is empty)"
    watched = sum(r["progress"] for r in records)
    scored = [r["score"] for r in records if r["score"]]
    summary = {
        "entries": len(records),
        "progress": watched,
        "unit": unit,
        "mean_score": round(sum(scored) / len(scored), 1) if scored else None,
        "scored_entries": len(scored) if scored else 0,
    }

    groups: dict[str, list[str]] = defaultdict(list)
    for r in records:
        for key in _group_keys(r, group_by):
            groups[key].append(r["title"])

    if group_by in ("year", "score"):
        # Numeric descending (2026 → older, score 10 → 1), unknown/unscored last.
        def order(kv: tuple[str, list[str]]) -> tuple[int, int]:
            digits = "".join(ch for ch in kv[0] if ch.isdigit())
            return (0, -int(digits)) if digits else (1, 0)

        ordered = sorted(groups.items(), key=order)
    else:
        ordered = sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True)

    result_groups: list[dict[str, Any]] = []
    for key, titles in ordered:
        shown = titles[:_MAX_TITLES_PER_GROUP]
        more = len(titles) - len(shown)
        result_groups.append(
            {
                "key": key,
                "count": len(titles),
                "titles": shown,
                "omitted": more if more > 0 else None,
            }
        )
    return _json_result(type="library_overview", group_by=group_by, summary=summary, groups=result_groups)


# --- entry / episode rendering ------------------------------------------------
def _compact_episode_line(ep: dict) -> str:
    meta = ep.get("episodeMetadata") or {}
    bits = []
    if meta.get("airDate"):
        bits.append(f"aired {meta['airDate']}")
    if meta.get("isFiller"):
        bits.append("filler")
    bits.append("downloaded" if ep.get("isDownloaded") else "not downloaded")
    title = ep.get("episodeTitle") or ep.get("displayTitle") or ""
    return f"- Ep {ep.get('episodeNumber', '?')}: {title} ({', '.join(bits)})"


def _compact_anime_entry(data: dict) -> str:
    media = data.get("media") or {}
    list_data = data.get("listData") or {}
    lib = data.get("libraryData") or {}
    lines = [
        f"# {_title(media)}{_adult_mark(media)} (id {data.get('mediaId')}, "
        f"{media.get('format') or '?'}, {media.get('seasonYear') or '?'}, "
        f"{media.get('status') or '?'})"
    ]
    if media.get("genres"):
        lines.append("Genres: " + ", ".join(media["genres"]))
    cover, original_cover = _cover_delivery_urls(media)
    if cover:
        lines.append(f"Cover: {cover}")
        lines.append(f"Original cover fallback: {original_cover}")
    score_part = f", score {list_data['score']}" if list_data.get("score") else ""
    lines.append(
        f"AniList: {list_data.get('status') or 'not on list'}, progress "
        f"{list_data.get('progress', 0)}/{media.get('episodes') or '?'}{score_part}"
    )
    if lib:
        lines.append(
            f"Library: {lib.get('mainFileCount', 0)} main file(s), "
            f"{lib.get('unwatchedCount', 0)} unwatched, folder {lib.get('sharedPath', '?')}"
        )
    next_ep = data.get("nextEpisode") or {}
    if next_ep:
        lines.append(f"Next to watch: Ep {next_ep.get('episodeNumber', '?')}")
    to_download = (data.get("downloadInfo") or {}).get("episodesToDownload") or []
    if to_download:
        lines.append(f"{len(to_download)} aired episode(s) not downloaded yet.")

    episodes = data.get("episodes") or []
    if episodes:
        episodes = sorted(episodes, key=lambda e: e.get("episodeNumber") or 0)
        shown = episodes[-_MAX_EPISODE_LINES:]
        skipped = len(episodes) - len(shown)
        header = f"\nFiles on disk ({len(episodes)} episode(s)"
        header += f", showing last {len(shown)}):" if skipped else "):"
        lines.append(header)
        for ep in shown:
            local = ep.get("localFile") or {}
            kind = ep.get("type")
            kind_part = f" [{kind}]" if kind and kind != "main" else ""
            lines.append(
                f"- Ep {ep.get('episodeNumber', '?')}{kind_part}: "
                f"{ep.get('episodeTitle') or ep.get('displayTitle') or ''} — "
                f"{local.get('name') or '(no file)'}"
            )
    return _clip("\n".join(lines))


def _compact_manga_entry(data: dict) -> str:
    media = data.get("media") or {}
    list_data = data.get("listData") or {}
    lines = [
        f"# {_title(media)}{_adult_mark(media)} (id {data.get('mediaId')}, "
        f"{media.get('format') or '?'}, {media.get('status') or '?'})"
    ]
    if media.get("genres"):
        lines.append("Genres: " + ", ".join(media["genres"]))
    cover, original_cover = _cover_delivery_urls(media)
    if cover:
        lines.append(f"Cover: {cover}")
        lines.append(f"Original cover fallback: {original_cover}")
    totals = []
    if media.get("chapters"):
        totals.append(f"{media['chapters']} chapters")
    if media.get("volumes"):
        totals.append(f"{media['volumes']} volumes")
    if totals:
        lines.append("Published: " + ", ".join(totals))
    score_part = f", score {list_data['score']}" if list_data.get("score") else ""
    lines.append(
        f"AniList: {list_data.get('status') or 'not on list'}, progress "
        f"{list_data.get('progress', 0)}/{media.get('chapters') or '?'} chapters{score_part}"
    )
    return _clip("\n".join(lines))


_HTML_BREAK_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str, limit: int = _DESC_MAX_CHARS) -> str:
    """AniList descriptions arrive as HTML; the model wants prose."""
    text = _HTML_BREAK_RE.sub("\n", text)
    text = unescape(_HTML_TAG_RE.sub("", text)).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + " …"
    return text


def _compact_details(data: dict, media_id: int, kind: str) -> str:
    """AniList details, noise-trimmed: the raw payload buries the useful facts
    under full nested media objects for every relation/recommendation, complete
    tag descriptions, character/staff edges, etc."""
    lines = [f"# AniList details for {kind} {media_id}{_adult_mark(data)}"]
    if data.get("description"):
        lines.append(_strip_html(data["description"]))
    if data.get("genres"):
        lines.append("Genres: " + ", ".join(data["genres"]))

    facts = []
    score = data.get("averageScore") or data.get("meanScore")
    if score:
        facts.append(f"score {score}/100")
    if data.get("popularity"):
        facts.append(f"popularity {data['popularity']}")
    start_year = (data.get("startDate") or {}).get("year")
    if start_year:
        facts.append(f"started {start_year}")
    if data.get("duration"):
        facts.append(f"{data['duration']} min/ep")
    if facts:
        lines.append("Facts: " + ", ".join(facts))

    studios = [s.get("name") for s in ((data.get("studios") or {}).get("nodes") or []) if s.get("name")]
    if studios:
        lines.append("Studios: " + ", ".join(studios))
    trailer = data.get("trailer") or {}
    if trailer.get("site") == "youtube" and trailer.get("id"):
        lines.append(f"Trailer: https://youtu.be/{trailer['id']}")
    for ranking in (data.get("rankings") or [])[:2]:
        if ranking.get("context"):
            lines.append(f"Ranked #{ranking.get('rank', '?')} {ranking['context']}")

    tags = [
        t["name"] + (" [adult]" if t.get("isAdult") else "")
        for t in sorted(data.get("tags") or [], key=lambda t: -(t.get("rank") or 0))
        if t.get("name") and not t.get("isMediaSpoiler") and not t.get("isGeneralSpoiler")
    ]
    if tags:
        lines.append("Tags: " + ", ".join(tags[:_TAG_LIMIT]))

    relations = (data.get("relations") or {}).get("edges") or []
    if relations:
        lines.append("\nRelations:")
        for edge in relations[:_RELATION_LIMIT]:
            node = edge.get("node") or {}
            lines.append(
                f"- {edge.get('relationType', '?')}: {_title(node)}{_adult_mark(node)} "
                f"({node.get('format') or node.get('type') or '?'}, id {node.get('id', '?')})"
            )
        if len(relations) > _RELATION_LIMIT:
            lines.append(f"  … +{len(relations) - _RELATION_LIMIT} more")

    recs = []
    for edge in ((data.get("recommendations") or {}).get("edges") or []):
        rec = (edge.get("node") or {}).get("mediaRecommendation") or {}
        if rec.get("id"):
            score_part = f", score {rec['meanScore']}" if rec.get("meanScore") else ""
            recs.append(f"- {_title(rec)}{_adult_mark(rec)} (id {rec['id']}{score_part})")
        if len(recs) >= _RECOMMENDATION_LIMIT:
            break
    if recs:
        lines.append("\nRecommended:")
        lines.extend(recs)

    if data.get("siteUrl"):
        lines.append(f"\nAniList page: {data['siteUrl']}")
    return _clip("\n".join(lines))


def _compact_status(data: dict) -> str:
    """Server status, trimmed to what conversations actually need — the raw
    payload is ~7k chars of theme/torrent/mediastream settings."""
    user = data.get("user")
    viewer = (user.get("viewer") if isinstance(user, dict) else None) or {}
    settings = data.get("settings")
    anilist_settings = (settings.get("anilist") if isinstance(settings, dict) else None) or {}
    version = data.get("version", "?")
    if data.get("versionName"):
        version += f" ({data['versionName']})"
    lines = [
        f"Seanime {version} on {data.get('os', '?')}",
        f"AniList user: {viewer.get('name') or '(not logged in)'}",
        "Adult content: "
        + ("enabled" if anilist_settings.get("enableAdultContent") else "disabled (server setting)")
        + (", profile shows it" if (viewer.get("options") or {}).get("displayAdultContent") else ""),
        f"Server ready: {bool(data.get('serverReady'))}, offline mode: {bool(data.get('isOffline'))}, "
        f"password-protected: {bool(data.get('serverHasPassword'))}",
    ]
    if data.get("dataDir"):
        lines.append(f"Data dir: {data['dataDir']}")
    return "\n".join(lines)


def _compact_missing(data: dict) -> str:
    """Missing episodes grouped per anime — the raw payload repeats the full
    media object and episode metadata (~19k chars) per missing episode."""
    episodes = data.get("episodes") or []
    silenced = data.get("silencedEpisodes") or []
    if not episodes and not silenced:
        return "(no missing episodes — the library is up to date)"
    by_anime: dict[tuple, list[str]] = defaultdict(list)
    for ep in episodes:
        anime = ep.get("baseAnime") or {}
        key = (anime.get("id"), _title(anime) + _adult_mark(anime))
        air = (ep.get("episodeMetadata") or {}).get("airDate")
        by_anime[key].append(
            f"Ep {ep.get('episodeNumber', '?')}" + (f" (aired {air})" if air else "")
        )
    lines = [f"{len(episodes)} missing episode(s):"]
    for (media_id, title), eps in by_anime.items():
        lines.append(f"- {title} (id {media_id}): {', '.join(eps)}")
    if silenced:
        lines.append(f"({len(silenced)} silenced episode(s) not shown)")
    return _clip("\n".join(lines))


def _compact_schedule(data: list) -> str:
    """One line per upcoming airing, soonest first. Seanime returns the whole
    schedule including long-past airing times — those are counted, not listed."""
    from datetime import UTC, datetime

    items = [i for i in data if isinstance(i, dict)]
    if not items:
        return "(nothing on the airing schedule)"
    items.sort(key=lambda i: i.get("dateTime") or "")
    # ISO-8601 Z timestamps compare correctly as strings.
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    upcoming = [i for i in items if (i.get("dateTime") or "") >= now]
    past = len(items) - len(upcoming)
    if not upcoming:
        return f"(no upcoming episodes on the schedule; {past} already-aired item(s))"
    lines = [f"{len(upcoming)} upcoming episode(s)" + (f" ({past} past not listed):" if past else ":")]
    for item in upcoming:
        flags = []
        if item.get("isMovie"):
            flags.append("movie")
        if item.get("isSeasonFinale"):
            flags.append("season finale")
        flag_part = f" [{', '.join(flags)}]" if flags else ""
        when = item.get("dateTime") or item.get("time") or "?"
        lines.append(
            f"- {when}: {item.get('title', '?')} — Ep {item.get('episodeNumber', '?')} "
            f"(id {item.get('mediaId', '?')}){flag_part}"
        )
    return _clip("\n".join(lines))


def _compact_continuity(data: Any) -> Optional[str]:
    """Watch history as one line per item; None when the shape is unexpected
    (caller falls back to the raw render)."""
    if isinstance(data, dict):
        items = list(data.values())
    elif isinstance(data, list):
        items = data
    else:
        return None
    items = [i for i in items if isinstance(i, dict)]
    if not items:
        return "(no watch history)"
    items.sort(key=lambda i: i.get("timeUpdated") or i.get("timeAdded") or "", reverse=True)
    lines = [f"{len(items)} watch-history item(s), most recent first:"]
    for item in items:
        position = ""
        current, duration = item.get("currentTime"), item.get("duration")
        if isinstance(current, (int, float)) and isinstance(duration, (int, float)) and duration:
            position = f", stopped at {current / duration:.0%}"
        when = item.get("timeUpdated") or item.get("timeAdded")
        when_part = f" ({when})" if when else ""
        lines.append(
            f"- media {item.get('mediaId', '?')}: episode {item.get('episodeNumber', '?')}"
            f"{position}{when_part}"
        )
    return _clip("\n".join(lines))


def _compact_search_results(result: Any, kind: str) -> Optional[str]:
    """Strip an AniList page envelope down to a compact media list, or None when
    the shape is unexpected (caller falls back to raw render)."""
    if not isinstance(result, dict):
        return None
    media = (result.get("Page") or {}).get("media")
    if media is None:
        media = result.get("media")
    if not isinstance(media, list):
        return None
    compact = []
    for m in media:
        if not isinstance(m, dict):
            continue
        entry = {
            "id": m.get("id"),
            "title": _title(m),
            "format": m.get("format"),
            "status": m.get("status"),
        }
        if kind == "anime":
            entry["episodes"] = m.get("episodes")
            entry["season"] = f"{m.get('season', '')} {m.get('seasonYear', '')}".strip()
        else:
            entry["chapters"] = m.get("chapters")
            entry["volumes"] = m.get("volumes")
            year = (m.get("startDate") or {}).get("year")
            if year:
                entry["year"] = year
        if m.get("genres"):
            entry["genres"] = m["genres"][:3]
        # Proxied cover URL so the lead can deliver the actual image through
        # Seanime first; keep the source as a fallback for failed proxy fetches.
        cover, original_cover = _cover_delivery_urls(m)
        if cover:
            entry["cover"] = cover
            entry["cover_original"] = original_cover
        # Flag adult titles so the reply can say so; omit when not.
        if m.get("isAdult"):
            entry["isAdult"] = True
        compact.append(entry)
    if not compact:
        return "(no results — try different filters, another spelling, or adult=\"include\")"
    return _render(compact)


# --- tools: server ------------------------------------------------------------
@tool(
    description="Get Seanime server status, readiness, version, login, and adult-content state.",
    instructions=(
        "Use when asked whether Seanime is up, who is logged in, or before debugging a failed Seanime call. "
        "Takes no arguments."
    ),
    show_result=True,
)
async def seanime_status() -> SeanimeOutput:
    """Get the Seanime server status: version, logged-in AniList user, whether
    adult content is enabled, server readiness. Call this first when the user
    asks whether Seanime is up, who is logged in, or before debugging any other
    Seanime call that failed."""
    result = await _call("GET", "/api/v1/status")
    if _is_error(result):
        return fail(result)
    if isinstance(result, dict):
        return _tool_result("Seanime status.", _compact_status(result))
    return _tool_result("Seanime status.", _render(result))


# --- tools: library (the user's own anime + manga lists) ------------------------
_COLLECTION_PATHS: Final[dict[str, tuple[str, str]]] = {
    "anime": ("/api/v1/library/collection", "episodes"),
    "manga": ("/api/v1/manga/collection", "chapters"),
}
LibraryKind = Literal["anime", "manga"]
LibraryGroupBy = Literal["none", "status", "genre", "format", "year", "score"]
MediaKind = Literal["anime", "manga"]
FindKind = Literal["all", "anime", "manga"]
AdultMode = Literal["exclude", "include", "only"]
AnimeSeason = Literal["WINTER", "SPRING", "SUMMER", "FALL"]
AnimeFormat = Literal["TV", "TV_SHORT", "MOVIE", "SPECIAL", "OVA", "ONA", "MUSIC"]
MangaFormat = Literal["MANGA", "NOVEL", "ONE_SHOT"]
# Union the kind-specific formats for the one browse tool's schema; `_build_search_body`
# validates the value against the right vocabulary for the chosen kind.
AnyFormat = Literal[
    "TV", "TV_SHORT", "MOVIE", "SPECIAL", "OVA", "ONA", "MUSIC",
    "MANGA", "NOVEL", "ONE_SHOT",
]
MediaStatus = Literal["FINISHED", "RELEASING", "NOT_YET_RELEASED", "CANCELLED", "HIATUS"]
MediaSort = Literal[
    "SCORE_DESC",
    "SCORE",
    "POPULARITY_DESC",
    "POPULARITY",
    "TRENDING_DESC",
    "FAVOURITES_DESC",
    "START_DATE_DESC",
    "START_DATE",
    "END_DATE_DESC",
    "EPISODES_DESC",
    "CHAPTERS_DESC",
    "VOLUMES_DESC",
    "TITLE_ROMAJI",
    "TITLE_ENGLISH",
    "UPDATED_AT_DESC",
]
AniListGenre = Literal[
    "Action",
    "Adventure",
    "Comedy",
    "Drama",
    "Ecchi",
    "Fantasy",
    "Hentai",
    "Horror",
    "Mahou Shoujo",
    "Mecha",
    "Music",
    "Mystery",
    "Psychological",
    "Romance",
    "Sci-Fi",
    "Slice of Life",
    "Sports",
    "Supernatural",
    "Thriller",
]


@tool(
    description=(
        "List or summarize the user's own Seanime anime or manga library. "
        "Use for library/list questions and library statistics, not global AniList discovery."
    ),
    instructions=(
        'kind: "anime" or "manga"; default "anime". '
        'group_by: "none", "status", "genre", "format", "year", or "score"; default "none". '
        "Omit default arguments instead of sending null."
    ),
    show_result=True,
)
async def seanime_library(
    kind: Annotated[
        LibraryKind | None,
        Field(default="anime", description="Library type to read from Seanime."),
    ] = "anime",
    group_by: Annotated[
        LibraryGroupBy | None,
        Field(default="none", description="Optional grouping for library summaries."),
    ] = "none",
) -> SeanimeOutput:
    """The user's OWN library lists on the Seanime server — the source of truth
    for "what am I watching/reading", "what's on my list", and any library
    statistics. Never answer those questions from the global AniList catalog."""
    kind = (kind or "anime").strip().lower()
    if kind not in _COLLECTION_PATHS:
        return fail('Unknown kind; use "anime" or "manga".', _data(kind=kind))
    group_by = (group_by or "none").strip().lower()
    if group_by not in ("none", "status", "genre", "format", "year", "score"):
        return fail("Unknown group_by; use one of: none, status, genre, format, year, score.", _data(group_by=group_by))
    path, unit = _COLLECTION_PATHS[kind]
    result = await _call("GET", path)
    if _is_error(result):
        return fail(result)
    if not isinstance(result, dict):
        return _tool_result("Seanime library.", _render(result))
    if group_by == "none":
        return _tool_result("Seanime library.", _compact_collection(result, unit))
    return _tool_result("Seanime library overview.", _overview(_entry_records(result, unit), group_by, unit))


# --- tools: one title, the full picture ------------------------------------------
async def _media_info(media_id: int, kind: str) -> str:
    """Library state + AniList facts for one id, fetched and joined here so the
    use-case "tell me about X" costs one tool call."""
    if kind == "anime":
        entry_raw = await _call("GET", f"/api/v1/library/anime-entry/{media_id}")
        details_raw = await _call("GET", f"/api/v1/anilist/media-details/{media_id}")
        entry = (
            entry_raw
            if _is_error(entry_raw)
            else _compact_anime_entry(entry_raw)
            if isinstance(entry_raw, dict)
            else _render(entry_raw)
        )
    else:
        entry_raw = await _call("GET", f"/api/v1/manga/entry/{media_id}")
        details_raw = await _call("GET", f"/api/v1/manga/entry/{media_id}/details")
        entry = (
            entry_raw
            if _is_error(entry_raw)
            else _compact_manga_entry(entry_raw)
            if isinstance(entry_raw, dict)
            else _render(entry_raw)
        )
    details = (
        details_raw
        if _is_error(details_raw)
        else _compact_details(details_raw, media_id, kind)
        if isinstance(details_raw, dict)
        else _render(details_raw)
    )
    return _clip(f"{entry}\n\n{details}")


@tool(
    description="Return the full user-library and AniList picture for one anime or manga by AniList media id.",
    instructions=(
        "Get media_id from seanime_find or seanime_library; never guess ids. "
        'kind must be "anime" or "manga" and must match the id.'
    ),
    show_result=True,
)
async def seanime_media_info(
    media_id: Annotated[
        int,
        Field(gt=0, description="AniList media id from seanime_find or seanime_library."),
    ],
    kind: Annotated[
        MediaKind | None,
        Field(default="anime", description="Whether the media id is anime or manga."),
    ] = "anime",
) -> SeanimeOutput:
    """Everything about ONE title, by AniList media id, in one call: the user's
    library/list state (list status, progress, score; for anime also the files
    on disk, next episode to watch, and undownloaded episodes) plus AniList
    facts (description, genres, score, studios, relations, recommendations,
    cover URL). Get the id from `seanime_find` or `seanime_library` — never
    guess it. `kind` must match what the id is: "anime" (default) or "manga"."""
    kind = (kind or "anime").strip().lower()
    if kind not in ("anime", "manga"):
        return fail('Unknown kind; use "anime" or "manga".', _data(kind=kind))
    return _tool_result("Seanime media info.", await _media_info(int(media_id), kind))


@tool(
    description="List all main episodes for one anime by AniList media id, including airing and download state.",
    instructions=(
        "Use for episode counts, filler questions, air dates, or downloaded-vs-missing checks for one show. "
        "Get media_id from seanime_find or seanime_library."
    ),
    show_result=True,
)
async def seanime_episode_collection(
    media_id: Annotated[
        int,
        Field(gt=0, description="AniList anime media id from seanime_find or seanime_library."),
    ],
) -> SeanimeOutput:
    """The full main-episode list for an anime by AniList media id: episode
    number, title, air date, filler flag, and whether it's downloaded. Use for
    "how many episodes does X have", "which episodes are filler", "when did
    episode N air", or to see what's downloaded vs missing for one show."""
    result = await _call("GET", f"/api/v1/anime/episode-collection/{int(media_id)}")
    if _is_error(result):
        return fail(result)
    if isinstance(result, dict):
        episodes = result.get("episodes") or []
        if not episodes:
            return ok("No episodes found for this media id.", _data(media_id=media_id, episodes=[]))
        episodes = sorted(episodes, key=lambda e: e.get("episodeNumber") or 0)
        lines = [f"{len(episodes)} main episode(s):"]
        lines += [_compact_episode_line(ep) for ep in episodes]
        if result.get("hasMappingError"):
            lines.append("(warning: metadata mapping error — episode data may be incomplete)")
        return ok("Seanime episode collection.", _data(media_id=media_id, text=_clip("\n".join(lines)), episodes=episodes))
    return _tool_result("Seanime episode collection.", _render(result))


@tool(
    description="List aired episodes missing from the user's local anime library.",
    instructions="Use for questions like what episodes are missing or what can be downloaded. Takes no arguments.",
    show_result=True,
)
async def seanime_missing_episodes() -> SeanimeOutput:
    """Episodes that have aired but are not in the local library yet, per anime
    the user is watching. Use for "what am I missing" / "what can I download"."""
    result = await _call("GET", "/api/v1/library/missing-episodes")
    if _is_error(result):
        return fail(result)
    if isinstance(result, dict):
        return ok("Seanime missing episodes.", _data(text=_compact_missing(result)))
    return _tool_result("Seanime missing episodes.", _render(result))


@tool(
    description="Return the airing schedule for anime in the user's Seanime collection.",
    instructions="Use for what airs today, this week, next, or upcoming collection episodes. Takes no arguments.",
    show_result=True,
)
async def seanime_upcoming_schedule() -> SeanimeOutput:
    """The airing schedule for anime in the user's collection: which episodes
    air on which date. Use for "what airs this week / today / next"."""
    result = await _call("GET", "/api/v1/library/schedule")
    if _is_error(result):
        return fail(result)
    if isinstance(result, list):
        return ok("Seanime upcoming schedule.", _data(text=_compact_schedule(result), items=result))
    return _tool_result("Seanime upcoming schedule.", _render(result))


@tool(
    description="Return the user's recent Seanime watch continuity/history.",
    instructions="Use when asked what the user was watching recently or where playback stopped. Takes no arguments.",
    show_result=True,
)
async def seanime_continuity_history() -> SeanimeOutput:
    """The user's recent watch history (continuity): what was watched last and
    where playback stopped. Use for "what was I watching" / "where did I leave off"."""
    result = await _call("GET", "/api/v1/continuity/history")
    if _is_error(result):
        return fail(result)
    text = _compact_continuity(result)
    return ok("Seanime continuity history.", _data(text=text, raw=result)) if text else _tool_result("Seanime continuity history.", _render(result))


# --- tools: resolve a named title (the find workflow) ----------------------------
def _title_matches(media: dict, term_words: list[str]) -> bool:
    """True when every word of the search term appears in any title variant or
    synonym (case-insensitive) — catches "frieren" against "Sousou no Frieren"
    and reordered words like "journey end"."""
    titles = media.get("title") or {}
    candidates: list[str] = [
        titles.get(key) or "" for key in ("userPreferred", "english", "romaji", "native")
    ]
    candidates += [s for s in media.get("synonyms") or [] if isinstance(s, str)]
    for candidate in candidates:
        folded = candidate.casefold()
        if folded and all(word in folded for word in term_words):
            return True
    return False


def _local_matches(data: dict, term_words: list[str], kind: str, unit: str) -> list[dict]:
    """Matching library entries as (kind, id, line) records for `seanime_find`.
    The line carries list status, progress, score, and cover URL so a match
    list is already answer-ready."""
    matches: list[dict] = []
    for lst in data.get("lists") or []:
        status = lst.get("type") or lst.get("status") or "UNKNOWN"
        for e in lst.get("entries") or []:
            media = e.get("media") or {}
            if not _title_matches(media, term_words):
                continue
            list_data = e.get("listData") or {}
            progress = list_data.get("progress") or 0
            total = media.get(unit) or "?"
            score = list_data.get("score")
            score_part = f", score {score}" if score else ""
            cover, original_cover = _cover_delivery_urls(media)
            cover_part = (
                f", cover {cover}, original cover {original_cover}" if cover else ""
            )
            matches.append(
                {
                    "kind": kind,
                    "id": e.get("mediaId"),
                    "line": (
                        f"- [{kind}] {_title(media)}{_adult_mark(media)} "
                        f"(id {e.get('mediaId')}, {status}): "
                        f"{progress}/{total}{score_part}{cover_part}"
                    ),
                }
            )
    return matches


_BROWSE_PATHS: Final[dict[str, str]] = {
    "anime": "/api/v1/anilist/list-anime",
    "manga": "/api/v1/manga/anilist/list",
}


@tool(
    description="Resolve a named anime or manga against the user's library first, then global AniList matches.",
    instructions=(
        "Always use first when the user names a specific title. Results state whether matches are in the user's library; "
        'kind is "all", "anime", or "manga".'
    ),
    show_result=True,
)
async def seanime_find(
    title: Annotated[
        str,
        Field(min_length=1, description="Anime or manga title as the user named it."),
    ],
    kind: Annotated[
        FindKind | None,
        Field(default="all", description="Search anime, manga, or both."),
    ] = "all",
) -> SeanimeOutput:
    """Resolve a title the user named — ALWAYS your first call when the user
    mentions a specific show or manga. One call runs the whole lookup: the
    user's own Seanime library is searched first, and the result states
    explicitly whether the title is in their library or not.

    What comes back:
      - Exactly one library match → its full picture immediately (list status,
        progress, files on disk, AniList facts, cover URL) — no follow-up call
        needed.
      - Several library matches → one line each with media id; call
        `seanime_media_info(media_id, kind)` for the one the user means.
      - No library match → top matches from the global AniList catalog,
        labeled NOT in the user's library — relay that label; never present
        them as something the user owns. Adult titles are included here and
        flagged [adult].

    Arguments:
      - title: the name as the user said it, e.g. "frieren". Title variants,
        synonyms, and word order are matched automatically.
      - kind: "all" (default — both), "anime", or "manga"."""
    normalized = (title or "").strip().casefold()
    if not normalized:
        return fail("Provide a non-empty title.", _data(title=title))
    kind = (kind or "all").strip().lower()
    if kind not in ("all", "anime", "manga"):
        return fail('Unknown kind; use "all" (default), "anime", or "manga".', _data(kind=kind))
    words = normalized.split()
    kinds: tuple[str, ...] = ("anime", "manga") if kind == "all" else (kind,)

    matches: list[dict] = []
    errors: list[str] = []
    for k in kinds:
        path, unit = _COLLECTION_PATHS[k]
        result = await _call("GET", path)
        if _is_error(result):
            errors.append(result)
        elif isinstance(result, dict):
            matches += _local_matches(result, words, k, unit)
    if errors and len(errors) == len(kinds):
        return fail(errors[0])

    if len(matches) == 1:
        match = matches[0]
        info = await _media_info(int(match["id"]), str(match["kind"]))
        return _tool_result(
            "Found title in the user's library.",
            _clip(f"Found in the user's library ({match['kind']}):\n\n{info}"),
        )
    if matches:
        lines = [f"{len(matches)} matches in the user's library for {title!r}:"]
        lines += [str(m["line"]) for m in matches]
        lines.append("Call seanime_media_info(media_id, kind) for the one the user means.")
        return ok("Found multiple title matches in the user's library.", _data(text=_clip("\n".join(lines)), matches=matches))

    # Nothing local — fall back to the global AniList catalog. isAdult is
    # omitted on purpose: the user named this title, so an adult title must be
    # findable too; results carry the isAdult flag for the reply.
    sections: list[str] = []
    for k in kinds:
        result = await _call(
            "POST", _BROWSE_PATHS[k], {"search": title.strip(), "page": 1, "perPage": 5}
        )
        if _is_error(result):
            errors.append(result)
            continue
        compact = _compact_search_results(result, k)
        if compact and not compact.startswith("(no results"):
            sections.append(f"## {k}\n{compact}")
    if not sections:
        if errors:
            return fail(errors[0])
        return fail(
            f"No match for {title!r}: not in the user's library, and no AniList "
            "catalog result either. Check the spelling with the user.",
            _data(title=title, kind=kind),
        )
    return ok(
        "Found global AniList catalog matches outside the user's library.",
        _data(
            text=_clip(
                f"NOT in the user's library. Global AniList catalog matches for {title!r} "
                "(relay that these are not the user's own):\n\n" + "\n\n".join(sections)
            ),
            title=title,
            kind=kind,
        ),
    )


# --- tools: discover in the global AniList catalog -------------------------------
@tool(
    description="Browse/discover the global AniList catalog (anime or manga) with search and filters.",
    instructions=(
        "Use for discovery and recommendations, not the user's own library or a specific named title. "
        'kind is "anime" or "manga"; results are global catalog items; adult is "exclude", "include", or "only".'
    ),
    show_result=True,
)
async def seanime_browse(
    kind: Annotated[
        MediaKind | None,
        Field(default="anime", description='Catalog to browse: "anime" or "manga".'),
    ] = "anime",
    search: Annotated[
        str,
        Field(default="", description="Optional keyword search term; leave empty for filter-only browsing."),
    ] = "",
    page: Annotated[int, Field(default=1, ge=1, description="AniList result page number.")] = 1,
    per_page: Annotated[
        int,
        Field(default=10, ge=1, le=25, description="Number of results to return."),
    ] = 10,
    genres: Annotated[
        list[AniListGenre] | None,
        Field(default=None, description="Optional AniList genres to filter by."),
    ] = None,
    season: Annotated[
        AnimeSeason | None,
        Field(default=None, description="Optional airing season (anime only; ignored for manga)."),
    ] = None,
    year: Annotated[
        int | None,
        Field(default=None, ge=1900, le=2100, description="Optional year: airing season (anime) or publication start (manga)."),
    ] = None,
    format: Annotated[
        AnyFormat | None,
        Field(
            default=None,
            description="Optional format. Anime: TV/TV_SHORT/MOVIE/SPECIAL/OVA/ONA/MUSIC. Manga: MANGA/NOVEL/ONE_SHOT.",
        ),
    ] = None,
    status: Annotated[
        MediaStatus | None,
        Field(default=None, description="Optional release/publication status."),
    ] = None,
    sort: Annotated[
        MediaSort | None,
        Field(default=None, description="Optional AniList sort mode."),
    ] = None,
    adult: Annotated[
        AdultMode | None,
        Field(default="exclude", description="Adult-content filtering mode."),
    ] = "exclude",
) -> SeanimeOutput:
    """DISCOVER anime or manga in the global AniList catalog by filters — "top
    rated 2024 TV anime", "romance from winter 2024", "finished novels from
    2024", trending, seasonal browsing. Results are recommendations from the
    whole catalog, NOT the user's library — say so when relaying them. To
    resolve a specific title the user named, use `seanime_find` instead, never
    this. Returns media with ids, formats, airing/publishing status, episode or
    chapter/volume counts, genres, and cover image URLs.

    Arguments:
      - kind: "anime" (default) or "manga".
      - search: optional keyword for themes AniList has no genre for (e.g.
        "isekai"). Leave empty when filters alone describe the request.
    Filters (combine freely; every one is honored by the API):
      - genres: AniList genre names, e.g. ["Romance", "Comedy"].
      - season: WINTER | SPRING | SUMMER | FALL (anime only, with `year`).
      - year: airing season year (anime) or publication start year (manga).
      - format: anime TV | TV_SHORT | MOVIE | SPECIAL | OVA | ONA | MUSIC;
        manga MANGA | NOVEL | ONE_SHOT (must match `kind`).
      - status: FINISHED | RELEASING | NOT_YET_RELEASED | CANCELLED | HIATUS.
      - sort: e.g. SCORE_DESC, POPULARITY_DESC, TRENDING_DESC, START_DATE_DESC.
      - adult: "exclude" (default — 18+ titles won't appear), "include" (both),
        or "only" (18+ only). Use "only" exactly when the user explicitly asks
        for adult content."""
    kind = (kind or "anime").strip().lower()
    if kind not in ("anime", "manga"):
        return fail('Unknown kind; use "anime" or "manga".', _data(kind=kind))
    body, error = _build_search_body(
        kind=kind, search=search, page=page, per_page=per_page, genres=genres,
        season=season if kind == "anime" else None, year=year,
        media_format=format, status=status, sort=sort, adult=adult,
    )
    if error:
        return fail(error)
    result = await _call("POST", _BROWSE_PATHS[kind], body)
    if _is_error(result):
        return fail(result)
    return _tool_result(f"Seanime {kind} browse results.", _compact_search_results(result, kind) or _render(result))


@tool(
    description="Update the user's AniList anime episode progress through Seanime.",
    instructions=(
        "This changes external user data. Use only when the user explicitly asks to mark/update progress, "
        "and do not guess media_id or episode_number."
    ),
    show_result=True,
)
async def seanime_update_progress(
    media_id: Annotated[
        int,
        Field(gt=0, description="AniList anime media id to update."),
    ],
    episode_number: Annotated[
        int,
        Field(ge=0, description="Episode progress value to set."),
    ],
    total_episodes: Annotated[
        int,
        Field(default=0, ge=0, description="Known total episode count, or 0 when unknown."),
    ] = 0,
) -> SeanimeOutput:
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
        return fail(result)
    return ok(
        f"Progress updated: media {media_id} marked at episode {episode_number}.",
        _data(media_id=media_id, episode_number=episode_number, total_episodes=total_episodes),
    )


@tool(
    description="Update the user's AniList manga chapter progress through Seanime.",
    instructions=(
        "This changes external user data. Use only when the user explicitly asks to mark/update progress, "
        "and do not guess media_id or chapter_number."
    ),
    show_result=True,
)
async def seanime_manga_update_progress(
    media_id: Annotated[
        int,
        Field(gt=0, description="AniList manga media id to update."),
    ],
    chapter_number: Annotated[
        int,
        Field(ge=0, description="Chapter progress value to set."),
    ],
    total_chapters: Annotated[
        int,
        Field(default=0, ge=0, description="Known total chapter count, or 0 when unknown."),
    ] = 0,
) -> SeanimeOutput:
    """Mark a manga chapter as read: updates the user's AniList progress for the
    manga to `chapter_number`. This CHANGES the user's AniList list — only call
    it when the user explicitly asks to mark/update progress, and confirm the
    chapter number from the conversation, never guess it."""
    body = {
        "mediaId": int(media_id),
        "chapterNumber": int(chapter_number),
        "totalChapters": int(total_chapters),
    }
    result = await _call("POST", "/api/v1/manga/update-progress", body)
    if _is_error(result):
        return fail(result)
    return ok(
        f"Progress updated: manga {media_id} marked at chapter {chapter_number}.",
        _data(media_id=media_id, chapter_number=chapter_number, total_chapters=total_chapters),
    )


# Use-case-shaped surface: one tool per conversational job, plus the two
# deliberate mutations (progress updates).
SEANIME_TOOLS: Final[list[Any]] = [
    seanime_status,
    seanime_library,
    seanime_find,
    seanime_media_info,
    seanime_browse,
    seanime_episode_collection,
    seanime_missing_episodes,
    seanime_upcoming_schedule,
    seanime_continuity_history,
    seanime_update_progress,
    seanime_manga_update_progress,
]
