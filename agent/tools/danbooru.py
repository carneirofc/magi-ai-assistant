"""Danbooru + Civitai lookups for the prompt-tag specialist.

Each docstring is read by the model to decide WHEN to call the tool — keep it
precise. Tools return short human-readable strings and never raise: a down or
rate-limiting site degrades to an error line the model can relay.

Tag and wiki lookups are local-first: the CSV dumps in config.danbooru_tags_csv
/ danbooru_wiki_csv (see agent/tools/danbooru_local) answer most queries with
no network at all; only a local miss (or missing files) hits the live site.

Danbooru 429-bans impatient anonymous clients, so every call to a host goes
through a shared throttle (minimum gap between requests, serialized across
tasks) and a 429 response is retried once honouring Retry-After. Civitai gets
the same treatment with a smaller gap.
"""

import asyncio
import re
import time

import httpx
from agno.tools import tool
from agno.utils.log import log_info, log_warning

from agent.tools.danbooru_local import LocalDanbooru
from core.config import config

_TIMEOUT = 15.0
_HEADERS = {"User-Agent": "AlyssaBot/1.0 (tag lookup; +https://discord.com)"}

_DANBOORU = "https://danbooru.donmai.us"
_CIVITAI = "https://civitai.com/api/v1"

# Danbooru throttles anonymous clients at roughly 1 req/s — stay safely under.
_DANBOORU_GAP_S = 1.5
_CIVITAI_GAP_S = 0.5
_MAX_RETRY_AFTER_S = 30.0

# Danbooru tag categories (the `category` field on /tags.json results).
_TAG_CATEGORIES = {0: "general", 1: "artist", 3: "copyright", 4: "character", 5: "meta"}

_WIKI_BODY_LIMIT = 6000
_DESCRIPTION_LIMIT = 3000


class _Throttle:
    """Minimum gap between requests to one host, serialized across tasks."""

    def __init__(self, gap_s: float):
        self.gap_s = gap_s
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            delay = self.gap_s - (time.monotonic() - self._last)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = time.monotonic()


_danbooru_throttle = _Throttle(_DANBOORU_GAP_S)
_civitai_throttle = _Throttle(_CIVITAI_GAP_S)

# Local CSV stores, cached per configured path pair (configure() can repoint).
_stores: dict[tuple[str, str], LocalDanbooru] = {}


def _local() -> LocalDanbooru:
    key = (config.danbooru_tags_csv, config.danbooru_wiki_csv)
    store = _stores.get(key)
    if store is None:
        store = _stores[key] = LocalDanbooru(*key)
    return store


async def _get_json(throttle: _Throttle, url: str, params: dict | None = None):
    """GET JSON through the host throttle; on 429, back off once per Retry-After."""
    async with httpx.AsyncClient(
        timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=True
    ) as client:
        for attempt in (1, 2):
            await throttle.wait()
            resp = await client.get(url, params=params)
            if resp.status_code == 429 and attempt == 1:
                try:
                    backoff = float(resp.headers.get("Retry-After") or 5.0)
                except ValueError:
                    backoff = 5.0
                backoff = min(backoff, _MAX_RETRY_AFTER_S)
                log_warning(f"rate-limited (429) on {url}; backing off {backoff:.0f}s")
                await asyncio.sleep(backoff)
                continue
            resp.raise_for_status()
            return resp.json()


