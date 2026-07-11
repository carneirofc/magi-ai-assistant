"""Self-evolution with a human in the loop — the proposal queue.

The assistant is allowed to grow, not to drift: she can PROPOSE changes to her
own operating prompts (and new declarative HTTP tools), but nothing applies
until the operator approves it. This module owns that queue and the apply
path; the model-facing propose tools live in magi/agent/tools/evolution.py and
only ever write here — never to a prompt file.

Layout, deliberately inside the memory tree (so memory-git versions every
proposal, decision, and applied change, and the whole thing is inspectable
files like the rest of deliberate memory):

    <memory_dir>/
      evolution/proposals/<id>.json    # one Proposal per file
      prompts-runtime/<target>.md      # APPROVED prompt overlays
      tools-runtime/<name>.json        # APPROVED http-tool recipes

`prompts-runtime` is registered FIRST in the entrypoint's prompt overlay
(`set_prompt_overlay(<memory_dir>/prompts-runtime, <persona>/prompts)`), so an
approved change wins over the repo prompt while the repo stays pristine —
revert = delete the runtime file (or git-revert its commit). Prompts bake into
agents at team build, so applies take effect on RESTART; the admin surface
says so instead of pretending otherwise.

Safety rails: only allowlisted targets are proposable (`evolution_proposable`
— the identity-bearing SOUL.md and lead.md are not in any sane allowlist);
the pending queue is capped; every decision is recorded on the proposal file.
"""

import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from agno.utils.log import log_info, log_warning

from magi.core.config import config
from magi.core.memory.adapters import emit_write

ProposalKind = Literal["prompt", "tool"]
ProposalStatus = Literal["pending", "approved", "rejected"]

# Prompt targets are overlay-relative markdown paths (e.g. "curation.md",
# "team/danbooru.md") — no traversal, no absolute paths.
_TARGET_RE = re.compile(r"^[a-z0-9_\-]+(/[a-z0-9_\-]+)*\.md$")
# Tool recipe names are bare slugs.
_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")

_RECIPE_REQUIRED = ("name", "description", "method", "url_template")
_RECIPE_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD")


class ProposalError(ValueError):
    """A proposal that violates the rails (bad target, full queue, bad recipe)."""


@dataclass(frozen=True)
class Proposal:
    """One proposed change, from birth to decision."""

    id: str
    kind: ProposalKind
    target: str  # prompt path ("curation.md") or tool name ("fetch_weather")
    current_text: str  # what the target says now (empty for a new tool)
    proposed_text: str  # the full replacement text / the recipe JSON
    rationale: str
    source: str  # "lead" | "operator" | (later) "curator"
    status: ProposalStatus = "pending"
    created: str = ""
    decided: str = ""
    applied_path: str = ""  # where the approved change landed

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


def validate_recipe(text: str) -> dict:
    """Parse + validate an HTTP tool recipe. Declarative on purpose: capability
    growth without arbitrary code execution — a recipe is data the loader
    (magi/agent/tools/recipes.py) turns into a plain HTTP tool."""
    try:
        recipe = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProposalError(f"recipe is not valid JSON: {exc}") from exc
    if not isinstance(recipe, dict):
        raise ProposalError("recipe must be a JSON object")
    for key in _RECIPE_REQUIRED:
        if not str(recipe.get(key) or "").strip():
            raise ProposalError(f"recipe needs a non-empty {key!r}")
    if not _TOOL_NAME_RE.match(str(recipe["name"])):
        raise ProposalError("recipe name must be a lowercase slug (a-z, 0-9, _)")
    if str(recipe["method"]).upper() not in _RECIPE_METHODS:
        raise ProposalError(f"recipe method must be one of {_RECIPE_METHODS}")
    if not str(recipe["url_template"]).startswith(("http://", "https://")):
        raise ProposalError("recipe url_template must be an absolute http(s) URL")
    return recipe


