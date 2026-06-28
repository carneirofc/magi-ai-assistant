"""Knowledge tool — how the lead retrieves from the global reference corpus.

The knowledge layer (core/knowledge) is a read-only RAG corpus, distinct from
memory: memory is what the assistant knows about *this user*; knowledge is what
the corpus says about a *topic*. Retrieval is a tool (a workflow the model invokes
when it needs reference material) rather than always-injected context, so the
window isn't paid on every turn — only when a question actually calls for a lookup.

`build_knowledge_tools(searcher)` binds the tool to an injected `KnowledgeSearcher`
(the `KnowledgeStore`, or a fake in tests) — no globals. The corpus is global, so
the tool takes only a query; the searcher's `scopes=` seam is where per-user/
session knowledge would later be added without changing this tool's contract.
"""

from typing import Annotated

from agno.tools import tool
from agno.utils.log import log_info
from pydantic import BaseModel, Field

from magi.agent.tools.outputs import ToolOutput, ok
from magi.core.config import config
from magi.core.knowledge import KnowledgeSearcher


class KnowledgeSnippet(BaseModel):
    text: str = Field(description="The retrieved passage, verbatim from the source.")
    source: str = Field(description="Where the passage came from (document name / origin).")
    score: float = Field(description="Relevance score (higher is closer); for ranking only.")


class KnowledgeSearchData(BaseModel):
    query: str = Field(description="The query that was searched.")
    snippets: list[KnowledgeSnippet] = Field(description="Matching passages, most relevant first.")
    count: int = Field(description="How many passages were returned.")


def build_knowledge_tools(searcher: KnowledgeSearcher) -> list:
    """Return the knowledge tool set bound to `searcher` (dependency-injected)."""

    @tool(
        description="Search the knowledge base for reference material relevant to a question.",
        instructions=(
            "Use when answering needs factual reference material that may live in the curated "
            "knowledge base — documentation, guides, domain facts — rather than the user's own "
            "history (that is your memory). Pass a focused natural-language query. Returns verbatim "
            "passages with their sources; ground your answer in them and cite the source when you "
            "rely on one. An empty result means the base has nothing relevant — say so rather than "
            "inventing an answer."
        ),
        show_result=True,
    )
    def search_knowledge(
        query: Annotated[
            str,
            Field(min_length=1, description="Natural-language description of what to look up."),
        ],
    ) -> ToolOutput[KnowledgeSearchData]:
        """Retrieve passages from the global knowledge base most relevant to `query`.

        Use for reference/domain knowledge, not for facts about the current user
        (those are in your memory). Returns up to a handful of verbatim passages,
        each with its source, ranked by relevance — empty when nothing matches.
        Ground answers in what comes back and cite the source; never present an
        empty result as if the base confirmed something.
        """
        hits = searcher.search(query.strip(), config.knowledge_top_k)
        snippets = [
            KnowledgeSnippet(text=h.text, source=h.source, score=h.score) for h in hits
        ]
        log_info(f"knowledge: search {query.strip()!r} -> {len(snippets)} hit(s)")
        msg = (
            f"Found {len(snippets)} relevant passage(s)."
            if snippets
            else "No relevant passages in the knowledge base."
        )
        return ok(msg, KnowledgeSearchData(query=query.strip(), snippets=snippets, count=len(snippets)))

    return [search_knowledge]
