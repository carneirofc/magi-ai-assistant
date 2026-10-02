"""`search_sessions` — look back through what was said in past conversations.

Bound to the injected `SessionIndex` and `MemoryManager`: the user is ALWAYS
the current memory scope (never a tool argument), so one user can never
search another's conversations.
"""

from typing import Annotated

from agno.tools import tool
from agno.tools.function import Function
from pydantic import BaseModel, Field

from magi.agent.tools.outputs import ToolOutput, fail, ok
from magi.core.memory import MemoryManager
from magi.core.session_index import SessionIndex


class SessionHitRow(BaseModel):
    session_id: str
    role: str = Field(description="Who said it: 'user' or 'assistant'.")
    when: str = Field(description="UTC timestamp of the turn.")
    snippet: str = Field(description="Matching excerpt; [brackets] mark the hits.")
    this_session: bool = Field(description="True when it is from the current conversation.")


class SessionSearchData(BaseModel):
    query: str
    hits: list[SessionHitRow]


def build_session_search_tools(index: SessionIndex, memory: MemoryManager) -> list[Function]:
    @tool(
        description="Search the user's past conversations for what was actually said.",
        instructions=(
            "Use when the user refers to something from an earlier conversation that "
            "isn't in your memory context ('what did we decide about…', 'that script "
            "you gave me last week'). Pass a few distinctive keywords, not a sentence. "
            "Only this user's conversations are searched."
        ),
        show_result=True,
    )
    def search_sessions(
        query: Annotated[str, Field(min_length=2, description="Distinctive keywords.")],
        limit: Annotated[int, Field(default=8, ge=1, le=25)] = 8,
    ) -> ToolOutput[SessionSearchData]:
        scope = memory.scope()
        try:
            hits = index.search(user_id=scope.user_id, query=query, limit=limit)
        except Exception as exc:  # noqa: BLE001 — a search failure is a tool result.
            return fail(f"Session search failed: {type(exc).__name__}: {exc}")
        rows = [
            SessionHitRow(
                session_id=h.session_id,
                role=h.role,
                when=h.ts,
                snippet=h.snippet,
                this_session=h.session_id == scope.session_id,
            )
            for h in hits
        ]
        message = f"{len(rows)} match(es)." if rows else "No past conversation matches that."
        return ok(message, SessionSearchData(query=query, hits=rows))

    return [search_sessions]
