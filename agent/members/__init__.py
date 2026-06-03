"""Team member registry.

Each specialist lives in its own module and exposes a `build_<name>(model)`
factory. `TEAM_MEMBERS` is the ordered list the team assembles from.

To add a member: drop a new module here, then append its builder to TEAM_MEMBERS.
"""

from agent.members.assistant import build_assistant
from agent.members.researcher import build_researcher

# Specialists the team's lead routes between.
# TEAM_MEMBERS = [build_assistant, build_researcher]

__all__ = ["build_assistant", "build_researcher"]
