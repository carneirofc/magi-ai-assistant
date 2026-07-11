"""Specialist team — multi-agent routing.

A Team has a lead model that reads each member's `role`, routes the message to the
right specialist (or coordinates several), then merges their work into one reply
in its own voice. Drop-in for a single Agent: `DiscordClient(team=build_team())`.

Members live in `magi/agent/members/` and are listed in `MEMBER_BUILDERS`. Everything
here is injectable (model via config, db via arg) so the team is testable and
reconfigurable.
"""

from collections.abc import Callable, Sequence
from typing import Annotated, Optional

from agno.agent import Agent
from agno.db.base import BaseDb
from agno.models.base import Model
from agno.team import Team
from agno.tools import tool
from agno.utils.log import log_info
from pydantic import BaseModel, Field

from magi.agent.hooks import tool_call_hook
from magi.agent.members import MEMBER_BUILDERS
from magi.agent.model import build_lead_model, build_member_model
from magi.agent.tools.http import HTTP_TOOLS
from magi.agent.tools.identity import build_identity_tools
from magi.agent.tools.knowledge import build_knowledge_tools
from magi.agent.tools.media import MEDIA_TOOLS
from magi.agent.tools.memory import build_memory_tools
from magi.agent.tools.outputs import ToolOutput, ok
from magi.agent.tools.storage import build_storage_tools
from magi.agent.tools.thinking import build_thinking_tools
from magi.agent.tools.vision import VISION_TOOLS
from magi.core.config import config
from magi.core.db import get_db
from magi.core.items import build_item_archive_from_config
from magi.core.knowledge import KnowledgeStore, build_knowledge_from_config
from magi.core.memory import MemoryManager
from magi.core.prompts import load_prompt
from magi.core.storage import build_object_store_from_config


class IntrospectionMember(BaseModel):
    """One specialist on the roster, as seen by the lead during introspection."""

    name: str = Field(description="Member id the lead routes to.")
    role: str = Field(description="What the member specializes in.")


class IntrospectionData(BaseModel):
    """Structured roster payload returned by `agent_introspection`."""

    reason: str | None = Field(description="Why the lead introspected, if it said.")
    lead_model: str = Field(description="Model id backing the lead.")
    members: list[IntrospectionMember] = Field(description="The specialist roster.")
    text: str = Field(description="Human-readable rendering of the roster.")


def _build_introspection_tool(lead: Model, members):
    """A tool that lets the lead inspect its own roster before delegating."""

    @tool(
        name="agent_introspection",
        description="Introspect self and the team's members and tools. Use to decide WHO to call for WHAT.",
        instructions=(
            "Use before delegation when routing is ambiguous or when you need the roster of specialist members. "
            "Optional reason should briefly state what routing decision you are making."
        ),
        # No show_result: this is the lead's *internal* routing aid. Unlike the
        # member data tools (whose show_result output is drained and re-summarized
        # inside a member's stream), this tool runs at the team/lead level, so its
        # result is yielded straight into the lead's user-facing stream. With
        # show_result=True agno dumps the raw `str(ToolOutput(...))` envelope —
        # `success=True status='ok' … data=IntrospectionData(…)` — as Alyssa's
        # reply. The lead still receives the roster in its message history either
        # way; it just no longer leaks to the user.
    )
    def agent_introspection(
        reason: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Brief reason for introspecting the team roster and tools.",
            ),
        ] = None,
    ) -> ToolOutput[IntrospectionData]:
        lines = [
            "Self-introspection:",
            f"Reason: {reason or 'not provided'}",
            f"Lead model: {lead.id}",
            "Team members:",
        ]
        lines += [f"- {m.name}: {m.role}" for m in members]
        return ok(
            "Team introspection completed.",
            IntrospectionData(
                reason=reason,
                lead_model=lead.id,
                members=[IntrospectionMember(name=m.name, role=m.role) for m in members],
                text="\n".join(lines),
            ),
        )

    return agent_introspection


