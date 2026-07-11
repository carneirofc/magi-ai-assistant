"""Web search — the lead's window onto the live web (ddgs-backed).

Search returns leads (title / url / snippet), not answers: the contract steers
the model to READ a promising result via the HTTP tools before citing it, so
claims stay grounded in fetched pages rather than snippet fragments.

Gated by `websearch_enabled` and the optional `websearch` extra (ddgs);
`build_websearch_tools()` returns [] when either is missing, so the base
deployment stays offline-by-default and boots without the package.
"""

from typing import Annotated

from agno.tools import tool
from agno.utils.log import log_info, log_warning
from pydantic import BaseModel, Field

from magi.agent.tools.outputs import ToolOutput, fail, ok
from magi.core.config import config


class WebSearchResult(BaseModel):
    title: str = Field(description="The result's title.")
    url: str = Field(description="The result's URL — read it with http_get before citing.")
    snippet: str = Field(description="The engine's snippet (a lead, not a source).")


class WebSearchData(BaseModel):
    query: str = Field(description="The query that was searched.")
    results: list[WebSearchResult] = Field(description="Results, best first.")
    count: int = Field(description="How many results were returned.")


def build_websearch_tools() -> list:
    """The web-search tool set, or [] when the feature is off / ddgs absent."""
    if not config.websearch_enabled:
        return []
    try:
        from ddgs import DDGS  # noqa: F401 — presence probe; used lazily per call.
    except ImportError:
        log_warning(
            "websearch_enabled but the 'ddgs' package is missing — web search "
            "disabled. Install the optional extra (`uv sync --extra websearch`)."
        )
        return []

    @tool(
        description="Search the live web for pages relevant to a query.",
        instructions=(
            "Use when the answer needs current information beyond your knowledge and "
            "memory — news, releases, prices, docs you don't hold. Results are LEADS "
            "(title/url/snippet), not sources: pick the most promising and read it "
            "with http_get before answering, then cite the page you actually read. "
            "An empty result means the search found nothing — say so."
        ),
        show_result=True,
    )
    def web_search(
        query: Annotated[
            str, Field(min_length=2, description="What to search the web for.")
        ],
        max_results: Annotated[
            int, Field(default=5, ge=1, le=10, description="How many results to return.")
        ] = 5,
    ) -> ToolOutput[WebSearchData]:
        """Search the web and return result leads (title, url, snippet).

        Snippets are hints for choosing what to read, not quotable sources —
        fetch the page (http_get) before grounding a claim in it.
        """
        from ddgs import DDGS

        q = query.strip()
        try:
            with DDGS() as engine:
                raw = list(engine.text(q, max_results=max_results))
        except Exception as exc:  # noqa: BLE001 — a search failure is a tool failure, not a crash.
            log_warning(f"websearch: {type(exc).__name__}: {exc}")
            return fail(f"Web search failed ({type(exc).__name__}). Try again or answer without it.")

        results = [
            WebSearchResult(
                title=str(r.get("title") or ""),
                url=str(r.get("href") or r.get("url") or ""),
                snippet=str(r.get("body") or r.get("snippet") or ""),
            )
            for r in raw
            if r.get("href") or r.get("url")
        ]
        log_info(f"websearch: {q!r} -> {len(results)} result(s)")
        msg = f"Found {len(results)} result(s)." if results else "The search found nothing."
        return ok(msg, WebSearchData(query=q, results=results, count=len(results)))

    return [web_search]
