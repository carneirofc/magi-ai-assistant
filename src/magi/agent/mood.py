"""Mood pass — predicts the reply's delivery mood before the reply is written.

Lives in the agent layer because it needs a model; `magi/core/conversation`
receives it as an injected `MoodFn` (the same seam the summarizers and the
curator use). Given the assembled run input (context + the user's message), it
returns one name from `config.mood_vocabulary` — always a valid name, so
downstream consumers (the avatar stage today, a TTS style later) never see free
text.

Reliability comes from constrained decoding, not parsing: the pass runs with an
`output_schema` whose `mood` field is a Literal over the vocabulary, which the
OpenAI-compatible path sends as `response_format: json_schema` — llama-server
enforces that with a grammar. Parsing stays defensive anyway (a proxy backend
may ignore response_format), and any failure degrades to the vocabulary's first
entry — the mood pass must never break a chat.
"""

import json
import re
from typing import Literal

import pydantic
from agno.agent import Agent
from agno.utils.log import log_info, log_warning

from magi.agent.model import build_member_model
from magi.core.config import config
from magi.core.conversation import MoodFn
from magi.core.prompts import load_prompt

# First {...} block in a text reply; only used when the backend returned a string
# instead of the parsed schema instance.
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _render_vocabulary(vocabulary: dict[str, str]) -> str:
    lines = ["## Mood vocabulary (pick exactly one `mood`)"]
    lines += [f"- {name}: {description}" for name, description in vocabulary.items()]
    return "\n".join(lines)


def _extract_mood(content: object, valid: frozenset[str]) -> str | None:
    """The vocabulary mood carried by a run's content, or None.

    Handles both shapes agno can hand back: the parsed `output_schema` instance
    (the structured-output path) and a raw string (a backend that ignored
    `response_format`). Anything outside the vocabulary is rejected."""
    mood = getattr(content, "mood", None)
    if isinstance(mood, str) and mood in valid:
        return mood
    if isinstance(content, str):
        match = _JSON_RE.search(content)
        if match:
            try:
                data = json.loads(match.group(0))
            except (json.JSONDecodeError, ValueError):
                return None
            value = data.get("mood") if isinstance(data, dict) else None
            if isinstance(value, str) and value in valid:
                return value
    return None


def build_mood_pass() -> MoodFn:
    """An async `MoodFn`: assembled run input -> one vocabulary mood name.

    The vocabulary is snapshotted at build time (it's config, set at the
    entrypoint); its first entry is the fallback for any failure."""
    vocabulary = dict(config.mood_vocabulary)
    if not vocabulary:
        raise ValueError("mood_enabled needs a non-empty mood_vocabulary")
    valid = frozenset(vocabulary)
    fallback = next(iter(vocabulary))

    # `mood` as a Literal over the vocabulary: the JSON schema carries an enum,
    # which llama-server compiles to a grammar — the constrained decoding that
    # makes this pass reliable.
    mood_pick = pydantic.create_model("MoodPick", mood=(Literal[tuple(vocabulary)], ...))
    agent = Agent(
        name="MoodPass",
        model=build_member_model(),
        system_message=f"{load_prompt('mood.md')}\n\n{_render_vocabulary(vocabulary)}",
        output_schema=mood_pick,
        markdown=False,
        telemetry=False,
    )
    log_info(
        f"MoodPass ready: model={getattr(agent.model, 'id', '?')}, "
        f"vocabulary={sorted(valid)}, fallback={fallback!r}"
    )

    async def predict(run_input: str) -> str:
        try:
            resp = await agent.arun(input=run_input)
            mood = _extract_mood(resp.content, valid)
        except Exception as exc:  # noqa: BLE001 — the pass must never break a chat.
            log_warning(f"mood pass failed: {type(exc).__name__}: {exc}")
            mood = None
        if mood is None:
            log_warning(f"mood pass produced no usable mood; falling back to {fallback!r}")
            return fallback
        return mood

    return predict
