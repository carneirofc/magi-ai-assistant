"""Tests for the knowledge tool (agent/tools/knowledge).

The tool is bound to an injected `KnowledgeSearcher`, so a fake stands in for the
store — no Qdrant, no proxy. The contract: a query returns verbatim passages with
their sources as structured data; an empty corpus answer is reported honestly (not
dressed up as a confirmation); the searcher is asked for `config.knowledge_top_k`.
"""

from magi.agent.tools.knowledge import build_knowledge_tools
from magi.core.config import config
from magi.core.knowledge import GLOBAL_SCOPE, KnowledgeHit


class _FakeSearcher:
    """Records the call and returns canned hits."""

    def __init__(self, hits):
        self._hits = hits
        self.calls: list[tuple] = []

    def search(self, query, top_k, *, subject=None, tags=(), scopes=(GLOBAL_SCOPE,)):
        self.calls.append((query, top_k, subject, tuple(tags), tuple(scopes)))
        return self._hits


class _FakeTagger:
    """Records tag writes; returns a canned new tag list (or None = not found)."""

    def __init__(self, result):
        self._result = result
        self.calls: list[tuple] = []

    def tag_document(self, doc_id, *, add=(), remove=()):
        self.calls.append((doc_id, tuple(add), tuple(remove)))
        return self._result


def _tool(hits):
    searcher = _FakeSearcher(hits)
    (search_knowledge,) = build_knowledge_tools(searcher)
    return searcher, search_knowledge


def test_search_returns_snippets_with_sources():
    hits = [
        KnowledgeHit(text="Pemmican keeps for months.", source="food.md", score=0.9, doc_id="food.md"),
        KnowledgeHit(text="Store it cold.", source="food.md", score=0.5, doc_id="food.md"),
    ]
    searcher, search_knowledge = _tool(hits)

    result = search_knowledge.entrypoint(query="how long does pemmican last")

    assert result.get("success") is True
    data = result.get("data")
    assert data["count"] == 2
    assert data["snippets"][0]["text"] == "Pemmican keeps for months."
    assert data["snippets"][0]["source"] == "food.md"
    # The query is forwarded (stripped) and the configured top_k is requested,
    # with no subject/tag narrowing by default.
    assert searcher.calls == [
        ("how long does pemmican last", config.knowledge_top_k, None, (), (GLOBAL_SCOPE,))
    ]


def test_search_empty_is_reported_honestly():
    searcher, search_knowledge = _tool([])

    result = search_knowledge.entrypoint(query="nothing on this")

    assert result.get("success") is True  # an empty corpus is not a failure
    data = result.get("data")
    assert data["count"] == 0 and data["snippets"] == []
    assert "No relevant passages" in result.get("message")


def test_search_strips_whitespace_query():
    searcher, search_knowledge = _tool([])
    search_knowledge.entrypoint(query="  spaced  ")
    assert searcher.calls[0][0] == "spaced"


def test_search_forwards_subject_and_tags():
    searcher, search_knowledge = _tool([])
    search_knowledge.entrypoint(query="q", subject="  Infra  ", tags=["docker", "  ", "ci"])
    # subject stripped to a value (or None when blank); blank tags dropped.
    assert searcher.calls[0][2] == "Infra"
    assert searcher.calls[0][3] == ("docker", "ci")


def test_blank_subject_becomes_none():
    searcher, search_knowledge = _tool([])
    search_knowledge.entrypoint(query="q", subject="   ")
    assert searcher.calls[0][2] is None


def test_snippets_carry_subject_and_tags():
    hits = [
        KnowledgeHit(
            text="t", source="s.md", score=0.9, doc_id="s.md", subject="Infra", tags=["docker"]
        )
    ]
    _, search_knowledge = _tool(hits)
    data = search_knowledge.entrypoint(query="q").get("data")
    assert data["snippets"][0]["subject"] == "Infra"
    assert data["snippets"][0]["tags"] == ["docker"]


# --- tag_knowledge (write tool) ---------------------------------------------
def test_no_tag_tool_without_a_tagger():
    # Read-only deployment: only the search tool is built.
    tools = build_knowledge_tools(_FakeSearcher([]))
    assert [t.name for t in tools] == ["search_knowledge"]


def test_tag_tool_present_with_tagger():
    tagger = _FakeTagger(["a"])
    names = [t.name for t in build_knowledge_tools(_FakeSearcher([]), tagger)]
    assert names == ["search_knowledge", "tag_knowledge"]


def _tag_tool(result):
    tagger = _FakeTagger(result)
    _, tag_knowledge = build_knowledge_tools(_FakeSearcher([]), tagger)
    return tagger, tag_knowledge


def test_tag_knowledge_forwards_and_reports_new_tags():
    tagger, tag_knowledge = _tag_tool(["keep", "new"])
    result = tag_knowledge.entrypoint(doc_id="a.md", add=["new", " "], remove=["drop"])
    assert result.get("success") is True
    assert result.get("data")["tags"] == ["keep", "new"]
    # Blank tags dropped before forwarding.
    assert tagger.calls == [("a.md", ("new",), ("drop",))]


def test_tag_knowledge_missing_doc_is_failure():
    _, tag_knowledge = _tag_tool(None)
    result = tag_knowledge.entrypoint(doc_id="nope.md", add=["x"])
    assert result.get("success") is False
    assert "No knowledge document" in result.get("message")
