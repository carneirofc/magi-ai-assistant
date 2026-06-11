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
"""

import json
import re
from collections import defaultdict
from html import unescape
from typing import Any, Final, Optional

import httpx
from agno.tools import tool
from agno.utils.log import log_info, log_warning

from core.config import config

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
            total = media.get(unit) or "?"
            score = list_data.get("score")
            score_part = f", score {score}" if score else ""
            lines.append(
                f"- {_title(media)}{_adult_mark(media)} (id {e.get('mediaId')}): "
                f"{progress}/{total}{score_part}"
            )
    unmatched = data.get("unmatchedGroups") or []
    if unmatched:
        lines.append(f"\n{len(unmatched)} unmatched group(s) — files not linked to any anime yet.")
    return _clip("\n".join(lines)) if lines else "(library is empty)"


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
    summary = f"{len(records)} entries, {watched} {unit} of progress"
    if scored:
        summary += f", mean score {sum(scored) / len(scored):.1f} over {len(scored)} scored"

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

    lines = [f"Grouped by {group_by} — {summary}"]
    for key, titles in ordered:
        shown = titles[:_MAX_TITLES_PER_GROUP]
        more = len(titles) - len(shown)
        suffix = f" … +{more} more" if more > 0 else ""
        lines.append(f"\n## {key} ({len(titles)})\n{', '.join(shown)}{suffix}")
    return _clip("\n".join(lines))


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
    if _cover_url(media):
        lines.append(f"Cover: {_cover_url(media)}")
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
    if _cover_url(media):
        lines.append(f"Cover: {_cover_url(media)}")
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
        # Cover URL so the lead can deliver the actual image on request
        # (send_media_from_url), not just describe it.
        if _cover_url(m):
            entry["cover"] = _cover_url(m)
        # Flag adult titles so the reply can say so; omit when not.
        if m.get("isAdult"):
            entry["isAdult"] = True
        compact.append(entry)
    if not compact:
        return "(no results — try different filters, another spelling, or adult=\"include\")"
    return _render(compact)


# --- tools: server ------------------------------------------------------------
@tool
async def seanime_status() -> str:
    """Get the Seanime server status: version, logged-in AniList user, whether
    adult content is enabled, server readiness. Call this first when the user
    asks whether Seanime is up, who is logged in, or before debugging any other
    Seanime call that failed."""
    result = await _call("GET", "/api/v1/status")
    if _is_error(result):
        return result
    if isinstance(result, dict):
        return _compact_status(result)
    return _render(result)


# --- tools: anime library -------------------------------------------------------
@tool
async def seanime_library_collection() -> str:
    """The user's local anime library, grouped by AniList list status (current,
    planning, completed, ...): one line per entry with title, AniList media id,
    watch progress, and score. Use it to answer "what am I watching", "what's in
    my library", or to find an anime's media id for the other tools. For
    grouping or statistics questions use `seanime_library_overview` instead."""
    result = await _call("GET", "/api/v1/library/collection")
    if _is_error(result):
        return result
    if isinstance(result, dict):
        return _compact_collection(result, "episodes")
    return _render(result)


@tool
async def seanime_library_overview(group_by: str = "status", kind: str = "anime") -> str:
    """Group and summarize the user's whole library in one call: overall totals
    (entries, episodes/chapters of progress, mean score) plus entry titles
    bucketed by the chosen dimension. Use for questions like "group my library
    by genre", "what do I watch per year", "how are my scores distributed",
    "summarize my manga list".

    Arguments:
      - group_by: "status" (watching/planning/...), "genre", "format"
        (TV/MOVIE/OVA/...), "year", or "score".
      - kind: "anime" (default) or "manga"."""
    group_by = (group_by or "status").strip().lower()
    if group_by not in ("status", "genre", "format", "year", "score"):
        return "Unknown group_by; use one of: status, genre, format, year, score."
    if kind not in ("anime", "manga"):
        return 'Unknown kind; use "anime" or "manga".'
    path = "/api/v1/library/collection" if kind == "anime" else "/api/v1/manga/collection"
    unit = "episodes" if kind == "anime" else "chapters"
    result = await _call("GET", path)
    if _is_error(result):
        return result
    if isinstance(result, dict):
        return _overview(_entry_records(result, unit), group_by, unit)
    return _render(result)


@tool
async def seanime_anime_entry(media_id: int) -> str:
    """Library details for one anime by AniList media id: AniList progress and
    score, file counts and the library folder, the next episode to watch,
    episodes not downloaded yet, and the actual files on disk per episode
    (filename per episode). Get the id from `seanime_library_collection` or
    `seanime_search_anime`."""
    result = await _call("GET", f"/api/v1/library/anime-entry/{int(media_id)}")
    if _is_error(result):
        return result
    if isinstance(result, dict):
        return _compact_anime_entry(result)
    return _render(result)


@tool
async def seanime_episode_collection(media_id: int) -> str:
    """The full main-episode list for an anime by AniList media id: episode
    number, title, air date, filler flag, and whether it's downloaded. Use for
    "how many episodes does X have", "which episodes are filler", "when did
    episode N air", or to see what's downloaded vs missing for one show."""
    result = await _call("GET", f"/api/v1/anime/episode-collection/{int(media_id)}")
    if _is_error(result):
        return result
    if isinstance(result, dict):
        episodes = result.get("episodes") or []
        if not episodes:
            return "(no episodes found for this media id)"
        episodes = sorted(episodes, key=lambda e: e.get("episodeNumber") or 0)
        lines = [f"{len(episodes)} main episode(s):"]
        lines += [_compact_episode_line(ep) for ep in episodes]
        if result.get("hasMappingError"):
            lines.append("(warning: metadata mapping error — episode data may be incomplete)")
        return _clip("\n".join(lines))
    return _render(result)


