"""Danbooru + Civitai lookups for the prompt-tag specialist.

Each docstring is read by the model to decide WHEN to call the tool — keep it
precise. Tools return short human-readable strings and never raise: a down or
rate-limiting site degrades to an error line the model can relay.

Tag and wiki lookups are local-first: the CSV dumps in config.danbooru_tags_csv
/ danbooru_wiki_csv (see magi/agent/tools/danbooru_local) answer most queries with
no network at all; only a local miss (or missing files) hits the live site.

Danbooru 429-bans impatient anonymous clients, so every call to a host goes
through a shared throttle (minimum gap between requests, serialized across
tasks) and a 429 response is retried once honouring Retry-After. Civitai gets
the same treatment with a smaller gap.
"""

import asyncio
import re
import time
from typing import Annotated, Any

import httpx
from agno.tools import tool
from agno.utils.log import log_info, log_warning
from pydantic import Field

from magi.agent.tools.danbooru_local import LocalDanbooru
from magi.agent.tools.outputs import FlexiblePayload, ToolOutput, fail, ok
from magi.core.config import Config

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

# Local CSV stores, cached per configured path pair (a repointed Config repoints).
_stores: dict[tuple[str, str], LocalDanbooru] = {}


def _local(config: Config) -> LocalDanbooru:
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


class DanbooruTextData(FlexiblePayload):
    text: str


DanbooruOutput = ToolOutput[DanbooruTextData | FlexiblePayload]


def _text_ok(message: str, text: str, **data: object) -> ToolOutput[DanbooruTextData]:
    return ok(message, DanbooruTextData(text=text, **data))


def _text_fail(message: str, **data: object) -> ToolOutput[FlexiblePayload]:
    return fail(message, FlexiblePayload(**data) if data else None)


async def _danbooru_wiki(title: str, config: Config) -> DanbooruOutput:
    slug = (title or "").strip().lower().replace(" ", "_")
    if not slug:
        return _text_fail("No wiki title given.")
    local = _local(config)
    body = await asyncio.to_thread(local.wiki, slug)
    if body is not None:
        log_info(f"danbooru_wiki '{slug}': local hit ({len(body)} chars)")
        if len(body) > _WIKI_BODY_LIMIT:
            body = body[:_WIKI_BODY_LIMIT] + "\n… (truncated)"
        return _text_ok(
            f"Wiki page '{slug}' found locally.",
            f"Wiki '{slug}' (local):\n{body or '(empty page)'}",
            title=slug,
            source="local",
            body=body or "",
        )
    if local.has_wiki:
        # No exact page — the title is probably loosely phrased; offer the
        # closest real titles instead of burning a live request on a 404.
        close = await asyncio.to_thread(local.search_wiki_titles, slug, 10)
        if close:
            log_info(
                f"danbooru_wiki '{slug}': no exact local page, "
                f"suggesting {len(close)} close titles"
            )
            text = (
                f"No wiki page titled '{slug}'. Closest local titles "
                "(fetch one with danbooru_wiki):\n" + "\n".join(f"- {t}" for t in close)
            )
            return _text_fail(text, title=slug, closest_titles=close)
    log_info(f"danbooru_wiki '{slug}': local miss → live API")
    try:
        data = await _get_json(_danbooru_throttle, f"{_DANBOORU}/wiki_pages/{slug}.json")
    except Exception as e:
        return _text_fail(f"Could not fetch wiki page '{slug}': {e}", title=slug)
    log_info(f"danbooru_wiki '{slug}': fetched from live API")
    body = (data.get("body") or "").strip()
    if len(body) > _WIKI_BODY_LIMIT:
        body = body[:_WIKI_BODY_LIMIT] + "\n… (truncated)"
    actual_title = data.get("title", slug)
    return _text_ok(
        f"Wiki page '{actual_title}' fetched.",
        f"Wiki '{actual_title}':\n{body or '(empty page)'}",
        title=actual_title,
        source="live",
        body=body or "",
    )


