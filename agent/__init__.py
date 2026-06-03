"""Agent package — public builders.

Import from here (`from agent import build_discord_agent`) rather than the
internal module split, so the layout can change without touching callers.
"""

from agent.agents import build_agent, build_discord_agent, build_stateless_agent
from agent.model import build_model
from agent.team import build_team

__all__ = [
    "build_model",
    "build_agent",
    "build_stateless_agent",
    "build_discord_agent",
    "build_team",
]
