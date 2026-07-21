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
from agno.utils.log import log_info, log_warning
from agno.utils.message import get_text_from_message

from magi.agent.model import build_member_model
from magi.core.config import config
from magi.core.memory import CurateFn, CurationInput, CurationResult, FactOp, PromptProposal
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


def _parse_proposal(data: dict) -> Optional[PromptProposal]:
    """The optional `proposal` object -> a `PromptProposal`, or None.

    All three fields must be non-empty strings; anything less is dropped (the
    rails in the store would reject it anyway — this just avoids filing noise)."""
    raw = data.get("proposal")
    if not isinstance(raw, dict):
        return None
    target = _str_field(raw, "target")
    text = _str_field(raw, "text")
    rationale = _str_field(raw, "rationale")
    if not (target and text and rationale):
        return None
    return PromptProposal(target=target, text=text, rationale=rationale)


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
        proposal=_parse_proposal(data),
    )


def file_curator_proposal(store, proposal: PromptProposal) -> bool:
    """File one curator proposal into the evolution queue; never raises.

    The same rails as every proposal (allowlisted target, capped queue) — a
    violation or any store failure degrades to a warning, because curation must
    never break a chat. Returns whether the proposal was queued."""
    try:
        current = ""
        try:
            current = load_prompt(proposal.target)
        except Exception:  # noqa: BLE001 — no file yet; a skill's inline default still shows.
            from magi.agent.skills import find_skill_by_prompt_path

            skill = find_skill_by_prompt_path(proposal.target)
            current = skill.prompt if skill is not None else ""
        queued = store.propose(
            "prompt",
            proposal.target,
            proposal.text,
            proposal.rationale,
            source="curator",
            current_text=current,
        )
    except Exception as exc:  # noqa: BLE001 — full queue / bad target / IO: log and move on.
        log_warning(f"curator: proposal for {proposal.target!r} not filed ({exc})")
        return False
    log_info(f"curator: filed evolution proposal {queued.id} for {proposal.target!r}")
    return True


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

    # The curator's escalation path: when evolution is on it may FILE a prompt
    # proposal (source="curator") under the same rails as the lead's propose
    # tools — allowlist (incl. registered skills), capped queue, operator
    # decision. Off => proposals parse but are dropped with a log line.
    evolution_store = None
    if config.evolution_enabled:
        from pathlib import Path

        from magi.agent.skills import proposable_skill_targets
        from magi.core.evolution import EvolutionStore

        evolution_store = EvolutionStore(
            Path(config.memory_dir),
            proposable=[*config.evolution_proposable, *proposable_skill_targets()],
        )

    async def curate(inp: CurationInput) -> CurationResult:
        resp = await agent.arun(input=_format_input(inp))
        text = get_text_from_message(resp.content) if resp.content else ""
        result = _parse(text)
        if result.proposal is not None:
            if evolution_store is not None:
                file_curator_proposal(evolution_store, result.proposal)
            else:
                log_info(
                    "MemoryCurator: dropped a proposal for "
                    f"{result.proposal.target!r} (evolution disabled)"
                )
        if result.is_empty:
            log_info("MemoryCurator: no durable change this turn")
        return result

    return curate
