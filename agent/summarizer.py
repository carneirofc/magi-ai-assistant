"""History summarizer — folds evicted conversation turns into one episodic line.

The deliberate memory (core/memory) caps the live window; turns that roll out of
it would otherwise be lost. When auto-summarize is enabled, those evicted turns
are buffered and handed here in batches, and a lightweight agent compresses the
batch into a single episode so the gist survives.

It lives in the agent layer because it needs a model. `core/memory` stays
model-free and receives this only as an injected async callable (`SummarizeFn`).
"""

from agno.agent import Agent
from agno.utils.log import log_info
from agno.utils.message import get_text_from_message

from agent.model import build_member_model
from core.memory.manager import SummarizeFn

_SYSTEM = (
    "You compress chat history. Given raw conversation turns, write ONE concise "
    "line (max ~30 words) capturing what the user wanted and how it went. No "
    "preamble, no markdown — just the single line."
)


def build_summarizer() -> SummarizeFn:
    """An async `turns -> one-line summary` callable, backed by the member model."""
    agent = Agent(
        name="Summarizer",
        model=build_member_model(),
        system_message=_SYSTEM,
        markdown=False,
        telemetry=False,
    )
    log_info(f"summarizer ready: model={getattr(agent.model, 'id', '?')}")

    async def summarize(turns: str) -> str:
        resp = await agent.arun(input=f"Conversation turns:\n{turns}")
        return get_text_from_message(resp.content) if resp.content else ""

    return summarize
