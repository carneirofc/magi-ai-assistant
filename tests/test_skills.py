"""Tests for the skill manifest (agent/skills).

A skill used to be spread across three seams — a prompt overlay file, a tool
registration, and a config gate. These pin the one-unit contract: register a
Skill once and the engine composes its prompt into the lead's instructions and
attaches its tools at team build, honoring the gate and degrading (never
aborting) on a broken skill.
"""

from dataclasses import fields

import pytest

from magi.agent.skills import (
    SKILLS,
    Skill,
    active_skills,
    compose_skill_prompts,
    proposable_skill_targets,
    register_skill,
    skill_lead_tools,
    skill_member_tools,
    skill_prompt,
)
from magi.agent.tools import enabled_tools
from magi.core.prompts import set_prompt_overlay


@pytest.fixture(autouse=True)
def restore_skills():
    """Snapshot/restore the module-global skill registry around every test."""
    snapshot = list(SKILLS)
    yield
    SKILLS[:] = snapshot


@pytest.fixture
def restore_overlay():
    yield
    set_prompt_overlay()


def test_register_skill_is_idempotent_by_name():
    skill = Skill(name="dice", prompt="Roll dice when asked.")
    assert register_skill(skill) is skill
    register_skill(Skill(name="dice", prompt="another body, same name"))

    assert [s for s in SKILLS if s.name == "dice"] == [skill]


def test_register_skill_usable_as_factory_decorator():
    @register_skill
    def dice_skill():
        return Skill(name="dice", prompt="Roll dice when asked.")

    assert dice_skill.__name__ == "dice_skill"  # decorator returns the factory
    register_skill(dice_skill)  # re-registering the factory is a no-op
    assert len([s for s in SKILLS if s.name == "dice"]) == 1


def test_register_skill_rejects_non_slug_name():
    with pytest.raises(ValueError):
        register_skill(Skill(name="Not A Slug!", prompt="x"))


def test_gate_excludes_disabled_and_raising_skills():
    register_skill(Skill(name="on", prompt="a"))
    register_skill(Skill(name="off", prompt="b", enabled=False))

    def broken_gate():
        raise RuntimeError("misconfigured gate")

    register_skill(Skill(name="broken", prompt="c", enabled=broken_gate))

    names = [s.name for s in active_skills()]
    assert "on" in names
    assert "off" not in names
    assert "broken" not in names  # degraded with a warning, not an exception


def test_skill_prompt_falls_back_to_inline_default():
    skill = Skill(name="dice", prompt="Roll dice when asked.")
    assert skill_prompt(skill) == "Roll dice when asked."


def test_skill_prompt_overlay_file_wins(tmp_path, restore_overlay):
    (tmp_path / "skills").mkdir(parents=True)
    (tmp_path / "skills" / "dice.md").write_text("Overlaid dice rules.", encoding="utf-8")
    set_prompt_overlay(tmp_path)

    skill = Skill(name="dice", prompt="inline default")
    assert skill_prompt(skill) == "Overlaid dice rules."


def test_compose_skill_prompts_labels_each_fragment():
    register_skill(Skill(name="dice", prompt="Roll dice when asked."))
    fragments = compose_skill_prompts()
    assert any("dice" in f and "Roll dice when asked." in f for f in fragments)


def test_skill_lead_tools_injects_memory_and_degrades():
    seen = []

    def toolkit(memory):
        seen.append(memory)
        return ["lead-tool"]

    def broken(memory):
        raise RuntimeError("boom")

    register_skill(Skill(name="good", prompt="a", tools=("plain-tool",), lead_toolkit=toolkit))
    register_skill(Skill(name="bad", prompt="b", lead_toolkit=broken))

    memory = object()
    tools = skill_lead_tools(memory)
    assert tools == ["plain-tool", "lead-tool"]
    assert seen == [memory]


def test_member_tools_flow_through_enabled_tools():
    register_skill(Skill(name="dice", prompt="a", member_tools=("member-tool",)))
    assert "member-tool" in skill_member_tools()
    assert "member-tool" in enabled_tools()
    # An explicit list still overrides everything.
    assert "member-tool" not in enabled_tools([])


def test_build_team_composes_skill_prompt_and_tools(tmp_path):
    from agno.tools import tool

    from magi.agent.team import build_team
    from magi.core.config import config, configure
    from magi.core.memory import build_memory_from_config

    @tool(name="roll_dice", show_result=True)
    def roll_dice():
        """Roll a die."""

    register_skill(
        Skill(name="dice", prompt="Roll dice when the user asks for randomness.", tools=(roll_dice,))
    )

    snapshot = {f.name: getattr(config, f.name) for f in fields(config)}
    try:
        configure(memory_dir=str(tmp_path / "memory"))
        team = build_team(build_memory_from_config())
    finally:
        configure(**snapshot)

    names = [getattr(t, "name", type(t).__name__) for t in (team.tools or [])]
    assert "roll_dice" in names
    assert "Roll dice when the user asks for randomness." in team.instructions


# --- self-evolution: skill prompts as proposable targets -----------------------


def test_proposable_skill_targets_respects_manifest_flag():
    register_skill(Skill(name="open", prompt="a"))
    register_skill(Skill(name="closed", prompt="b", proposable=False))

    targets = proposable_skill_targets()
    assert "skills/open.md" in targets
    assert "skills/closed.md" not in targets


def test_skill_prompt_proposal_end_to_end(tmp_path, restore_overlay):
    """Register → propose (via the real team's tool) → approve → overlay wins."""
    from magi.agent.team import build_team
    from magi.core.config import config, configure
    from magi.core.evolution import EvolutionStore
    from magi.core.memory import build_memory_from_config

    skill = Skill(name="dice", prompt="Roll dice honestly.")
    register_skill(skill)

    snapshot = {f.name: getattr(config, f.name) for f in fields(config)}
    try:
        configure(memory_dir=str(tmp_path / "memory"), evolution_enabled=True)
        memory = build_memory_from_config()
        team = build_team(memory)

        propose = next(t for t in team.tools if getattr(t, "name", "") == "propose_prompt_update")

        # Identity prompts stay non-proposable even with skills registered.
        refused = propose.entrypoint(
            target="team/SOUL.md", proposed_text="x" * 30, rationale="drift attempt"
        )
        assert not refused.success

        result = propose.entrypoint(
            target="skills/dice.md",
            proposed_text="Roll dice honestly, and always show the modifier math.",
            rationale="users keep asking for the modifier breakdown",
        )
        assert result.success

        store = EvolutionStore(memory.store.root)
        proposal = store.get(result.data.proposal_id)
        # The honest before: no overlay file yet, so the manifest's inline default.
        assert proposal.current_text == "Roll dice honestly."

        decided = store.decide(proposal.id, approve=True)
        assert decided.applied_path.endswith("prompts-runtime/skills/dice.md")

        # The approved overlay wins over the inline default at next build.
        set_prompt_overlay(memory.store.root / "prompts-runtime")
        assert skill_prompt(skill) == "Roll dice honestly, and always show the modifier math."
    finally:
        configure(**snapshot)
