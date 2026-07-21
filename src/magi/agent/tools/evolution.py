"""Evolution tools — how the assistant PROPOSES its own changes.

Two tools, both queue-only (see magi/core/evolution.py — nothing the model
does here touches a prompt file or registers a tool): propose a revision to an
allowlisted operating prompt, or propose a new declarative HTTP tool. The
operator reviews the queue in the admin UI; approved changes apply on restart.

The contract language matters: the model must understand this is a REQUEST to
grow, with a human decision in between — so the tools are explicit about what
happens (queued, reviewed, maybe applied later) and the failure messages
surface the rails (target not allowlisted, queue full) honestly.
"""

from typing import Annotated

from agno.tools import tool
from pydantic import BaseModel, Field

from magi.agent.tools.outputs import ToolOutput, fail, ok
from magi.core.evolution import EvolutionStore, ProposalError


class ProposalData(BaseModel):
    proposal_id: str = Field(description="The queued proposal's id.")
    kind: str = Field(description="What was proposed: 'prompt' or 'tool'.")
    target: str = Field(description="The prompt path or tool name.")
    status: str = Field(description="Always 'pending' — the operator decides.")


def build_evolution_tools(store: EvolutionStore) -> list:
    """The propose tools bound to an `EvolutionStore` (dependency-injected)."""

    @tool(
        description=(
            "PROPOSE a revision to one of your own operating prompts. Nothing "
            "changes until the operator approves it."
        ),
        instructions=(
            "Use when experience shows one of your adjustable prompts (the "
            "allowlisted ones — e.g. the memory-curation policy, the greeting "
            "policy, or one of your registered skills at 'skills/<name>.md') "
            "should work differently, or when the user asks you to adjust "
            "how such a process behaves. Pass the COMPLETE replacement text, not a "
            "diff, and a rationale grounded in what actually happened. This queues a "
            "proposal for the operator; it does NOT change anything now — say so. "
            "Your identity prompts are not proposable, by design."
        ),
        show_result=True,
    )
    def propose_prompt_update(
        target: Annotated[
            str,
            Field(
                min_length=4,
                description=(
                    "The prompt's overlay path, e.g. 'curation.md', 'greet.md', "
                    "or a skill's 'skills/<name>.md'."
                ),
            ),
        ],
        proposed_text: Annotated[
            str,
            Field(min_length=20, description="The complete replacement prompt text."),
        ],
        rationale: Annotated[
            str,
            Field(min_length=10, description="Why — grounded in observed behavior, not vibes."),
        ],
    ) -> ToolOutput[ProposalData]:
        """Queue a prompt revision for operator review (never applies directly)."""
        try:
            # File-or-skill-default resolution, shared with the curator's path.
            from magi.agent.skills import current_prompt_text

            proposal = store.propose(
                "prompt",
                target,
                proposed_text,
                rationale,
                source="lead",
                current_text=current_prompt_text(target),
            )
        except ProposalError as exc:
            return fail(str(exc))
        return ok(
            f"Proposal {proposal.id} queued for the operator — nothing changes until "
            "they approve it (and a restart applies it).",
            ProposalData(
                proposal_id=proposal.id, kind="prompt", target=proposal.target, status="pending"
            ),
        )

    @tool(
        description=(
            "PROPOSE a new simple HTTP tool for yourself (a declarative recipe). "
            "Nothing is created until the operator approves it."
        ),
        instructions=(
            "Use when a capability you repeatedly need is a plain HTTP call away — "
            "a local service, a public API the user pointed you at. Describe it as a "
            "recipe: name (lowercase slug), description (when to use it), method, "
            "url_template (absolute http(s); `{param}` placeholders allowed), and "
            "optional params (name -> description) and headers. Recipes are data, "
            "not code — that is the safety boundary. This queues a proposal; the "
            "tool exists only after operator approval and a restart — say so."
        ),
        show_result=True,
    )
    def propose_http_tool(
        name: Annotated[
            str, Field(min_length=3, description="Tool name: lowercase slug (a-z, 0-9, _).")
        ],
        description: Annotated[
            str, Field(min_length=10, description="What the tool does / when to use it.")
        ],
        method: Annotated[
            str, Field(description="HTTP method: GET, POST, PUT, DELETE, PATCH, or HEAD.")
        ],
        url_template: Annotated[
            str,
            Field(
                min_length=10,
                description="Absolute http(s) URL, with optional `{param}` placeholders.",
            ),
        ],
        rationale: Annotated[
            str,
            Field(min_length=10, description="Why this tool — grounded in a real recurring need."),
        ],
        params: Annotated[
            dict[str, str],
            Field(
                default_factory=dict,
                description="Placeholder/query parameter descriptions (name -> what it is).",
            ),
        ] = {},  # noqa: B006 — agno reads the annotation default; never mutated.
        headers: Annotated[
            dict[str, str],
            Field(default_factory=dict, description="Static request headers, if any."),
        ] = {},  # noqa: B006
    ) -> ToolOutput[ProposalData]:
        """Queue an HTTP tool recipe for operator review (never registers directly)."""
        import json

        recipe = {
            "name": name.strip(),
            "description": description.strip(),
            "method": method.strip().upper(),
            "url_template": url_template.strip(),
            "params": params,
            "headers": headers,
        }
        try:
            proposal = store.propose(
                "tool", recipe["name"], json.dumps(recipe), rationale, source="lead"
            )
        except ProposalError as exc:
            return fail(str(exc))
        return ok(
            f"Tool proposal {proposal.id} queued for the operator — the tool exists "
            "only after approval and a restart.",
            ProposalData(
                proposal_id=proposal.id, kind="tool", target=proposal.target, status="pending"
            ),
        )

    return [propose_prompt_update, propose_http_tool]
