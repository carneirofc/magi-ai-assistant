"""Title pass — a short conversation title from its opening exchange.

Lives in the agent layer because it needs a model; the API channel receives it
as an injected `TitleFn` (the mood-pass seam). One cheap member-model call, no
memory touched — this is labeling, not conversation. Output is clamped and
stripped defensively; any failure returns None and the caller keeps its
client-derived title, so titling can never break anything.
"""

from typing import Optional

from agno.agent import Agent
from agno.utils.log import log_info, log_warning
from agno.utils.message import get_text_from_message

from magi.agent.model import build_member_model

# Long enough for a good label, short enough for a session rail.
_TITLE_MAX = 48

_SYSTEM = """You title conversations. Given the opening of a chat, answer with a
short title for it: at most six words, no quotes, no trailing punctuation, the
conversation's own language. Answer with the title only."""


def _clean(raw: str) -> Optional[str]:
    title = raw.strip().strip("\"'").strip()
    # A model that rambled (multi-line, way over budget) is worse than the
    # client's derived title — reject rather than truncate mid-sentence.
    if not title or "\n" in title or len(title) > _TITLE_MAX * 2:
        return None
    return title[:_TITLE_MAX]


def build_title_pass():
    """An async `TitleFn`: opening text -> a short title, or None on any failure."""
    agent = Agent(
        name="TitlePass",
        model=build_member_model(),
        system_message=_SYSTEM,
        markdown=False,
        telemetry=False,
    )
    log_info(f"TitlePass ready: model={getattr(agent.model, 'id', '?')}")

    async def title(text: str) -> Optional[str]:
        try:
            resp = await agent.arun(input=text[:2000])
            raw = get_text_from_message(resp.content) if resp.content else ""
        except Exception as exc:  # noqa: BLE001 — titling must never break anything.
            log_warning(f"title pass failed: {type(exc).__name__}: {exc}")
            return None
        return _clean(raw)

    return title