class EvolutionStore:
    """The proposal queue + apply path over one memory root. Pure file IO."""

    def __init__(
        self,
        root: Path,
        *,
        proposable: Optional[list[str]] = None,
        queue_max: Optional[int] = None,
    ):
        self.root = Path(root)
        self.proposals_dir = self.root / "evolution" / "proposals"
        self.prompts_runtime = self.root / "prompts-runtime"
        self.tools_runtime = self.root / "tools-runtime"
        self.proposable = (
            list(proposable) if proposable is not None else list(config.evolution_proposable)
        )
        self.queue_max = queue_max if queue_max is not None else config.evolution_queue_max

    # --- propose -------------------------------------------------------------
    def propose(
        self,
        kind: ProposalKind,
        target: str,
        proposed_text: str,
        rationale: str,
        *,
        source: str,
        current_text: str = "",
    ) -> Proposal:
        """Queue one proposal. Raises `ProposalError` when it breaks the rails —
        the tool layer relays that as an honest failure."""
        target = target.strip()
        proposed_text = proposed_text.strip()
        if not proposed_text:
            raise ProposalError("proposed_text is empty")
        if kind == "prompt":
            if not _TARGET_RE.match(target):
                raise ProposalError(
                    f"bad prompt target {target!r} (expected an overlay-relative "
                    "path like 'curation.md' or 'team/danbooru.md')"
                )
            if target not in self.proposable:
                raise ProposalError(
                    f"prompt {target!r} is not proposable here (allowed: "
                    f"{', '.join(self.proposable) or 'none'})"
                )
        else:
            recipe = validate_recipe(proposed_text)
            target = str(recipe["name"])
        if len(self.list(status="pending")) >= self.queue_max:
            raise ProposalError(
                f"the proposal queue is full ({self.queue_max} pending) — "
                "ask the operator to review it first"
            )

        proposal = Proposal(
            id=uuid.uuid4().hex[:10],
            kind=kind,
            target=target,
            current_text=current_text,
            proposed_text=proposed_text,
            rationale=rationale.strip(),
            source=source,
            created=datetime.now().isoformat(timespec="seconds"),
        )
        self._write(proposal)
        log_info(f"evolution: proposal {proposal.id} queued ({kind} {target!r}, from {source})")
        return proposal

    # --- reads ---------------------------------------------------------------
    def list(self, status: Optional[str] = None) -> list[Proposal]:
        """All proposals, newest first (optionally filtered by status)."""
        if not self.proposals_dir.is_dir():
            return []
        out: list[Proposal] = []
        for path in self.proposals_dir.glob("*.json"):
            proposal = self._read(path)
            if proposal is not None and (status is None or proposal.status == status):
                out.append(proposal)
        out.sort(key=lambda p: p.created, reverse=True)
        return out

    def get(self, proposal_id: str) -> Optional[Proposal]:
        path = self.proposals_dir / f"{proposal_id}.json"
        return self._read(path) if path.is_file() else None

    # --- decide --------------------------------------------------------------
    def decide(self, proposal_id: str, approve: bool) -> Optional[Proposal]:
        """Approve (apply) or reject one pending proposal. Returns the decided
        proposal, or None for an unknown id. Deciding a non-pending proposal
        raises — decisions are one-shot."""
        proposal = self.get(proposal_id)
        if proposal is None:
            return None
        if proposal.status != "pending":
            raise ProposalError(f"proposal {proposal_id} was already {proposal.status}")

        applied_path = ""
        if approve:
            applied_path = str(self._apply(proposal))
        decided = Proposal(
            **{
                **asdict(proposal),
                "status": "approved" if approve else "rejected",
                "decided": datetime.now().isoformat(timespec="seconds"),
                "applied_path": applied_path,
            }
        )
        self._write(decided)
        log_info(
            f"evolution: proposal {proposal_id} {'APPROVED' if approve else 'rejected'}"
            + (f" -> {applied_path}" if applied_path else "")
        )
        return decided

    def _apply(self, proposal: Proposal) -> Path:
        """Write the approved change into the runtime overlay. The write rides
        `emit_write`, so git-backed memory commits it like any deliberate write."""
        if proposal.kind == "prompt":
            path = self.prompts_runtime / proposal.target
            content = proposal.proposed_text + "\n"
        else:
            recipe = validate_recipe(proposal.proposed_text)
            path = self.tools_runtime / f"{proposal.target}.json"
            content = json.dumps(recipe, ensure_ascii=False, indent=2) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        emit_write(path)
        return path

    # --- io ------------------------------------------------------------------
    def _write(self, proposal: Proposal) -> None:
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
        path = self.proposals_dir / f"{proposal.id}.json"
        path.write_text(proposal.to_json() + "\n", encoding="utf-8")
        emit_write(path)

    def _read(self, path: Path) -> Optional[Proposal]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Proposal(**data)
        except Exception as exc:  # noqa: BLE001 — one corrupt file must not hide the queue.
            log_warning(f"evolution: unreadable proposal {path.name}: {type(exc).__name__}: {exc}")
            return None


def build_evolution_store(root: Path) -> Optional[EvolutionStore]:
    """The store for this deployment, or None when evolution is off."""
    if not config.evolution_enabled:
        return None
    return EvolutionStore(Path(root))