def _strip_html(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


def _tag_line(info: dict) -> str:
    category = _TAG_CATEGORIES.get(info.get("category"), "?")
    return f"- {info.get('name')} ({category}, {info.get('post_count', 0)} posts)"


@tool
async def danbooru_wiki(title: str) -> str:
    """Fetch a Danbooru wiki page by title, e.g. 'list_of_uniforms' or 'collarbone'.

    Use to read curated tag lists (pages named list_of_* or tag_group:*) or the
    definition of a single tag. Returns the wiki body text; [[double brackets]]
    inside it are links to other tags/wiki pages you can fetch next.
    """
    slug = (title or "").strip().lower().replace(" ", "_")
    if not slug:
        return "No wiki title given."
    local = _local()
    body = await asyncio.to_thread(local.wiki, slug)
    if body is not None:
        log_info(f"danbooru_wiki '{slug}': local hit ({len(body)} chars)")
        if len(body) > _WIKI_BODY_LIMIT:
            body = body[:_WIKI_BODY_LIMIT] + "\n… (truncated)"
        return f"Wiki '{slug}' (local):\n{body or '(empty page)'}"
    if local.has_wiki:
        # No exact page — the title is probably loosely phrased; offer the
        # closest real titles instead of burning a live request on a 404.
        close = await asyncio.to_thread(local.search_wiki_titles, slug, 10)
        if close:
            log_info(
                f"danbooru_wiki '{slug}': no exact local page, "
                f"suggesting {len(close)} close titles"
            )
            return (
                f"No wiki page titled '{slug}'. Closest local titles "
                "(fetch one with danbooru_wiki):\n" + "\n".join(f"- {t}" for t in close)
            )
    log_info(f"danbooru_wiki '{slug}': local miss → live API")
    try:
        data = await _get_json(_danbooru_throttle, f"{_DANBOORU}/wiki_pages/{slug}.json")
    except Exception as e:
        return f"Could not fetch wiki page '{slug}': {e}"
    log_info(f"danbooru_wiki '{slug}': fetched from live API")
    body = (data.get("body") or "").strip()
    if len(body) > _WIKI_BODY_LIMIT:
        body = body[:_WIKI_BODY_LIMIT] + "\n… (truncated)"
    return f"Wiki '{data.get('title', slug)}':\n{body or '(empty page)'}"


@tool
async def danbooru_wiki_search(query: str) -> str:
    """Find Danbooru wiki page titles loosely matching a phrase or pattern.

    Use when you don't know the exact wiki page name: 'uniform', 'school girl
    outfits', or a glob like 'list_of_*' all work — matching is fuzzy, best
    hits first. Returns up to 30 titles — fetch the interesting ones with
    `danbooru_wiki` next.
    """
    q = (query or "").strip().lower().replace(" ", "_")
    if not q:
        return "No query given."
    titles = await asyncio.to_thread(_local().search_wiki_titles, q)
    if titles:
        log_info(f"danbooru_wiki_search '{q}': {len(titles)} local titles (top: {titles[0]})")
        return f"Wiki pages matching '{q}' (local):\n" + "\n".join(f"- {t}" for t in titles)
    log_info(f"danbooru_wiki_search '{q}': local miss → live API")
    pattern = q if "*" in q else f"*{q}*"
    try:
        data = await _get_json(
            _danbooru_throttle,
            f"{_DANBOORU}/wiki_pages.json",
            params={"search[title_matches]": pattern, "search[hide_deleted]": "yes", "limit": 30},
        )
    except Exception as e:
        return f"Wiki search for '{q}' failed: {e}"
    if not data:
        return f"No wiki pages match '{q}'."
    return f"Wiki pages matching '{q}':\n" + "\n".join(f"- {p.get('title')}" for p in data)


@tool
async def danbooru_search_tags(query: str) -> str:
    """Search Danbooru general/character/copyright tags; use to verify a tag exists.

    Matching is loose: a natural phrase ('school girl uniform', 'maids') finds
    the closest real tags — no need for exact names; explicit * wildcards also
    work. Returns up to 20 tags with category and post count, best match
    first. A tag that doesn't appear here is NOT a valid Danbooru tag.
    NOT for artists — the local dump has no artist tags, so artist names come
    back as wrong look-alike matches; use `danbooru_search_artists` instead.
    """
    q = (query or "").strip().lower().replace(" ", "_")
    if not q:
        return "No query given."
    hits = await asyncio.to_thread(_local().search_tags, q)
    if hits:
        log_info(f"danbooru_search_tags '{q}': {len(hits)} local hits (top: {hits[0][0]})")
        lines = [
            f"- {name} ({_TAG_CATEGORIES.get(cat, '?')}, {count} posts)"
            for name, cat, count in hits
        ]
        return f"Tags matching '{q}' (local):\n" + "\n".join(lines)
    log_info(f"danbooru_search_tags '{q}': local miss → live API")
    try:
        data = await _get_json(
            _danbooru_throttle,
            f"{_DANBOORU}/tags.json",
            params={
                "search[name_matches]": q if "*" in q else f"*{q}*",
                "search[order]": "count",
                "search[hide_empty]": "true",
                "limit": 20,
            },
        )
    except Exception as e:
        return f"Tag search for '{q}' failed: {e}"
    if not data:
        return f"No tags match '{q}'."
    return f"Tags matching '{q}':\n" + "\n".join(_tag_line(t) for t in data)


@tool
async def danbooru_search_artists(query: str) -> str:
    """Search Danbooru ARTIST tags by name; the only tool that finds artists.

    Always use this for artist/style lookups ('art by wlop', 'style of …') —
    artist tags are absent from the local dump and `danbooru_search_tags`
    cannot verify them. Query with the artist's romanized name (substring is
    fine; * wildcards work). Always live API. Returns up to 20 artist tags
    with post counts, most-used first; an artist not listed here has no
    Danbooru tag.
    """
    q = (query or "").strip().lower().replace(" ", "_")
    if not q:
        return "No query given."
    try:
        data = await _get_json(
            _danbooru_throttle,
            f"{_DANBOORU}/tags.json",
            params={
                "search[name_matches]": q if "*" in q else f"*{q}*",
                "search[category]": 1,
                "search[order]": "count",
                "search[hide_empty]": "true",
                "limit": 20,
            },
        )
    except Exception as e:
        return f"Artist search for '{q}' failed: {e}"
    if not data:
        return (
            f"No artist tags match '{q}'. Try the artist's romanized name "
            "or a shorter fragment of it."
        )
    return f"Artist tags matching '{q}':\n" + "\n".join(_tag_line(t) for t in data)


@tool
async def danbooru_related_tags(tag: str) -> str:
    """List the tags that most often co-occur with `tag` on Danbooru posts.

    Use to expand a theme: given 'collarbone' it returns what real posts pair
    with it. `tag` must be one valid tag (underscores, not spaces).
    """
    t = (tag or "").strip().lower().replace(" ", "_")
    if not t:
        return "No tag given."
    try:
        data = await _get_json(
            _danbooru_throttle,
            f"{_DANBOORU}/related_tag.json",
            params={"search[query]": t, "limit": 25},
        )
    except Exception as e:
        return f"Related-tag lookup for '{t}' failed: {e}"
    related = data.get("related_tags") or []
    if not related:
        return f"No related tags found for '{t}' (is it a valid tag?)."
    lines = [_tag_line(r.get("tag") or {}) for r in related]
    return f"Tags co-occurring with '{t}':\n" + "\n".join(lines)


@tool
async def danbooru_post_tags(tags: str) -> str:
    """Show the full tag lists of recent Danbooru posts matching a tag search.

    Use to see how real posts combine tags around a theme. `tags` is a Danbooru
    search query (e.g. 'collarbone 1girl'); anonymous search allows AT MOST two
    tags. Returns each post's character/copyright/general tags.
    """
    q = (tags or "").strip()
    if not q:
        return "No tags given."
    try:
        data = await _get_json(
            _danbooru_throttle,
            f"{_DANBOORU}/posts.json",
            params={"tags": q, "limit": 5},
        )
    except Exception as e:
        return f"Post search for '{q}' failed: {e}"
    if not data:
        return f"No posts match '{q}'."
    blocks = []
    for p in data:
        lines = [f"Post {p.get('id')} (score {p.get('score', 0)}, rating {p.get('rating', '?')}):"]
        for label, key in (
            ("characters", "tag_string_character"),
            ("copyright", "tag_string_copyright"),
            ("general", "tag_string_general"),
        ):
            value = (p.get(key) or "").strip()
            if value:
                lines.append(f"  {label}: {value}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


@tool
async def civitai_model(model_id: int) -> str:
    """Fetch a Civitai model page by numeric id (e.g. 994401 for MatureRitual).

    Returns the model's name, type, tags, the author's usage notes (recommended
    prompts, sampler, CFG), and its versions with ids — pass a version id to
    `civitai_model_version` for version-specific notes.
    """
    try:
        data = await _get_json(_civitai_throttle, f"{_CIVITAI}/models/{int(model_id)}")
    except Exception as e:
        return f"Could not fetch Civitai model {model_id}: {e}"
    versions = data.get("modelVersions") or []
    version_lines = [
        f"- {v.get('id')}: {v.get('name')} (base: {v.get('baseModel', '?')})"
        for v in versions[:10]
    ]
    description = _strip_html(data.get("description", ""))[:_DESCRIPTION_LIMIT]
    return (
        f"Civitai model {model_id}: {data.get('name')}\n"
        f"Type: {data.get('type')}; tags: {', '.join(data.get('tags') or []) or '-'}\n"
        "Versions:\n" + ("\n".join(version_lines) or "- none") + "\n"
        f"Description: {description or '(none)'}"
    )


@tool
async def civitai_model_version(version_id: int) -> str:
    """Fetch one Civitai model version by id (e.g. 2730987) for its usage notes.

    Returns base model, trained/trigger words, and the version description —
    where authors put recommended sampler, steps, CFG, and prompt templates.
    """
    try:
        data = await _get_json(
            _civitai_throttle, f"{_CIVITAI}/model-versions/{int(version_id)}"
        )
    except Exception as e:
        return f"Could not fetch Civitai model version {version_id}: {e}"
    model = data.get("model") or {}
    words = ", ".join(data.get("trainedWords") or []) or "-"
    description = _strip_html(data.get("description", ""))[:_DESCRIPTION_LIMIT]
    return (
        f"Civitai version {version_id}: {model.get('name')} — {data.get('name')}\n"
        f"Base model: {data.get('baseModel', '?')}\n"
        f"Trained words: {words}\n"
        f"Notes: {description or '(none)'}"
    )


DANBOORU_TOOLS = [
    danbooru_wiki,
    danbooru_wiki_search,
    danbooru_search_tags,
    danbooru_search_artists,
    danbooru_related_tags,
    danbooru_post_tags,
    civitai_model,
    civitai_model_version,
]