async def _danbooru_wiki_search(query: str, config: Config) -> DanbooruOutput:
    q = (query or "").strip().lower().replace(" ", "_")
    if not q:
        return _text_fail("No query given.")
    titles = await asyncio.to_thread(_local(config).search_wiki_titles, q)
    if titles:
        log_info(f"danbooru_wiki_search '{q}': {len(titles)} local titles (top: {titles[0]})")
        text = f"Wiki pages matching '{q}' (local):\n" + "\n".join(f"- {t}" for t in titles)
        return _text_ok(f"Found {len(titles)} local wiki title(s).", text, query=q, source="local", titles=titles)
    log_info(f"danbooru_wiki_search '{q}': local miss → live API")
    pattern = q if "*" in q else f"*{q}*"
    try:
        data = await _get_json(
            _danbooru_throttle,
            f"{_DANBOORU}/wiki_pages.json",
            params={"search[title_matches]": pattern, "search[hide_deleted]": "yes", "limit": 30},
        )
    except Exception as e:
        return _text_fail(f"Wiki search for '{q}' failed: {e}", query=q)
    if not data:
        return _text_ok(f"No wiki pages match '{q}'.", f"No wiki pages match '{q}'.", query=q, titles=[])
    titles = [p.get("title") for p in data]
    text = f"Wiki pages matching '{q}':\n" + "\n".join(f"- {t}" for t in titles)
    return _text_ok(f"Found {len(titles)} wiki title(s).", text, query=q, source="live", titles=titles)


async def _danbooru_search_tags(query: str, config: Config) -> DanbooruOutput:
    q = (query or "").strip().lower().replace(" ", "_")
    if not q:
        return _text_fail("No query given.")
    hits = await asyncio.to_thread(_local(config).search_tags, q)
    if hits:
        log_info(f"danbooru_search_tags '{q}': {len(hits)} local hits (top: {hits[0][0]})")
        lines = [
            f"- {name} ({_TAG_CATEGORIES.get(cat, '?')}, {count} posts)"
            for name, cat, count in hits
        ]
        return _text_ok(
            f"Found {len(hits)} local tag match(es).",
            f"Tags matching '{q}' (local):\n" + "\n".join(lines),
            query=q,
            source="local",
            tags=[{"name": name, "category": _TAG_CATEGORIES.get(cat, "?"), "post_count": count} for name, cat, count in hits],
        )
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
        return _text_fail(f"Tag search for '{q}' failed: {e}", query=q)
    if not data:
        return _text_ok(f"No tags match '{q}'.", f"No tags match '{q}'.", query=q, tags=[])
    return _text_ok(
        f"Found {len(data)} tag match(es).",
        f"Tags matching '{q}':\n" + "\n".join(_tag_line(t) for t in data),
        query=q,
        source="live",
        tags=[{"name": t.get("name"), "category": _TAG_CATEGORIES.get(t.get("category"), "?"), "post_count": t.get("post_count", 0)} for t in data],
    )


async def _danbooru_search_artists(query: str) -> DanbooruOutput:
    q = (query or "").strip().lower().replace(" ", "_")
    if not q:
        return _text_fail("No query given.")
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
        return _text_fail(f"Artist search for '{q}' failed: {e}", query=q)
    if not data:
        text = f"No artist tags match '{q}'. Try the artist's romanized name or a shorter fragment of it."
        return _text_ok(text, text, query=q, tags=[])
    return _text_ok(
        f"Found {len(data)} artist tag match(es).",
        f"Artist tags matching '{q}':\n" + "\n".join(_tag_line(t) for t in data),
        query=q,
        tags=[{"name": t.get("name"), "post_count": t.get("post_count", 0)} for t in data],
    )


