"""Skill tools — read the SKILL.md library, and (optionally) grow it.

`skill_view` is always attached when there are file skills or the assistant
may write them: it returns a skill's instructions plus the list of bundled
files, or one bundled file (progressive disclosure — the lead's prompt only
carries each skill's one-line description).

`skill_create` / `skill_patch` follow `config.skills.agent_write`:

- "propose" — the new SKILL.md is queued in the evolution queue for the
  operator (needs `evolution_enabled`); nothing changes until approved.
- "direct"  — written straight to `$MAGI_HOME/skills` (atomic, validated);
  live on the next team build.
- "off"     — the write tools are not attached.

A skill is a procedure the assistant worked out and wants to reuse — the
Hermes-style "learn from experience" loop, with the operator in charge.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal

from agno.tools import tool
from agno.tools.function import Function
from pydantic import BaseModel, Field

from magi.agent.tools.outputs import ToolOutput, fail, ok
from magi.core.evolution import EvolutionStore, ProposalError
from magi.core.skills_fs import (
    SKILL_NAME_RE,
    FileSkill,
    SkillFileError,
    SkillFrontmatter,
    parse_skill_md,
    render_skill_md,
    write_skill,
)

type WriteMode = Literal["off", "propose", "direct"]


class SkillViewData(BaseModel):
    name: str
    description: str
    content: str = Field(description="The skill's instructions, or the requested file.")
    files: list[str] = Field(description="Bundled files you can open with `file`.")


class SkillWriteData(BaseModel):
    name: str
    mode: str = Field(description="'direct' (written now) or 'propose' (queued).")
    location: str = Field(description="The file written, or the proposal id.")


def build_skill_tools(
    library: Callable[[], list[FileSkill]],
    *,
    write_root: Path,
    mode: WriteMode,
    store: EvolutionStore | None,
) -> list[Function]:
    """The skill tools. `library` re-reads the skills on each call so a skill
    written this session is viewable immediately."""

    def find(name: str) -> FileSkill | None:
        return next((s for s in library() if s.name == name), None)

    @tool(
        description="Open a saved skill: its step-by-step instructions and bundled files.",
        instructions=(
            "Call with a skill name from your Skills library BEFORE doing a task it "
            "covers, then follow what it says. Pass `file` to read one of the "
            "bundled files it lists."
        ),
        show_result=True,
    )
    def skill_view(
        name: Annotated[str, Field(description="Skill name from the Skills library.")],
        file: Annotated[
            str | None, Field(default=None, description="A bundled file to read instead.")
        ] = None,
    ) -> ToolOutput[SkillViewData]:
        skill = find(name.strip())
        if skill is None:
            return fail(f"No skill named {name!r}.")
        try:
            content = skill.read_file(file) if file else skill.body
        except SkillFileError as exc:
            return fail(str(exc))
        return ok(
            f"Skill {skill.name}.",
            SkillViewData(
                name=skill.name,
                description=skill.description,
                content=content,
                files=skill.bundled_files(),
            ),
        )

    def save(text: str, rationale: str, current: str) -> ToolOutput[SkillWriteData]:
        try:
            meta, _ = parse_skill_md(text, source="skill")
        except SkillFileError as exc:
            return fail(str(exc))
        if mode == "direct":
            path = write_skill(write_root, text)
            return ok(
                f"Saved skill {meta.name}; it is in your library from the next conversation.",
                SkillWriteData(name=meta.name, mode="direct", location=str(path)),
            )
        if store is None:
            return fail("Skill proposals need self-evolution enabled (evolution_enabled).")
        try:
            proposal = store.propose(
                "skill", meta.name, text, rationale, source="lead", current_text=current
            )
        except ProposalError as exc:
            return fail(str(exc))
        return ok(
            f"Skill {meta.name} proposed ({proposal.id}); the operator decides — it is "
            "not usable until approved.",
            SkillWriteData(name=meta.name, mode="propose", location=proposal.id),
        )

    @tool(
        description="Save a reusable procedure as a new skill for future conversations.",
        instructions=(
            "Use after you completed a non-trivial, repeatable task (several steps, a "
            "tricky fix, a workflow the user will want again) — write down how to do "
            "it so next time you can follow it. `name`: lowercase slug. "
            "`description`: one line saying WHEN to use it (this is all you will see "
            "until you open it). `instructions`: concise numbered steps, pitfalls, "
            "and how to verify. Don't save one-off facts — those are memory."
        ),
        show_result=True,
    )
    def skill_create(
        name: Annotated[str, Field(pattern=SKILL_NAME_RE.pattern)],
        description: Annotated[str, Field(min_length=10, max_length=1024)],
        instructions: Annotated[str, Field(min_length=20)],
        rationale: Annotated[str, Field(min_length=10, description="What task taught you this.")],
    ) -> ToolOutput[SkillWriteData]:
        if find(name) is not None:
            return fail(f"Skill {name!r} exists — use skill_patch to improve it.")
        meta = SkillFrontmatter(name=name, description=description)
        return save(render_skill_md(meta, instructions), rationale, current="")

    @tool(
        description="Improve an existing skill by replacing one exact passage.",
        instructions=(
            "Use when following a skill went wrong or you found a better way. "
            "`old_text` must appear exactly once in the skill's instructions."
        ),
        show_result=True,
    )
    def skill_patch(
        name: Annotated[str, Field(description="The skill to change.")],
        old_text: Annotated[str, Field(min_length=1)],
        new_text: Annotated[str, Field(description="Replacement text (may be empty).")],
        rationale: Annotated[str, Field(min_length=10)],
    ) -> ToolOutput[SkillWriteData]:
        skill = find(name.strip())
        if skill is None:
            return fail(f"No skill named {name!r}.")
        count = skill.body.count(old_text)
        if count != 1:
            return fail(f"old_text must match exactly once (found {count}).")
        current = (skill.root / "SKILL.md").read_text(encoding="utf-8")
        body = skill.body.replace(old_text, new_text, 1)
        return save(render_skill_md(skill.meta, body), rationale, current=current)

    tools: list[Function] = [skill_view]
    if mode != "off":
        tools += [skill_create, skill_patch]
    return tools