@tool
async def seanime_missing_episodes() -> str:
    """Episodes that have aired but are not in the local library yet, per anime
    the user is watching. Use for "what am I missing" / "what can I download"."""
    result = await _call("GET", "/api/v1/library/missing-episodes")
    if _is_error(result):
        return result
    if isinstance(result, dict):
        return _compact_missing(result)
    return _render(result)


@tool
async def seanime_upcoming_schedule() -> str:
    """The airing schedule for anime in the user's collection: which episodes
    air on which date. Use for "what airs this week / today / next"."""
    result = await _call("GET", "/api/v1/library/schedule")
    if _is_error(result):
        return result
    if isinstance(result, list):
        return _compact_schedule(result)
    return _render(result)


@tool
async def seanime_continuity_history() -> str:
    """The user's recent watch history (continuity): what was watched last and
    where playback stopped. Use for "what was I watching" / "where did I leave off"."""
    result = await _call("GET", "/api/v1/continuity/history")
    if _is_error(result):
        return result
    return _compact_continuity(result) or _render(result)


# --- tools: AniList search / details -------------------------------------------
@tool
async def seanime_search_anime(
    search: str = "",
    page: int = 1,
    per_page: int = 10,
    genres: Optional[list[str]] = None,
    season: Optional[str] = None,
    year: Optional[int] = None,
    format: Optional[str] = None,
    status: Optional[str] = None,
    sort: Optional[str] = None,
    adult: str = "exclude",
) -> str:
    """Search/browse AniList for anime through Seanime (works regardless of
    whether the anime is in the library). Returns matching media with ids,
    formats, airing status, genres, and cover image URLs. Use to resolve a
    title to an AniList media id, or to browse by filters alone (`search` may
    be empty when at least one filter or sort is given).

    Filters (combine freely; every one is honored by the API):
      - genres: AniList genre names, e.g. ["Romance", "Comedy"].
      - season: WINTER | SPRING | SUMMER | FALL (with `year` = that airing season).
      - year: airing season year, e.g. 2024.
      - format: TV | TV_SHORT | MOVIE | SPECIAL | OVA | ONA | MUSIC.
      - status: FINISHED | RELEASING | NOT_YET_RELEASED | CANCELLED | HIATUS.
      - sort: e.g. SCORE_DESC, POPULARITY_DESC, TRENDING_DESC, START_DATE_DESC.
      - adult: "exclude" (default — 18+ titles won't appear), "include" (both),
        or "only" (18+ only). Use "include" when a title the user named isn't
        found; use "only" when they explicitly ask for adult content."""
    body, error = _build_search_body(
        kind="anime", search=search, page=page, per_page=per_page, genres=genres,
        season=season, year=year, media_format=format, status=status, sort=sort,
        adult=adult,
    )
    if error:
        return error
    result = await _call("POST", "/api/v1/anilist/list-anime", body)
    if _is_error(result):
        return result
    return _compact_search_results(result, "anime") or _render(result)


