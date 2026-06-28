"""LLM session summarizer — fold raw memory into compact form.

The deliberate memory (core/memory) caps the live window; without summarization
the turns that roll out of it are lost. This callable folds those evicted
conversation turns into a rolling session summary. (Durable per-user memory is
owned by the curator — see agent/curator.py — not summarized here.)

It lives in the agent layer because it needs a model. `core/memory` stays
model-free and receives it only as an injected async callable (`SummarizeFn`).
"""

from agno.agent import Agent
from agno.utils.log import log_info
from agno.utils.message import get_text_from_message

from magi.agent.model import build_member_model
from magi.core.memory.manager import SummarizeFn

_SESSION_SYSTEM = (
    "You compress chat history. Given a prior running summary (may be empty) and "
    "newer raw conversation turns, write an updated running summary in a few short "
    "lines: what the user wanted, key facts surfaced, and how it's going. No "
    "preamble, no markdown headings — just the summary text."
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
