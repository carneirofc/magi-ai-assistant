"""Memory curator — the post-turn pass that decides what durable memory to keep.

Lives in the agent layer because it needs a model; `magi/core/memory` receives it as
an injected `CurateFn` (the same seam the summarizers use, see magi/agent/summarizer).
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

from magi.agent.model import build_member_model
from magi.core.memory import CurateFn, CurationInput, CurationResult, FactOp
from magi.core.prompts import load_prompt

# The what-to-remember policy is a prompt file (prompts/curation.md), loaded via
# the overlay seam so a persona can supply its own policy without editing the
# engine. The engine ships a generic default. It's read lazily in
# build_memory_curator() — after the entrypoint has set any overlay — not at
# import time.

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
        system_message=load_prompt("curation.md"),
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
