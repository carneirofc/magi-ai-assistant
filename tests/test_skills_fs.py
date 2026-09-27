"""Tests for file-based skills: SKILL.md parsing, discovery, tools, and wiring."""

from pathlib import Path

import pytest

from magi.agent import skills as skills_mod
from magi.agent.skills import SKILLS, Skill, active_skills, all_skills, compose_skill_prompts
from magi.agent.tools.skills import build_skill_tools
from magi.agent.toolsets import TOOLSETS, toolset, unknown_disabled
from magi.core.config import configure
from magi.core.evolution import EvolutionStore, ProposalError
from magi.core.skills_fs import (
    SkillFileError,
    SkillFrontmatter,
    discover,
    load_skill,
    parse_skill_md,
    render_skill_md,
    write_skill,
)

GOOD = """---
name: release-notes
description: Draft release notes from merged PRs.
allowed-tools: http_get web_search
---

1. List merged PRs.
2. Group by type.
"""


def _make(root: Path, name: str, text: str) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    return d


@pytest.fixture
def library(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setenv("MAGI_HOME", str(tmp_path))
    return root


@pytest.fixture
def clean_registry():
    snapshot = list(SKILLS)
    yield
    SKILLS[:] = snapshot


# --- parsing ------------------------------------------------------------------


def test_parse_good_skill():
    meta, body = parse_skill_md(GOOD, source="t")
    assert meta.name == "release-notes"
    assert meta.allowed_tools == ["http_get", "web_search"]
    assert body.startswith("1. List merged PRs.")


@pytest.mark.parametrize(
    "text",
    [
        "no frontmatter",
        "---\nname: x\n---\nbody",  # name too short + no description
        "---\nname: Bad Name\ndescription: d\n---\n",
        "---\n: [unbalanced\n---\n",
        "---\ndescription: missing name\n---\n",
    ],
)
def test_parse_rejects_bad_skills(text):
    with pytest.raises(SkillFileError):
        parse_skill_md(text, source="t")


def test_name_must_match_directory(tmp_path):
    d = _make(tmp_path, "other-name", GOOD)
    with pytest.raises(SkillFileError, match="directory"):
        load_skill(d)


def test_render_roundtrips():
    meta = SkillFrontmatter(name="demo-skill", description="A demo.")
    meta2, body = parse_skill_md(render_skill_md(meta, "Do the thing."), source="t")
    assert meta2 == meta and body == "Do the thing."


# --- discovery + bundled files -------------------------------------------------


def test_discover_skips_bad_and_first_dir_wins(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _make(a, "release-notes", GOOD)
    _make(a, "broken", "nope")
    _make(b, "release-notes", GOOD.replace("Draft release", "Other"))
    found = discover([a, b, tmp_path / "missing"])
    assert [s.name for s in found] == ["release-notes"]
    assert found[0].description.startswith("Draft")


def test_bundled_files_and_traversal_guard(tmp_path):
    d = _make(tmp_path, "release-notes", GOOD)
    (d / "template.md").write_text("# Notes", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    skill = load_skill(d)
    assert skill.bundled_files() == ["template.md"]
    assert skill.read_file("template.md") == "# Notes"
    with pytest.raises(SkillFileError):
        skill.read_file("../secret.txt")


def test_write_skill_validates_and_writes(tmp_path):
    path = write_skill(tmp_path, GOOD)
    assert path == tmp_path / "release-notes" / "SKILL.md"
    with pytest.raises(SkillFileError):
        write_skill(tmp_path, "garbage")


# --- registry integration -------------------------------------------------------


def test_file_skills_join_prompt_as_an_index(library, clean_registry):
    _make(library, "release-notes", GOOD)
    SKILLS.append(Skill(name="dice", prompt="Roll dice when asked."))
    names = [(s.name, s.source) for s in all_skills()]
    assert names == [("dice", "python"), ("release-notes", "file")]
    fragments = compose_skill_prompts()
    assert fragments[0].startswith("### Skill: dice")
    assert "### Skills library" in fragments[1]
    assert "- release-notes: Draft release notes" in fragments[1]
    assert "List merged PRs" not in "\n".join(fragments)  # body is NOT inlined


def test_python_skill_wins_name_collision(library, clean_registry):
    _make(library, "release-notes", GOOD)
    SKILLS.append(Skill(name="release-notes", prompt="python version"))
    assert [s.source for s in all_skills()] == ["python"]


def test_disabled_skills_are_inactive(library, clean_registry):
    _make(library, "release-notes", GOOD)
    configure(skills={"disabled": ["release-notes"]})
    assert "release-notes" not in {s.name for s in active_skills()}


# --- tools -----------------------------------------------------------------------


def _call(fn, **kwargs):
    assert fn.entrypoint is not None
    return fn.entrypoint(**kwargs)


def _tools(library: Path, mode, store=None):
    return {
        t.name: t
        for t in build_skill_tools(
            lambda: discover([library]), write_root=library, mode=mode, store=store
        )
    }


def test_view_returns_body_files_and_file_content(library):
    d = _make(library, "release-notes", GOOD)
    (d / "template.md").write_text("# T", encoding="utf-8")
    view = _tools(library, "off")["skill_view"]
    out = _call(view, name="release-notes")
    assert out.success and "Group by type" in out.data.content
    assert out.data.files == ["template.md"]
    assert _call(view, name="release-notes", file="template.md").data.content == "# T"
    assert not _call(view, name="nope").success


def test_off_mode_has_no_write_tools(library):
    assert set(_tools(library, "off")) == {"skill_view"}


def test_direct_create_then_patch(library):
    tools = _tools(library, "direct")
    out = _call(
        tools["skill_create"],
        name="deploy-check",
        description="Verify a deploy before announcing it.",
        instructions="1. curl /healthz\n2. check logs",
        rationale="did this three times today",
    )
    assert out.success and out.data.mode == "direct"
    assert load_skill(library / "deploy-check").body.startswith("1. curl")
    dup = _call(
        tools["skill_create"],
        name="deploy-check",
        description="again and again",
        instructions="x" * 30,
        rationale="dup!!!!!!!",
    )
    assert not dup.success
    patched = _call(
        tools["skill_patch"],
        name="deploy-check",
        old_text="check logs",
        new_text="tail the journal",
        rationale="better way",
    )
    assert patched.success
    assert "tail the journal" in load_skill(library / "deploy-check").body


def test_patch_needs_a_unique_match(library):
    _make(library, "release-notes", GOOD)
    out = _call(
        _tools(library, "direct")["skill_patch"],
        name="release-notes",
        old_text="zzz",
        new_text="y",
        rationale="no match here",
    )
    assert not out.success and "found 0" in out.message


def test_propose_queues_and_approval_writes(library, tmp_path):
    store = EvolutionStore(tmp_path / "memory", skills_root=library)
    out = _call(
        _tools(library, "propose", store)["skill_create"],
        name="deploy-check",
        description="Verify a deploy before announcing it.",
        instructions="1. curl /healthz and read it",
        rationale="a recurring task",
    )
    assert out.success and out.data.mode == "propose"
    assert not (library / "deploy-check").exists()  # nothing until approved
    decided = store.decide(out.data.location, approve=True)
    assert decided is not None and decided.status == "approved"
    assert load_skill(library / "deploy-check").description.startswith("Verify")


def test_propose_without_store_fails_honestly(library):
    out = _call(
        _tools(library, "propose", None)["skill_create"],
        name="deploy-check",
        description="Verify a deploy first.",
        instructions="x" * 25,
        rationale="because it recurs",
    )
    assert not out.success and "evolution_enabled" in out.message


def test_evolution_rejects_malformed_skill(tmp_path):
    store = EvolutionStore(tmp_path / "memory", skills_root=tmp_path / "skills")
    with pytest.raises(ProposalError):
        store.propose("skill", "x", "not a skill", "why not", source="lead")


# --- toolsets --------------------------------------------------------------------


def test_toolset_gate_and_typos():
    assert toolset("http", [1, 2]) == [1, 2]
    configure(toolsets={"disabled": ["http", "htttp"]})
    assert toolset("http", [1, 2]) == []
    assert unknown_disabled() == ["htttp"]
    with pytest.raises(KeyError):
        toolset("not-a-toolset", [])


def test_every_team_toolset_is_documented():
    import inspect

    from magi.agent import team

    used = set(__import__("re").findall(r'toolset\(\s*"([a-z_]+)"', inspect.getsource(team)))
    assert used <= set(TOOLSETS)
    assert skills_mod.file_skills is not None
