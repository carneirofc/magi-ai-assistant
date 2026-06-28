"""Seanime specialist — the user's local anime media server.

The lead routes anime-library work here: what's in the library, watch progress,
missing episodes, the airing schedule, AniList lookups via Seanime, and marking
episodes watched. Its `role` (prompts/team/seanime.md) tells the lead when to
pick it; its tools call the Seanime HTTP API at `config.seanime_base_url`.

It also carries the media-delivery tool: every Seanime result already holds a
proxied cover URL, so the member attaches the actual image itself (staging it in
the per-run outbox) instead of handing the URL back to the lead to re-fetch —
killing the parse-the-prose-and-refetch hop that the verbatim-URL rule existed
to patch.

Two variants, one role on the team: by default the specialist is built from the
hand-rolled HTTP tools above; when `config.seanime_use_mcp` is set it is instead
built from Seanime's own read-only MCP server (see agent/tools/seanime_mcp.py).
`build_seanime_specialist` picks at build time, so the team always has exactly
one anime member and routing never sees two.
"""

from agno.agent import Agent
from agno.models.base import Model

from agent.tools.media import MEDIA_TOOLS
from agent.tools.seanime import SEANIME_TOOLS
from core.config import config
from core.prompts import load_prompt


def build_seanime_specialist(model: Model) -> Agent:
    """The anime/manga specialist — MCP-backed when `config.seanime_use_mcp`,
    else the direct-HTTP one."""
    if config.seanime_use_mcp:
        return build_seanime_mcp_specialist(model)
    return Agent(
        name="Seanime",
        role=load_prompt("team/seanime.md"),
        model=model,
        tools=[*SEANIME_TOOLS, *MEDIA_TOOLS],
    )


def build_seanime_mcp_specialist(model: Model) -> Agent:
    """The anime/manga specialist backed by Seanime's read-only MCP server.

    Same team seat as the direct variant, narrower surface: search, media
    details, the user's collection, and viewer stats — no library files,
    schedules, or progress mutations. Its tools are discovered from the server
    at connect time; the MEDIA_TOOLS let it still deliver a cover image when a
    result carries one.
    """
    from agent.tools.seanime_mcp import build_seanime_mcp_tools

    return Agent(
        name="Seanime",
        role=load_prompt("team/seanime_mcp.md"),
        model=model,
        tools=[build_seanime_mcp_tools(), *MEDIA_TOOLS],
    )