async def _danbooru_related_tags(tag: str) -> DanbooruOutput:
    t = (tag or "").strip().lower().replace(" ", "_")
    if not t:
        return _text_fail("No tag given.")
    try:
        data = await _get_json(
            _danbooru_throttle,
            f"{_DANBOORU}/related_tag.json",
            params={"search[query]": t, "limit": 25},
        )
    except Exception as e:
        return _text_fail(f"Related-tag lookup for '{t}' failed: {e}", tag=t)
    related = data.get("related_tags") or []
    if not related:
        text = f"No related tags found for '{t}' (is it a valid tag?)."
        return _text_ok(text, text, tag=t, related_tags=[])
    lines = [_tag_line(r.get("tag") or {}) for r in related]
    return _text_ok(
        f"Found {len(related)} related tag(s).",
        f"Tags co-occurring with '{t}':\n" + "\n".join(lines),
        tag=t,
        related_tags=[
            {
                "name": (r.get("tag") or {}).get("name"),
                "category": _TAG_CATEGORIES.get((r.get("tag") or {}).get("category"), "?"),
                "post_count": (r.get("tag") or {}).get("post_count", 0),
            }
            for r in related
        ],
    )


async def _danbooru_post_tags(tags: str) -> DanbooruOutput:
    q = (tags or "").strip()
    if not q:
        return _text_fail("No tags given.")
    try:
        data = await _get_json(
            _danbooru_throttle,
            f"{_DANBOORU}/posts.json",
            params={"tags": q, "limit": 5},
        )
    except Exception as e:
        return _text_fail(f"Post search for '{q}' failed: {e}", tags=q)
    if not data:
        text = f"No posts match '{q}'."
        return _text_ok(text, text, tags=q, posts=[])
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
    return _text_ok(
        f"Found {len(data)} post(s).",
        "\n".join(blocks),
        tags=q,
        posts=[
            {
                "id": p.get("id"),
                "score": p.get("score", 0),
                "rating": p.get("rating"),
                "characters": (p.get("tag_string_character") or "").split(),
                "copyright": (p.get("tag_string_copyright") or "").split(),
                "general": (p.get("tag_string_general") or "").split(),
            }
            for p in data
        ],
    )


async def _civitai_model(model_id: int) -> DanbooruOutput:
    try:
        data = await _get_json(_civitai_throttle, f"{_CIVITAI}/models/{int(model_id)}")
    except Exception as e:
        return _text_fail(f"Could not fetch Civitai model {model_id}: {e}", model_id=model_id)
    versions = data.get("modelVersions") or []
    version_lines = [
        f"- {v.get('id')}: {v.get('name')} (base: {v.get('baseModel', '?')})"
        for v in versions[:10]
    ]
    description = _strip_html(data.get("description", ""))[:_DESCRIPTION_LIMIT]
    text = (
        f"Civitai model {model_id}: {data.get('name')}\n"
        f"Type: {data.get('type')}; tags: {', '.join(data.get('tags') or []) or '-'}\n"
        "Versions:\n" + ("\n".join(version_lines) or "- none") + "\n"
        f"Description: {description or '(none)'}"
    )
    return _text_ok(
        f"Fetched Civitai model {model_id}.",
        text,
        model_id=model_id,
        name=data.get("name"),
        type=data.get("type"),
        tags=data.get("tags") or [],
        versions=[
            {"id": v.get("id"), "name": v.get("name"), "base_model": v.get("baseModel")}
            for v in versions[:10]
        ],
        description=description or "",
    )


