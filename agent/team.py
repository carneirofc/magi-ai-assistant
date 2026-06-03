"""Specialist team skeleton — multi-agent routing.

A Team has a lead model that reads each member's `role` and routes the message
to the right specialist (or coordinates several). It is a drop-in replacement for
a single Agent: DiscordClient(team=build_team()).

Members live in `agent/members/` (one file each) and are listed in TEAM_MEMBERS.
Everything here is injectable (model/db) so the team is testable and reconfigurable.
"""

from agno.db.base import BaseDb
from agno.models.base import Model
from agno.team import Team
from agno.utils.log import log_info

from agent.model import ModelDefinition, ModelProviderEnum,  build_model
from core.config import config
from core.db import get_db
from core.prompts import load_prompt


from agent.members.assistant import build_assistant
from agent.members.researcher import build_researcher


def build_team(*, db: BaseDb | None = None) -> Team:

    model_ollama_lfm_latest = ModelDefinition(
        has_tools=True,
        provider=ModelProviderEnum.OLLAMA,
        model_id="gemma4:e4b" #"gemma4:e4b",
    )

    model_lead = build_model(model=model_ollama_lfm_latest)
    model_members = build_model(model=model_ollama_lfm_latest)

    members = [
        builder(model_members)
        for builder in [
            build_assistant,
            build_researcher
        ]
    ]

    lead_instructions = load_prompt("team/lead.md")
    log_info(
        f"building team 'ChatbotTeam': members={[m.name for m in members]}, "
        f"lead_prompt={len(lead_instructions)} chars, db={'injected' if db else 'default'}, "
        f"history=True (n=10)"
    )

    from agno.tools import tool

    @tool(
        name="agent_introspection",
        description="Introspect self and the team's own members and tools. Use to decide WHO to call for WHAT.",
    )
    def introspect_tools() -> str:
        """Return a list of team members and tools, with descriptions."""
        introspection = "Self-introspection:\n"
        introspection += f"Model lead: {model_lead}\n"
        introspection += "Team members:\n"
        for member in members:
            introspection += f"- {member.name}: {member.role}\n"
        return introspection

    return Team(
        name="ChatbotTeam",
        model=model_lead,  # lead / router brain — must support tools
        members=members,
        instructions=lead_instructions,
        db=db or get_db(),
        add_history_to_context=True,
        num_history_runs=10,
        update_memory_on_run=True,  # only if model support tools
        markdown=True,
        telemetry=False,
        tools=[introspect_tools],
    )
