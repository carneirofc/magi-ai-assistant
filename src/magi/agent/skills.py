"""First-class skills — prompt + tools + gate registered as one unit.

A capability used to be spread across three seams: a prompt overlay file, a
tool registration (`register_tool` / `register_lead_toolkit`), and a config
gate checked somewhere in team assembly. A Skill bundles them: a persona
declares one manifest and registers it once at the entrypoint, before
`build_team()`. Team build composes each active skill's prompt fragment into
the lead's instructions and attaches its tools, honoring the gate.

The skill's prompt is overlay-aware: it resolves through `load_prompt` at
`skills/<name>.md` (runtime overlay first, persona dir next, bundled last)
and falls back to the manifest's inline default when no file exists anywhere.
That path is the seam self-evolution targets — an approved enhancement lands
as an overlay file and wins over the inline default, while the persona source
stays pristine.

Same conventions as the other registries: idempotent registration, and a
broken skill (raising gate or toolkit) degrades to "not attached" with a
warning — the bot always boots.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Union

from agno.utils.log import log_warning

from magi.core.config import config
from magi.core.prompts import load_prompt

if TYPE_CHECKING:
    from magi.core.memory import MemoryManager

# Skill names double as the overlay-relative prompt filename (skills/<name>.md),
# so they must stay proposable-target-safe (see magi/core/evolution.py).
_NAME_RE = re.compile(r"^[a-z][a-z0-9_\-]{1,40}$")


@dataclass(frozen=True)
class Skill:
    """One registrable capability: what the assistant knows + what it can do.

    `prompt` is the inline default fragment; the resolved prompt prefers an
    overlay file at `skills/<name>.md` when one exists. `tools` are plain
    tools for the lead; `lead_toolkit` is a memory-injected builder (the
    engine's `build_*_tools(memory)` convention); `member_tools` join the
    shared member default set. `enabled` is the gate — a bool or a zero-arg
    callable evaluated at team build (so it can read config).
    """

    name: str
    prompt: str = ""
    tools: Sequence = field(default_factory=tuple)
    lead_toolkit: Optional[Callable[["MemoryManager"], Sequence]] = None
    member_tools: Sequence = field(default_factory=tuple)
    enabled: Union[bool, Callable[[], bool]] = True
    # Whether self-evolution may target this skill's prompt (see
    # magi/core/evolution.py). The manifest owns the choice; identity-class
    # prompts are never proposable regardless.
    proposable: bool = True

    @property
    def prompt_path(self) -> str:
        """Overlay-relative markdown path the skill's prompt resolves through."""
        return f"skills/{self.name}.md"


# The skill registry — ordered, module-global, mutated in place like
# MEMBER_BUILDERS so a persona extends it without editing the public tree.
SKILLS: list[Skill] = []


def register_skill(skill: Union[Skill, Callable[[], Skill]]):
    """Register a skill; return the argument (usable as a factory decorator).

    Accepts a `Skill` directly, or a zero-arg factory returning one (so
    `@register_skill` above a `def my_skill(): return Skill(...)` works).
    Call at the entrypoint, before `build_team()`. Idempotent by name: a
    re-imported entrypoint doesn't duplicate skills, first registration wins.
    """
    resolved = skill() if callable(skill) else skill
    if not isinstance(resolved, Skill):
        raise ValueError(f"register_skill expects a Skill, got {type(resolved).__name__}")
    if not _NAME_RE.match(resolved.name):
        raise ValueError(
            f"skill name {resolved.name!r} must be a lowercase slug (a-z, 0-9, _, -)"
        )
    if not any(s.name == resolved.name for s in SKILLS):
        SKILLS.append(resolved)
    return skill


def active_skills() -> list[Skill]:
    """The registered skills whose gate passes; a raising gate means skipped."""
    active: list[Skill] = []
    for skill in SKILLS:
        try:
            enabled = skill.enabled() if callable(skill.enabled) else bool(skill.enabled)
        except Exception as exc:  # noqa: BLE001 — degrade, don't abort startup.
            log_warning(f"skill '{skill.name}' gate raised, skipped ({type(exc).__name__}: {exc})")
            continue
        if enabled:
            active.append(skill)
    return active


def skill_prompt(skill: Skill) -> str:
    """The skill's resolved prompt: overlay file if present, inline default else."""
    try:
        return load_prompt(skill.prompt_path)
    except FileNotFoundError:
        return skill.prompt


def compose_skill_prompts() -> list[str]:
    """One labeled fragment per active skill, for the lead's instructions."""
    fragments = []
    for skill in active_skills():
        text = skill_prompt(skill)
        if text:
            fragments.append(f"### Skill: {skill.name}\n\n{text}")
    return fragments


def skill_lead_tools(memory: "MemoryManager") -> list:
    """Every active skill's lead tools: plain tools + memory-injected toolkit.

    A raising toolkit drops that skill's toolkit tools with a warning — its
    plain tools (already collected) still attach.
    """
    tools: list = []
    for skill in active_skills():
        tools.extend(skill.tools)
        if skill.lead_toolkit is not None:
            try:
                tools.extend(skill.lead_toolkit(memory))
            except Exception as exc:  # noqa: BLE001 — degrade, don't abort startup.
                log_warning(
                    f"skill '{skill.name}' lead toolkit skipped ({type(exc).__name__}: {exc})"
                )
    return tools


def skill_member_tools() -> list:
    """Every active skill's member tools (joined into the member default set)."""
    tools: list = []
    for skill in active_skills():
        tools.extend(skill.member_tools)
    return tools


def proposable_skill_targets() -> list[str]:
    """Registered skills' prompt paths that evolution may target.

    All *registered* skills (not just active ones — approvals apply on
    restart, when the gate may pass) whose manifest opted in. Composition
    points append these to the evolution allowlist; core stays agent-free.
    """
    return [s.prompt_path for s in SKILLS if s.proposable]


def find_skill_by_prompt_path(path: str) -> Optional[Skill]:
    """The registered skill whose prompt lives at `path`, if any."""
    for skill in SKILLS:
        if skill.prompt_path == path:
            return skill
    return None


def current_prompt_text(path: str) -> str:
    """The live text at an overlay prompt path, for an honest before/after.

    The resolved file when one exists anywhere on the overlay search path;
    else a matching skill's inline default; else "". The one fallback both
    propose paths (the lead's tool and the curator) share."""
    try:
        return load_prompt(path)
    except Exception:  # noqa: BLE001 — no file yet is the normal skill case.
        skill = find_skill_by_prompt_path(path)
        return skill.prompt if skill is not None else ""


def evolution_proposable_targets() -> list[str]:
    """The full evolution allowlist for this deployment: the configured prompt
    targets plus registered skills' prompts.

    The single composition helper every propose surface (team assembly, the
    curator, the admin store) reads, so a new proposable source is one edit —
    core stays agent-free by having the extension live here."""
    return [*config.evolution_proposable, *proposable_skill_targets()]
