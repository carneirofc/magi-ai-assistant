"""Agent package — public builders.

Import from here rather than the internal module split, so the layout can change
without touching callers.
"""

from magi.agent.model import build_lead_model, build_member_model, build_model

__all__ = [
    "build_model",
    "build_lead_model",
    "build_member_model",
]
