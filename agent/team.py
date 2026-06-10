"""Specialist team — multi-agent routing.

A Team has a lead model that reads each member's `role`, routes the message to the
right specialist (or coordinates several), then merges their work into one reply
in its own voice. Drop-in for a single Agent: `DiscordClient(team=build_team())`.

Members live in `agent/members/` and are listed in `MEMBER_BUILDERS`. Everything
here is injectable (model via config, db via arg) so the team is testable and
reconfigurable.
"""

from collections.abc import Callable, Sequence
from typing import Optional

from agno.agent import Agent
from agno.db.base import BaseDb
from agno.models.base import Model
from agno.team import Team
from agno.tools import tool
from agno.tools.websearch import WebSearchTools
from agno.utils.log import log_info

from agent.hooks import tool_call_hook
from agent.members import MEMBER_BUILDERS
from agent.model import build_lead_model, build_member_model
from agent.tools.memory import build_memory_tools
from agent.tools.vision import VISION_TOOLS
from core.config import config
from core.db import get_db
from core.memory import MemoryManager
from core.prompts import load_prompt


def _build_introspection_tool(lead: Model, members):
    """A tool that lets the lead inspect its own roster before delegating."""

    @tool(
        name="agent_introspection",
        description="Introspect self and the team's members and tools. Use to decide WHO to call for WHAT.",
    )
    def agent_introspection(reason: Optional[str] = None) -> str:
        lines = [
            "Self-introspection:",
            f"Reason: {reason or 'not provided'}",
            f"Lead model: {lead.id}",
            "Team members:",
        ]
        lines += [f"- {m.name}: {m.role}" for m in members]
        return "\n".join(lines)

    return agent_introspection


def build_team(
    memory: MemoryManager,
    db: Optional[BaseDb] = None,
    member_builders: Optional[Sequence[Callable[[Model], Agent]]] = None,
) -> Team:
    """Assemble the chatbot team: a multimodal lead routing to specialist members.

    `memory` is injected so the lead's memory tools are bound to it (no globals).
    `member_builders` defaults to the full registry; a channel that can't host a
    specialist (e.g. the Discord member outside Discord) passes a trimmed list.
    """
    lead = build_lead_model()
    member_model = build_member_model()
    builders = MEMBER_BUILDERS if member_builders is None else list(member_builders)
    members = [build(member_model) for build in builders]

    instructions = load_prompt("team/lead.md")
    log_info(
        f"building team 'ChatbotTeam': lead={lead.id} (ctx={config.lead_num_ctx}, "
        f"temp={config.model_temperature}), member_model={member_model.id} "
        f"(ctx={config.member_num_ctx}), instructions={len(instructions)} chars, "
        f"db={'injected' if db else 'default'}"
    )
    for m in members:
        log_info(
            f"  member '{m.name}': model={getattr(m.model, 'id', '?')}, "
            f"tools={[getattr(t, 'name', type(t).__name__) for t in (m.tools or [])]}"
        )

    return Team(
        name="ChatbotTeam",
        model=lead,  # lead / router brain — must support tools
        members=members,
        instructions=instructions,
        db=db or get_db(),
        # Memory is handled deliberately, not by the framework: we inject our own
        # short-term window + long-term + episodic + persona per run (see
        # core/memory) and the lead writes back only via the memory tools. So we
        # turn off agno's automatic history-stuffing and memory extraction.
        add_history_to_context=False,
        update_memory_on_run=False,
        markdown=True,
        telemetry=False,
        # Observability + robustness: log every member/tool call and convert a
        # raising tool into a lead-visible error instead of aborting the run.
        tool_hooks=[tool_call_hook],
        # Bound runaway delegation loops (lead → member → lead → …).
        tool_call_limit=config.tool_call_limit,
        tools=[
            _build_introspection_tool(lead, members),
            WebSearchTools(backend="duckduckgo"),
            # Lead is multimodal; this lets it pull an image URL into its own
            # context and actually look, instead of guessing from the link text.
            *VISION_TOOLS,
            *build_memory_tools(memory),
        ],
    )