async def _civitai_model_version(version_id: int) -> DanbooruOutput:
    try:
        data = await _get_json(
            _civitai_throttle, f"{_CIVITAI}/model-versions/{int(version_id)}"
        )
    except Exception as e:
        return _text_fail(f"Could not fetch Civitai model version {version_id}: {e}", version_id=version_id)
    model = data.get("model") or {}
    words = ", ".join(data.get("trainedWords") or []) or "-"
    description = _strip_html(data.get("description", ""))[:_DESCRIPTION_LIMIT]
    text = (
        f"Civitai version {version_id}: {model.get('name')} — {data.get('name')}\n"
        f"Base model: {data.get('baseModel', '?')}\n"
        f"Trained words: {words}\n"
        f"Notes: {description or '(none)'}"
    )
    return _text_ok(
        f"Fetched Civitai model version {version_id}.",
        text,
        version_id=version_id,
        model_name=model.get("name"),
        version_name=data.get("name"),
        base_model=data.get("baseModel"),
        trained_words=data.get("trainedWords") or [],
        notes=description or "",
    )


def build_danbooru_tools(config: Config) -> list:
    """Danbooru + Civitai tools, with `config` (CSV dump paths) captured by closure."""

    @tool(
        description="Fetch a Danbooru wiki page by exact title for tag definitions or curated tag lists.",
        instructions=(
            "Use for pages such as list_of_* or tag_group:* and for single-tag definitions. "
            "Titles are normalized to lowercase underscores; returned [[links]] can be fetched next."
        ),
        show_result=True,
    )
    async def danbooru_wiki(
        title: Annotated[
            str,
            Field(
                min_length=1,
                description="Danbooru wiki page title, normalized to lowercase underscores.",
            ),
        ],
    ) -> DanbooruOutput:
        """Fetch a Danbooru wiki page by title, e.g. 'list_of_uniforms' or 'collarbone'.

        Use to read curated tag lists (pages named list_of_* or tag_group:*) or the
        definition of a single tag. Returns the wiki body text; [[double brackets]]
        inside it are links to other tags/wiki pages you can fetch next.
        """
        return await _danbooru_wiki(title, config)

    @tool(
        description="Search Danbooru wiki page titles by fuzzy phrase or wildcard pattern.",
        instructions="Use when the exact wiki title is unknown. Fetch interesting returned titles with danbooru_wiki.",
        show_result=True,
    )
    async def danbooru_wiki_search(
        query: Annotated[
            str,
            Field(
                min_length=1,
                description="Phrase or wildcard pattern to match against Danbooru wiki page titles.",
            ),
        ],
    ) -> DanbooruOutput:
        """Find Danbooru wiki page titles loosely matching a phrase or pattern.

        Use when you don't know the exact wiki page name: 'uniform', 'school girl
        outfits', or a glob like 'list_of_*' all work — matching is fuzzy, best
        hits first. Returns up to 30 titles — fetch the interesting ones with
        `danbooru_wiki` next.
        """
        return await _danbooru_wiki_search(query, config)

    @tool(
        description="Search Danbooru non-artist tags and verify whether a tag exists.",
        instructions=(
            "Use for general, character, copyright, and meta tags. Do not use for artists; "
            "call danbooru_search_artists for artist/style tags."
        ),
        show_result=True,
    )
    async def danbooru_search_tags(
        query: Annotated[
            str,
            Field(
                min_length=1,
                description="General, character, copyright, or meta tag phrase to verify.",
            ),
        ],
    ) -> DanbooruOutput:
        """Search Danbooru general/character/copyright tags; use to verify a tag exists.

        Matching is loose: a natural phrase ('school girl uniform', 'maids') finds
        the closest real tags — no need for exact names; explicit * wildcards also
        work. Returns up to 20 tags with category and post count, best match
        first. A tag that doesn't appear here is NOT a valid Danbooru tag.
        NOT for artists — the local dump has no artist tags, so artist names come
        back as wrong look-alike matches; use `danbooru_search_artists` instead.
        """
        return await _danbooru_search_tags(query, config)

    @tool(
        description="Search Danbooru artist tags by name.",
        instructions=(
            "Use for artist or style lookups. Query with romanized names or shorter fragments; "
            "this is the only Danbooru tool intended to verify artist tags."
        ),
        show_result=True,
    )
    async def danbooru_search_artists(
        query: Annotated[
            str,
            Field(
                min_length=1,
                description="Artist name, romanized name, fragment, or wildcard pattern.",
            ),
        ],
    ) -> DanbooruOutput:
        """Search Danbooru ARTIST tags by name; the only tool that finds artists.

        Always use this for artist/style lookups ('art by wlop', 'style of …') —
        artist tags are absent from the local dump and `danbooru_search_tags`
        cannot verify them. Query with the artist's romanized name (substring is
        fine; * wildcards work). Always live API. Returns up to 20 artist tags
        with post counts, most-used first; an artist not listed here has no
        Danbooru tag.
        """
        return await _danbooru_search_artists(query)

    @tool(
        description="List tags that commonly co-occur with one valid Danbooru tag.",
        instructions="Use to expand a prompt theme from a known valid tag. Pass one tag using underscores, not a free-form phrase.",
        show_result=True,
    )
    async def danbooru_related_tags(
        tag: Annotated[
            str,
            Field(
                min_length=1,
                pattern=r"^[^\s]+$",
                description="One valid Danbooru tag using underscores instead of spaces.",
            ),
        ],
    ) -> DanbooruOutput:
        """List the tags that most often co-occur with `tag` on Danbooru posts.

        Use to expand a theme: given 'collarbone' it returns what real posts pair
        with it. `tag` must be one valid tag (underscores, not spaces).
        """
        return await _danbooru_related_tags(tag)

    @tool(
        description="Show full tag lists from recent Danbooru posts matching a tag query.",
        instructions=(
            "Use to see how real posts combine tags around a theme. Anonymous Danbooru search allows at most two tags."
        ),
        show_result=True,
    )
    async def danbooru_post_tags(
        tags: Annotated[
            str,
            Field(
                min_length=1,
                description="Danbooru post search query, normally one or two tags.",
            ),
        ],
    ) -> DanbooruOutput:
        """Show the full tag lists of recent Danbooru posts matching a tag search.

        Use to see how real posts combine tags around a theme. `tags` is a Danbooru
        search query (e.g. 'collarbone 1girl'); anonymous search allows AT MOST two
        tags. Returns each post's character/copyright/general tags.
        """
        return await _danbooru_post_tags(tags)

    @tool(
        description="Fetch a Civitai model page by numeric model id.",
        instructions=(
            "Use for model-level metadata, tags, author notes, and available version ids. "
            "Use civitai_model_version for version-specific trigger words and settings."
        ),
        show_result=True,
    )
    async def civitai_model(
        model_id: Annotated[
            int,
            Field(gt=0, description="Numeric Civitai model id."),
        ],
    ) -> DanbooruOutput:
        """Fetch a Civitai model page by numeric id (e.g. 994401 for MatureRitual).

        Returns the model's name, type, tags, the author's usage notes (recommended
        prompts, sampler, CFG), and its versions with ids — pass a version id to
        `civitai_model_version` for version-specific notes.
        """
        return await _civitai_model(model_id)

    @tool(
        description="Fetch one Civitai model version by numeric version id.",
        instructions="Use for version-specific base model, trigger words, author notes, sampler, steps, CFG, or prompt templates.",
        show_result=True,
    )
    async def civitai_model_version(
        version_id: Annotated[
            int,
            Field(gt=0, description="Numeric Civitai model version id."),
        ],
    ) -> DanbooruOutput:
        """Fetch one Civitai model version by id (e.g. 2730987) for its usage notes.

        Returns base model, trained/trigger words, and the version description —
        where authors put recommended sampler, steps, CFG, and prompt templates.
        """
        return await _civitai_model_version(version_id)

    return [
        danbooru_wiki,
        danbooru_wiki_search,
        danbooru_search_tags,
        danbooru_search_artists,
        danbooru_related_tags,
        danbooru_post_tags,
        civitai_model,
        civitai_model_version,
    ]
