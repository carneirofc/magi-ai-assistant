"""Toolsets — named groups of the lead's tools that config can switch off.

`toolsets.disabled: [websearch, http]` in the config file drops those groups
from the lead at team build; everything else is unchanged. Gating a group
here is cosmetic for tools that are already config-gated (websearch,
storage, …) — it matters for the always-on ones (http, media, vision, …).
"""

from collections.abc import Sequence

from magi.core.config import config

# name -> what the group gives the lead (shown by `magi skills list`).
TOOLSETS: dict[str, str] = {
    "introspection": "inspect the team roster before delegating",
    "vision": "look at an image URL",
    "media": "send files/images/audio as attachments",
    "identity": "view/send the bot's own profile picture",
    "http": "fetch URLs and make explicit HTTP requests",
    "memory": "read (and without the curator, write) durable memory",
    "storage": "keep and recall files (storage_enabled)",
    "knowledge": "search the knowledge corpus (knowledge_enabled)",
    "websearch": "web search (websearch_enabled)",
    "reminders": "set and list reminders (reminders_enabled)",
    "mcp": "lead-attached MCP servers",
    "evolution": "propose prompt/tool changes + approved recipe tools",
    "session_search": "search past conversations (session_search_enabled)",
    "delegate": "hand a subtask to an isolated helper agent (delegation_enabled)",
    "terminal": "run shell commands in the user's sandbox (sandbox.backend)",
    "skills": "open, create, and patch SKILL.md skills",
    "extensions": "persona-registered lead toolkits and Python skills' tools",
    "thinking": "toggle model thinking at runtime",
}


def toolset[T](name: str, tools: Sequence[T]) -> list[T]:
    """`tools`, or nothing when the `name` group is disabled in config."""
    if name not in TOOLSETS:
        raise KeyError(f"unknown toolset {name!r}")
    return [] if name in config.toolsets.disabled else list(tools)


def unknown_disabled() -> list[str]:
    """Names in `toolsets.disabled` that match no toolset (likely typos)."""
    return [n for n in config.toolsets.disabled if n not in TOOLSETS]