@tool
async def seanime_anime_details(media_id: int) -> str:
    """AniList details for one anime by media id: description, genres, tags,
    studios, score, trailer, relations (sequels/prequels/source), and
    recommendations. Library status is NOT included — use `seanime_anime_entry`
    for what's on disk and watch progress."""
    result = await _call("GET", f"/api/v1/anilist/media-details/{int(media_id)}")
    if _is_error(result):
        return result
    if isinstance(result, dict):
        return _compact_details(result, int(media_id), "anime")
    return _render(result)


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


# --- tools: manga ---------------------------------------------------------------
@tool
async def seanime_manga_collection() -> str:
    """The user's manga library, grouped by AniList list status (current,
    planning, completed, ...): one line per entry with title, AniList media id,
    chapter progress, and score. Use for "what manga am I reading" or to find a
    manga's media id. For grouping or statistics questions use
    `seanime_library_overview(kind="manga")` instead."""
    result = await _call("GET", "/api/v1/manga/collection")
    if _is_error(result):
        return result
    if isinstance(result, dict):
        return _compact_collection(result, "chapters")
    return _render(result)


@tool
async def seanime_manga_entry(media_id: int) -> str:
    """The user's reading state for one manga by AniList media id: list status,
    chapter progress, score, and the manga's published chapter/volume totals.
    Get the id from `seanime_manga_collection` or `seanime_search_manga`."""
    result = await _call("GET", f"/api/v1/manga/entry/{int(media_id)}")
    if _is_error(result):
        return result
    if isinstance(result, dict):
        return _compact_manga_entry(result)
    return _render(result)


@tool
async def seanime_manga_details(media_id: int) -> str:
    """AniList details for one manga by media id: genres, tags, rankings,
    relations (adaptations/sequels), and recommendations. The user's reading
    progress is NOT included — use `seanime_manga_entry` for that."""
    result = await _call("GET", f"/api/v1/manga/entry/{int(media_id)}/details")
    if _is_error(result):
        return result
    if isinstance(result, dict):
        return _compact_details(result, int(media_id), "manga")
    return _render(result)


@tool
async def seanime_search_manga(
    search: str = "",
    page: int = 1,
    per_page: int = 10,
    genres: Optional[list[str]] = None,
    year: Optional[int] = None,
    format: Optional[str] = None,
    status: Optional[str] = None,
    sort: Optional[str] = None,
    adult: str = "exclude",
) -> str:
    """Search/browse AniList for manga through Seanime. Returns matching media
    with ids, formats, chapter/volume counts, publishing status, genres, and
    cover image URLs. Use to resolve a manga title to an AniList media id, or
    to browse by filters alone (`search` may be empty when at least one filter
    or sort is given).

    Filters (combine freely; every one is honored by the API):
      - genres: AniList genre names, e.g. ["Romance", "Drama"].
      - year: publication start year, e.g. 2024.
      - format: MANGA | NOVEL | ONE_SHOT.
      - status: FINISHED | RELEASING | NOT_YET_RELEASED | CANCELLED | HIATUS.
      - sort: e.g. SCORE_DESC, POPULARITY_DESC, TRENDING_DESC, START_DATE_DESC.
      - adult: "exclude" (default — 18+ titles won't appear), "include" (both),
        or "only" (18+ only). Use "include" when a title the user named isn't
        found; use "only" when they explicitly ask for adult content."""
    body, error = _build_search_body(
        kind="manga", search=search, page=page, per_page=per_page, genres=genres,
        season=None, year=year, media_format=format, status=status, sort=sort,
        adult=adult,
    )
    if error:
        return error
    result = await _call("POST", "/api/v1/manga/anilist/list", body)
    if _is_error(result):
        return result
    return _compact_search_results(result, "manga") or _render(result)


@tool
async def seanime_manga_update_progress(
    media_id: int, chapter_number: int, total_chapters: int = 0
) -> str:
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
        return result
    return f"Progress updated: manga {media_id} marked at chapter {chapter_number}."


# Read-mostly library/AniList access + the two deliberate mutations (progress).
SEANIME_TOOLS: Final[list[Any]] = [
    seanime_status,
    seanime_library_collection,
    seanime_library_overview,
    seanime_anime_entry,
    seanime_episode_collection,
    seanime_missing_episodes,
    seanime_upcoming_schedule,
    seanime_continuity_history,
    seanime_search_anime,
    seanime_anime_details,
    seanime_update_progress,
    seanime_manga_collection,
    seanime_manga_entry,
    seanime_manga_details,
    seanime_search_manga,
    seanime_manga_update_progress,
]
