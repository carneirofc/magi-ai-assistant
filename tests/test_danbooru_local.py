"""Tests for the local Danbooru CSV store and the tools' local-first path.

`LocalDanbooru` is exercised against tiny CSVs written per-test: wildcard tag
search ranked by post count, alias resolution, exact-title wiki lookup with
normalization and deleted-row skipping, wiki title search, and graceful
misses when the files don't exist. The tool-level tests then prove a local hit
never constructs an HTTP client, and a local miss falls back to the API.
"""

import pytest

import agent.tools.danbooru as danbooru
from agent.tools.danbooru_local import LocalDanbooru
from core.config import config, configure


def _tool_text(result: dict) -> str:
    data = result.get("data") or {}
    return " ".join(str(part) for part in (result.get("message", ""), data.get("text", ""), data) if part)


TAGS_CSV = """tag,category,count,alias
1girl,0,4974288,"女の子,girl"
maid,0,123456,"メイド"
maid_headdress,0,65432,
hatsune_miku,4,99999,"初音ミク,miku"
"""

WIKI_CSV = """id,created_at,updated_at,title,body,is_locked,other_names,is_deleted
10,2005-01-01,2026-01-01,list_of_uniforms,"Uniform tags:

[[school_uniform]] and [[maid]].",false,,false
11,2005-01-01,2026-01-01,collarbone,The bone. Use when visible.,false,,false
12,2005-01-01,2026-01-01,old_page,gone,false,,true
"""


@pytest.fixture
def store(tmp_path):
    tags = tmp_path / "tags.csv"
    wiki = tmp_path / "wiki.csv"
    tags.write_text(TAGS_CSV, encoding="utf-8")
    wiki.write_text(WIKI_CSV, encoding="utf-8")
    return LocalDanbooru(str(tags), str(wiki))


def test_search_tags_ranks_by_count_and_wildcards(store):
    hits = store.search_tags("maid")

    assert hits == [("maid", 0, 123456), ("maid_headdress", 0, 65432)]


def test_search_tags_explicit_wildcard(store):
    hits = store.search_tags("*_headdress")

    assert [name for name, *_ in hits] == ["maid_headdress"]


def test_search_tags_resolves_alias(store):
    hits = store.search_tags("初音ミク")

    assert [name for name, *_ in hits][0] == "hatsune_miku"


def test_search_tags_loose_phrase(store):
    """User phrasing ('1 girl') finds the real tag without exact-name match."""
    hits = store.search_tags("1 girl")

    assert [name for name, *_ in hits][0] == "1girl"


def test_search_tags_plural_still_hits(store):
    hits = store.search_tags("maids")

    assert "maid" in [name for name, *_ in hits]


def test_search_tags_typo_close_match(store):
    hits = store.search_tags("maidd")

    assert "maid" in [name for name, *_ in hits]


def test_wiki_exact_title_normalized(store):
    body = store.wiki("List of Uniforms")

    assert body is not None and "[[school_uniform]]" in body


def test_wiki_skips_deleted_and_misses(store):
    assert store.wiki("old_page") is None
    assert store.wiki("does_not_exist") is None


def test_search_wiki_titles(store):
    assert store.search_wiki_titles("list_of_*") == ["list_of_uniforms"]
    assert "old_page" not in store.search_wiki_titles("page")


def test_search_wiki_titles_loose_phrase(store):
    """'uniforms list' (wrong order, plural) still finds list_of_uniforms."""
    assert "list_of_uniforms" in store.search_wiki_titles("uniforms list")


def test_missing_files_degrade_to_misses(tmp_path):
    store = LocalDanbooru(str(tmp_path / "no.csv"), str(tmp_path / "no2.csv"))

    assert not store.has_tags and not store.has_wiki
    assert store.search_tags("maid") == []
    assert store.wiki("collarbone") is None
    assert store.search_wiki_titles("list") == []


# --- tools prefer local data and never open an HTTP client on a hit ---


class _NoNetwork:
    def __init__(self, **_):
        raise AssertionError("HTTP client constructed despite local hit")


@pytest.fixture
def local_config(tmp_path, monkeypatch):
    tags = tmp_path / "tags.csv"
    wiki = tmp_path / "wiki.csv"
    tags.write_text(TAGS_CSV, encoding="utf-8")
    wiki.write_text(WIKI_CSV, encoding="utf-8")
    monkeypatch.setattr(danbooru, "_stores", {})
    before = (config.danbooru_tags_csv, config.danbooru_wiki_csv)
    configure(danbooru_tags_csv=str(tags), danbooru_wiki_csv=str(wiki))
    yield
    configure(danbooru_tags_csv=before[0], danbooru_wiki_csv=before[1])


async def test_search_tags_tool_serves_local_hit(local_config, monkeypatch):
    monkeypatch.setattr(danbooru.httpx, "AsyncClient", _NoNetwork)

    result = await danbooru.danbooru_search_tags.entrypoint(query="maid")

    assert "(local)" in _tool_text(result)
    assert "- maid (general, 123456 posts)" in _tool_text(result)


async def test_wiki_tool_serves_local_hit(local_config, monkeypatch):
    monkeypatch.setattr(danbooru.httpx, "AsyncClient", _NoNetwork)

    result = await danbooru.danbooru_wiki.entrypoint(title="list of uniforms")

    assert "(local)" in _tool_text(result) and "[[school_uniform]]" in _tool_text(result)


async def test_wiki_search_tool_serves_local_hit(local_config, monkeypatch):
    monkeypatch.setattr(danbooru.httpx, "AsyncClient", _NoNetwork)

    result = await danbooru.danbooru_wiki_search.entrypoint(query="list_of_*")

    assert "(local)" in _tool_text(result) and "- list_of_uniforms" in _tool_text(result)


async def test_wiki_tool_suggests_close_titles_on_miss(local_config, monkeypatch):
    monkeypatch.setattr(danbooru.httpx, "AsyncClient", _NoNetwork)

    result = await danbooru.danbooru_wiki.entrypoint(title="uniforms")

    assert "No wiki page titled 'uniforms'" in _tool_text(result)
    assert "- list_of_uniforms" in _tool_text(result)


async def test_local_miss_falls_back_to_api(local_config, monkeypatch):
    class _FakeResponse:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            pass

        def json(self):
            return [{"name": "obscure_tag", "category": 0, "post_count": 3}]

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None):
            return _FakeResponse()

    monkeypatch.setattr(danbooru.httpx, "AsyncClient", lambda **_: _FakeClient())
    monkeypatch.setattr(danbooru._danbooru_throttle, "gap_s", 0.0)

    result = await danbooru.danbooru_search_tags.entrypoint(query="obscure_tag")

    assert "(local)" not in _tool_text(result)
    assert "- obscure_tag (general, 3 posts)" in _tool_text(result)
