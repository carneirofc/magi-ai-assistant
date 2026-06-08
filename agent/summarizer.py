"""LLM summarizers — fold raw memory into compact form.

The deliberate memory (core/memory) caps the live window and lets durable facts
pile up; both would otherwise grow without bound. These callables compress the
overflow: one folds evicted conversation turns into a rolling session summary,
the other condenses accumulated long-term facts into a deduplicated profile.

They live in the agent layer because they need a model. `core/memory` stays
model-free and receives them only as injected async callables (`SummarizeFn`).
"""

from agno.agent import Agent
from agno.utils.log import log_info
from agno.utils.message import get_text_from_message

from agent.model import build_member_model
from core.memory.manager import SummarizeFn

_SESSION_SYSTEM = (
    "You compress chat history. Given a prior running summary (may be empty) and "
    "newer raw conversation turns, write an updated running summary in a few short "
    "lines: what the user wanted, key facts surfaced, and how it's going. No "
    "preamble, no markdown headings — just the summary text."
)

_LONG_TERM_SYSTEM = (
    "You maintain a durable user profile. Given a list of facts learned about one "
    "user over time, merge them into a concise, deduplicated profile grouped by "
    "topic (preferences, projects, identity, recurring needs). Drop redundancy and "
    "resolve contradictions in favor of the most recent. No preamble — just the profile."
)


def _build(name: str, system: str) -> SummarizeFn:
    agent = Agent(
        name=name,
        model=build_member_model(),
        system_message=system,
        markdown=False,
        telemetry=False,
    )
    log_info(f"{name} ready: model={getattr(agent.model, 'id', '?')}")

    async def summarize(text: str) -> str:
        resp = await agent.arun(input=text)
        return get_text_from_message(resp.content) if resp.content else ""

    return summarize


def build_session_summarizer() -> SummarizeFn:
    """An async callable folding `(old summary + raw turns) -> updated summary`."""
    return _build("SessionSummarizer", _SESSION_SYSTEM)


def build_long_term_summarizer() -> SummarizeFn:
    """An async callable folding accumulated long-term facts into one profile."""
    return _build("LongTermSummarizer", _LONG_TERM_SYSTEM)