def build_team(
    memory: MemoryManager,
    db: Optional[BaseDb] = None,
    member_builders: Optional[Sequence[Callable[[Model], Agent]]] = None,
    knowledge: Optional[KnowledgeStore] = None,
) -> Team:
    """Assemble the chatbot team: a multimodal lead routing to specialist members.

    `memory` is injected so the lead's memory tools are bound to it (no globals).
    `member_builders` defaults to the full registry; a channel that can't host a
    specialist (e.g. the Discord member outside Discord) passes a trimmed list.
    `knowledge` is the RAG store backing the search tool; the composition root
    injects the same instance it also hands to `ConversationService` for context
    auto-injection, so one store powers both. When None (e.g. a direct/test call)
    it's built from config — a no-op when the feature is off.
    """
    lead = build_lead_model()
    member_model = build_member_model()
    builders = MEMBER_BUILDERS if member_builders is None else list(member_builders)
    members = [build(member_model) for build in builders]
    # agno copies the team's tool_hooks onto the *team-level* tools only —
    # members never inherit them, so their tool calls (wiki lookups, http_get,
    # …) ran invisibly. Attach the same hook to every member: each call is
    # logged with args/timing/result and failures become member-visible text.
    for m in members:
        if not m.tool_hooks:
            m.tool_hooks = [tool_call_hook]

    # Durable object storage — the model's byte archive (local filesystem or an
    # S3-compatible bucket, per config.storage_backend). Gated by config and
    # degrades to nothing when off / boto3 absent / backend unreachable, so a
    # deployment without it (or with its backend down) still boots cleanly.
    storage_tools: list = []
    if config.storage_enabled:
        store = build_object_store_from_config()
        if store is not None:
            try:
                store.ensure_bucket()
            except Exception as exc:  # noqa: BLE001 — a down backend must not abort startup.
                log_info(f"storage: backend check skipped ({type(exc).__name__}: {exc})")
            # The item archive (None unless enabled) adds meaning-based file search.
            storage_tools = build_storage_tools(store, memory, build_item_archive_from_config())
            log_info(
                f"storage: ENABLED ({len(storage_tools)} tools, backend={config.storage_backend})"
            )

    # Knowledge layer (global RAG corpus) — read-only reference the lead can search.
    # Gated by config; degrades to nothing when off / Qdrant down / embeddings absent,
    # so a deployment without it still boots cleanly.
    knowledge_tools: list = []
    if knowledge is None:
        knowledge = build_knowledge_from_config()
    if knowledge is not None:
        # The store is searcher, tagger AND indexer, so it powers the read,
        # tag-write, and save (chat-derived ingest) tools alike.
        knowledge_tools = build_knowledge_tools(knowledge, knowledge, knowledge)
        log_info(
            f"knowledge: ENABLED ({len(knowledge_tools)} tool(s), "
            f"collection={config.knowledge_collection})"
        )

    # The lead's prompt is its soul (who Alyssa is) followed by the operational
    # router (how she delegates and wields tools). SOUL.md establishes identity
    # first so persona stays primary; lead.md supplies the hard rules and routing.
    instructions = "\n\n".join((load_prompt("team/SOUL.md"), load_prompt("team/lead.md")))
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
        # magi/core/memory) and the lead writes back only via the memory tools. So we
        # turn off agno's automatic history-stuffing and memory extraction.
        add_history_to_context=False,
        update_memory_on_run=False,
        # List each member's tool names in the lead's <team_members> block, so
        # routing can match a request to a member's actual capabilities (e.g.
        # danbooru_* → Prompt Artist), not just its prose role.
        add_member_tools_to_context=True,
        markdown=True,
        telemetry=False,
        store_events=True,
        store_member_responses=True,
        # Don't re-emit each member's granular run events (tool calls, reasoning,
        # run lifecycle) up through the delegate tool. They flood the logs — the
        # tool hook drains and logs the delegate's stream (see magi/agent/hooks.py) —
        # and add nothing: a member's own tool calls are already logged by the
        # tool_hook we attach to every member above. The member's answer (its
        # RunContent deltas) still streams through; only the noise is dropped.
        stream_member_events=False,
        # Observability + robustness: log every member/tool call and convert a
        # raising tool into a lead-visible error instead of aborting the run.
        tool_hooks=[tool_call_hook],
        # Bound runaway delegation loops (lead → member → lead → …).
        tool_call_limit=config.tool_call_limit,
        tools=[
            _build_introspection_tool(lead, members),
            # Lead is multimodal; this lets it pull an image URL into its own
            # context and actually look, instead of guessing from the link text.
            *VISION_TOOLS,
            # Deliver a URL's actual bytes to the user as an attachment (image,
            # audio, file) instead of pasting a link (see magi/core/media.py outbox).
            *MEDIA_TOOLS,
            # The bot's own profile picture: look at it, or send it to the user
            # (bound to this run's identity; empty-safe when no picture is set).
            *build_identity_tools(memory),
            # Read a URL (http_get) and perform an explicit user-described request
            # (http_request) without round-tripping through a member.
            *HTTP_TOOLS,
            *build_memory_tools(memory),
            # Durable byte archive: keep a file/image for later, recall by
            # reference (empty unless storage is enabled).
            *storage_tools,
            # Search the global knowledge corpus (empty unless the feature is on).
            *knowledge_tools,
            # Bound to the live model objects: members all share `member_model`,
            # so one mutation flips the whole team.
            *build_thinking_tools([lead, member_model]),
        ],
    )
