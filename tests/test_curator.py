"""Tests for the memory curator (agent/curator + MemoryManager.maybe_curate).

Two seams: the defensive JSON parsing (`_parse` turns any model output into a
`CurationResult`, malformed => no-op) and the manager applying a result to the
durable files. Neither touches the network — the manager test injects a fake
`CurateFn`.
"""

import pytest

from agent.curator import _format_input, _parse
from core.memory import CurationInput, CurationResult
from core.memory import manager as manager_mod
from core.memory.manager import MemoryManager
from core.memory.store import FileMemoryStore


# --- parsing ----------------------------------------------------------------
def test_parse_full_object():
    result = _parse('{"profile": "Likes Python.", "episode": "Helped debug.", "persona": "Be terse."}')
    assert result.profile == "Likes Python."
    assert result.episode == "Helped debug."
    assert result.persona_adjustment == "Be terse."
    assert not result.is_empty


def test_parse_nulls_are_empty():
    result = _parse('{"profile": null, "episode": null, "persona": null}')
    assert result.is_empty


def test_parse_strips_code_fence_and_prose():
    text = 'Here is the result:\n```json\n{"profile": "Uses SQLite now."}\n```\n'
    result = _parse(text)
    assert result.profile == "Uses SQLite now."
    assert result.episode is None


def test_parse_blank_strings_treated_as_none():
    result = _parse('{"profile": "   ", "episode": ""}')
    assert result.is_empty


@pytest.mark.parametrize("text", ["", "not json at all", "[1,2,3]", "{bad json"])
def test_parse_malformed_is_noop(text):
    assert _parse(text).is_empty


def test_format_input_includes_turn_and_profile():
    out = _format_input(
        CurationInput(
            user_message="I switched to SQLite",
            assistant_reply="Noted.",
            current_profile="Uses Postgres.",
            persona="Sharp and concise.",
        )
    )
    assert "I switched to SQLite" in out and "Noted." in out
    assert "Uses Postgres." in out and "Sharp and concise." in out


def test_format_input_handles_empty_profile():
    out = _format_input(CurationInput("hi", "hello", "", ""))
    assert "(empty)" in out and "(none)" in out


# --- manager apply ----------------------------------------------------------
@pytest.fixture(autouse=True)
def _reset_scope():
    token = manager_mod._scope.set(None)
    yield
    manager_mod._scope.reset(token)


def _manager(tmp_path, curate_fn):
    mgr = MemoryManager(
        store=FileMemoryStore(tmp_path / "mem"),
        short_term_max=5,
        curate_fn=curate_fn,
    )
    mgr.set_scope(user_id="u1", session_id="s1")
    return mgr


async def test_maybe_curate_applies_all_three(tmp_path):
    seen = {}

    async def fake(inp: CurationInput) -> CurationResult:
        seen["input"] = inp
        return CurationResult(
            profile="Identity: CTF player. Prefers Python.",
            episode="Walked through an auth bug.",
            persona_adjustment="Lead with the fix, not the preamble.",
        )

    mgr = _manager(tmp_path, fake)
    applied = await mgr.maybe_curate("user said X", "assistant said Y")

    assert set(applied) == {"profile", "episode", "persona"}
    # The curator sees the turn it is curating.
    assert seen["input"].user_message == "user said X"
    assert seen["input"].assistant_reply == "assistant said Y"
    # Profile is the durable memory now rendered into context.
    assert "Prefers Python." in mgr.recall_long_term()
    assert "Identity: CTF player." in mgr.build_context()
    assert "Walked through an auth bug." in mgr.recall_episodes()
    assert "Lead with the fix" in mgr.store.persona.read()


async def test_maybe_curate_empty_result_changes_nothing(tmp_path):
    async def fake(inp: CurationInput) -> CurationResult:
        return CurationResult()  # nothing durable

    mgr = _manager(tmp_path, fake)
    assert await mgr.maybe_curate("hi", "hello") is None
    assert mgr.recall_long_term() == "(no long-term memory yet)"
    assert mgr.recall_episodes() == "(no episodes recorded yet)"


async def test_maybe_curate_rewrite_supersedes_prior_profile(tmp_path):
    profiles = iter(["Uses Postgres.", "Uses SQLite (switched from Postgres)."])

    async def fake(inp: CurationInput) -> CurationResult:
        return CurationResult(profile=next(profiles))

    mgr = _manager(tmp_path, fake)
    await mgr.maybe_curate("I use Postgres", "ok")
    await mgr.maybe_curate("actually SQLite now", "noted")

    profile = mgr.recall_long_term()
    assert "SQLite" in profile
    assert "Uses Postgres." not in profile  # whole-file rewrite, not append


async def test_maybe_curate_failure_never_breaks_the_chat(tmp_path):
    async def boom(inp: CurationInput) -> CurationResult:
        raise RuntimeError("curator down")

    mgr = _manager(tmp_path, boom)
    assert await mgr.maybe_curate("hi", "hello") is None  # swallowed
    assert mgr.recall_long_term() == "(no long-term memory yet)"


async def test_maybe_curate_noop_without_curator(tmp_path):
    mgr = _manager(tmp_path, None)
    assert await mgr.maybe_curate("hi", "hello") is None


async def test_maybe_curate_clamps_runaway_profile(tmp_path):
    async def huge(inp: CurationInput) -> CurationResult:
        return CurationResult(profile="x" * 50_000)

    mgr = MemoryManager(
        store=FileMemoryStore(tmp_path / "mem"),
        short_term_max=5,
        curate_fn=huge,
        long_term_summary_max_chars=500,
    )
    mgr.set_scope(user_id="u1", session_id="s1")
    await mgr.maybe_curate("hi", "hello")

    stored = mgr.mem.long_term_summary.read()
    assert len(stored) < 700  # 500 + header + marker
    assert "truncated" in stored
