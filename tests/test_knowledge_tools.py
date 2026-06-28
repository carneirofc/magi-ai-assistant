"""Tests for the knowledge tool (agent/tools/knowledge).

The tool is bound to an injected `KnowledgeSearcher`, so a fake stands in for the
store — no Qdrant, no proxy. The contract: a query returns verbatim passages with
their sources as structured data; an empty corpus answer is reported honestly (not
dressed up as a confirmation); the searcher is asked for `config.knowledge_top_k`.
"""

from agent.tools.knowledge import build_knowledge_tools
from core.config import config
from core.knowledge import GLOBAL_SCOPE, KnowledgeHit


class _FakeSearcher:
    """Records the call and returns canned hits."""

    def __init__(self, hits):
        self._hits = hits
        self.calls: list[tuple] = []

    def search(self, query, top_k, *, scopes=(GLOBAL_SCOPE,)):
        self.calls.append((query, top_k, tuple(scopes)))
        return self._hits


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
    # The query is forwarded (stripped) and the configured top_k is requested.
    assert searcher.calls == [("how long does pemmican last", config.knowledge_top_k, (GLOBAL_SCOPE,))]


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
