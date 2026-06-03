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

from agent.model import ModelsEnum, build_model
from core.config import config
from core.db import get_db
from core.prompts import load_prompt


from agent.members.assistant import build_assistant
from agent.members.researcher import build_researcher

def build_team(
    *,
    db: BaseDb | None = None,
) -> Team:

    model_ollama = build_model(model_id=ModelsEnum.OLLAMA_GEMMA_4_26B)
    members = [builder(model_ollama) for builder in [build_assistant, build_researcher]]
    
    lead_instructions = load_prompt("team/lead.md", config.system_prompt)
    log_info(
        f"building team 'ChatbotTeam': members={[m.name for m in members]}, "
        f"lead_prompt={len(lead_instructions)} chars, db={'injected' if db else 'default'}, "
        f"history=True (n=10)"
    )
    return Team(
        name="ChatbotTeam",
        model=model_ollama,  # lead / router brain
        members=members,
        instructions=lead_instructions,
        db=db or get_db(),
        add_history_to_context=True,
        num_history_runs=10,
        update_memory_on_run=True,
        markdown=True,
        telemetry=False,
    )
