"""Memory curator — the post-turn pass that decides what durable memory to keep.

Lives in the agent layer because it needs a model; `core/memory` receives it as
an injected `CurateFn` (the same seam the summarizers use, see agent/summarizer).
Given one finished turn plus the user's current durable facts (each tagged with an
id) and the persona, it returns a `CurationResult`: a list of per-fact operations
(ADD/UPDATE/DELETE) when something durable changed, an optional episode at a
natural close, an optional persona adjustment. The common case is "nothing
changed" — no operations, both other fields None.

Parsing is defensive: the model returns JSON, but any malformed output degrades
to a no-op (never raises), and the manager swallows failures anyway — curation
must never break a chat.
"""

import json
import re
from typing import Optional

from agno.agent import Agent
from agno.utils.log import log_info
from agno.utils.message import get_text_from_message

from agent.model import build_member_model
from core.memory import CurateFn, CurationInput, CurationResult, FactOp

_SYSTEM = (
    "You are the memory curator for a conversational assistant. After each turn you "
    "decide what DURABLE memory to keep about the user — nothing is saved unless you "
    "save it.\n\n"
    "You are given the user's latest message, the assistant's reply, the current "
    "durable facts (each on its own line as `[id] fact`, may be empty), and the "
    "assistant's persona.\n\n"
    "Revise the facts ONE AT A TIME. Return ONLY a JSON object with exactly these keys:\n"
    '- "operations": a list of changes to the durable facts, or [] when nothing '
    "durable changed. Each item is an object:\n"
    '    {"op": "add", "text": "<the new fact>"}            — a new durable fact\n'
    '    {"op": "update", "id": "<id>", "text": "<revised>"} — replace an existing fact\n'
    '    {"op": "delete", "id": "<id>"}                      — drop a fact now wrong\n'
    "  Use the ids exactly as shown. ADD only facts not already present; UPDATE when a "
    "known fact changed or was refined; DELETE when one is contradicted or no longer "
    "true. Touch only what THIS turn changed — never re-emit unchanged facts. Store only "
    "stable, reusable facts (name, preferences, ongoing projects, stack choices, "
    "recurring constraints); keep each fact one concise self-contained line. Never store "
    "passing chatter, one-off details, transient moods, or sensitive data the user did "
    "not ask you to keep.\n"
    '- "episode": a one-line summary of what happened this turn, or null. Only at a '
    "natural close or after a notable outcome — usually null.\n"
    '- "persona": a single general, lasting behaviour rule learned this turn (a tone '
    "that landed, a habit to adopt or avoid), or null. It must be a general rule that "
    "applies for everyone, not a user-specific fact and not a one-off reaction — almost "
    "always null.\n\n"
    "Default to empty: most turns teach nothing durable (operations: []). Output the "
    "JSON object only — no prose, no markdown, no code fences."
)

# First {...} block in the output; the model may wrap it in prose despite the rule.
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _format_input(inp: CurationInput) -> str:
    return (
        f"## Current durable facts\n{inp.current_facts or '(empty)'}\n\n"
        f"## Persona\n{inp.persona or '(none)'}\n\n"
        f"## This turn\nUser: {inp.user_message}\nAssistant: {inp.assistant_reply}"
    )


def _str_field(data: dict, key: str) -> Optional[str]:
    value = data.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _parse_op(item: object) -> Optional[FactOp]:
    """One operation dict -> a `FactOp`, or None when it's malformed/unusable.

    Drops anything that can't be applied: an unknown verb, an add/update with no
    text, or an update/delete with no id. The manager skips unknown ids anyway.
    Each branch compares `op` to a literal so the verb narrows without a cast."""
    if not isinstance(item, dict):
        return None
    op = item.get("op")
    raw_id = item.get("id")
    fact_id = raw_id.strip() if isinstance(raw_id, str) and raw_id.strip() else None
    text = _str_field(item, "text")
    if op == "add":
        return FactOp(op="add", text=text) if text else None
    if op == "update":
        return FactOp(op="update", fact_id=fact_id, text=text) if (fact_id and text) else None
    if op == "delete":
        return FactOp(op="delete", fact_id=fact_id) if fact_id else None
    return None


def _parse(text: str) -> CurationResult:
    """Parse the curator's JSON into a result. Malformed output => no-op."""
    if not text:
        return CurationResult()
    match = _JSON_RE.search(text)
    if not match:
        return CurationResult()
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return CurationResult()
    if not isinstance(data, dict):
        return CurationResult()
    raw_ops = data.get("operations")
    operations = tuple(
        op for op in (_parse_op(item) for item in raw_ops) if op is not None
    ) if isinstance(raw_ops, list) else ()
    return CurationResult(
        operations=operations,
        episode=_str_field(data, "episode"),
        persona_adjustment=_str_field(data, "persona"),
    )


def build_memory_curator() -> CurateFn:
    """An async `CurateFn`: one finished turn -> the durable-memory changes to apply."""
    agent = Agent(
        name="MemoryCurator",
        model=build_member_model(),
        system_message=_SYSTEM,
        markdown=False,
        telemetry=False,
    )
    log_info(f"MemoryCurator ready: model={getattr(agent.model, 'id', '?')}")

    async def curate(inp: CurationInput) -> CurationResult:
        resp = await agent.arun(input=_format_input(inp))
        text = get_text_from_message(resp.content) if resp.content else ""
        result = _parse(text)
        if result.is_empty:
            log_info("MemoryCurator: no durable change this turn")
        return result

    return curate
