"""Tests for the memory curator (agent/curator + MemoryManager.maybe_curate).

Two seams: the defensive JSON parsing (`_parse` turns any model output into a
`CurationResult` of per-fact operations, malformed => no-op) and the manager
applying those operations to the durable fact sheet. Neither touches the network —
the manager test injects a fake `CurateFn`.
"""

import pytest

from agent.curator import _format_input, _parse
from core.memory import CurationInput, CurationResult, FactOp
from core.memory import manager as manager_mod
from core.memory.manager import MemoryManager
from core.memory.store import FileMemoryStore


# --- parsing ----------------------------------------------------------------
def test_parse_full_object():
    result = _parse(
        '{"operations": [{"op": "add", "text": "Likes Python."}], '
        '"episode": "Helped debug.", "persona": "Be terse."}'
    )
    assert result.operations == (FactOp(op="add", fact_id=None, text="Likes Python."),)
    assert result.episode == "Helped debug."
    assert result.persona_adjustment == "Be terse."
    assert not result.is_empty


def test_parse_all_three_verbs():
    result = _parse(
        '{"operations": ['
        '{"op": "add", "text": "Uses SQLite."}, '
        '{"op": "update", "id": "ab12cd34", "text": "Prefers terse replies."}, '
        '{"op": "delete", "id": "ef56gh78"}'
        "]}"
    )
    assert result.operations == (
        FactOp(op="add", fact_id=None, text="Uses SQLite."),
        FactOp(op="update", fact_id="ab12cd34", text="Prefers terse replies."),
        FactOp(op="delete", fact_id="ef56gh78", text=None),
    )


def test_parse_empty_operations_is_noop():
    result = _parse('{"operations": [], "episode": null, "persona": null}')
    assert result.is_empty


def test_parse_strips_code_fence_and_prose():
    text = 'Here is the result:\n```json\n{"operations": [{"op": "add", "text": "Uses SQLite now."}]}\n```\n'
    result = _parse(text)
    assert result.operations == (FactOp(op="add", fact_id=None, text="Uses SQLite now."),)
    assert result.episode is None


def test_parse_drops_unusable_operations():
    # add with no text, update with no id, delete with no id, unknown verb — all dropped.
    result = _parse(
        '{"operations": ['
        '{"op": "add"}, '
        '{"op": "update", "text": "no id"}, '
        '{"op": "delete"}, '
        '{"op": "frobnicate", "text": "x"}, '
        '{"op": "add", "text": "kept"}'
        "]}"
    )
    assert result.operations == (FactOp(op="add", fact_id=None, text="kept"),)


@pytest.mark.parametrize("text", ["", "not json at all", "[1,2,3]", "{bad json"])
def test_parse_malformed_is_noop(text):
    assert _parse(text).is_empty


def test_parse_missing_operations_key_is_noop():
    assert _parse('{"episode": null}').is_empty


def test_format_input_includes_turn_and_facts():
    out = _format_input(
        CurationInput(
            user_message="I switched to SQLite",
            assistant_reply="Noted.",
            current_facts="[ab12cd34] Uses Postgres.",
            persona="Sharp and concise.",
        )
    )
    assert "I switched to SQLite" in out and "Noted." in out
    assert "[ab12cd34] Uses Postgres." in out and "Sharp and concise." in out


def test_format_input_handles_empty_facts():
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
            operations=(
                FactOp(op="add", text="Identity: CTF player."),
                FactOp(op="add", text="Prefers Python."),
            ),
            episode="Walked through an auth bug.",
            persona_adjustment="Lead with the fix, not the preamble.",
        )

    mgr = _manager(tmp_path, fake)
    applied = await mgr.maybe_curate("user said X", "assistant said Y")

    assert set(applied) == {"profile", "episode", "persona"}
    # The curator sees the turn it is curating.
    assert seen["input"].user_message == "user said X"
    assert seen["input"].assistant_reply == "assistant said Y"
    # The added facts are the durable memory now rendered into context.
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


async def test_maybe_curate_update_supersedes_a_fact(tmp_path):
    """ADD a fact on the first turn, then UPDATE it by id on the second."""
    captured: dict[str, str] = {}

    async def fake(inp: CurationInput) -> CurationResult:
        if not inp.current_facts:
            return CurationResult(operations=(FactOp(op="add", text="Uses Postgres."),))
        # The second pass sees the fact with its id and updates it.
        fact_id = inp.current_facts.split("]")[0].lstrip("[")
        captured["id"] = fact_id
        return CurationResult(
            operations=(FactOp(op="update", fact_id=fact_id, text="Uses SQLite (was Postgres)."),)
        )

    mgr = _manager(tmp_path, fake)
    await mgr.maybe_curate("I use Postgres", "ok")
    await mgr.maybe_curate("actually SQLite now", "noted")

    profile = mgr.recall_long_term()
    assert "SQLite" in profile
    assert "Uses Postgres." not in profile  # superseded in place, not appended
    # The fact sheet still holds exactly one fact (updated, not duplicated).
    assert len(mgr.mem.long_term_facts.read()) == 1
    assert captured["id"]  # the curator targeted a real id


async def test_maybe_curate_delete_drops_a_fact(tmp_path):
    async def fake(inp: CurationInput) -> CurationResult:
        if not inp.current_facts:
            return CurationResult(operations=(FactOp(op="add", text="Temporary fact."),))
        fact_id = inp.current_facts.split("]")[0].lstrip("[")
        return CurationResult(operations=(FactOp(op="delete", fact_id=fact_id),))

    mgr = _manager(tmp_path, fake)
    await mgr.maybe_curate("note this", "ok")
    assert "Temporary fact." in mgr.recall_long_term()
    await mgr.maybe_curate("forget that", "done")
    assert mgr.recall_long_term() == "(no long-term memory yet)"


async def test_maybe_curate_unknown_id_is_skipped(tmp_path):
    async def fake(inp: CurationInput) -> CurationResult:
        return CurationResult(
            operations=(FactOp(op="update", fact_id="deadbeef", text="ghost"),)
        )

    mgr = _manager(tmp_path, fake)
    # Nothing existed to update; the op is skipped and nothing is recorded.
    assert await mgr.maybe_curate("hi", "hello") is None
    assert mgr.recall_long_term() == "(no long-term memory yet)"


async def test_maybe_curate_failure_never_breaks_the_chat(tmp_path):
    async def boom(inp: CurationInput) -> CurationResult:
        raise RuntimeError("curator down")

    mgr = _manager(tmp_path, boom)
    assert await mgr.maybe_curate("hi", "hello") is None  # swallowed
    assert mgr.recall_long_term() == "(no long-term memory yet)"


async def test_maybe_curate_noop_without_curator(tmp_path):
    mgr = _manager(tmp_path, None)
    assert await mgr.maybe_curate("hi", "hello") is None


async def test_maybe_curate_clamps_runaway_fact(tmp_path):
    async def huge(inp: CurationInput) -> CurationResult:
        return CurationResult(operations=(FactOp(op="add", text="x" * 50_000),))

    mgr = MemoryManager(
        store=FileMemoryStore(tmp_path / "mem"),
        short_term_max=5,
        curate_fn=huge,
        long_term_fact_max_chars=500,
    )
    mgr.set_scope(user_id="u1", session_id="s1")
    await mgr.maybe_curate("hi", "hello")

    stored = mgr.mem.long_term_facts.texts()[0]
    assert len(stored) <= 500
    assert "truncated" in stored


async def test_maybe_curate_trims_facts_over_cap(tmp_path):
    counter = iter(range(100))

    async def add_one(inp: CurationInput) -> CurationResult:
        return CurationResult(operations=(FactOp(op="add", text=f"fact {next(counter)}"),))

    mgr = MemoryManager(
        store=FileMemoryStore(tmp_path / "mem"),
        short_term_max=5,
        curate_fn=add_one,
        long_term_facts_max=3,
    )
    mgr.set_scope(user_id="u1", session_id="s1")
    for _ in range(5):
        await mgr.maybe_curate("more", "ok")

    facts = mgr.mem.long_term_facts.texts()
    assert len(facts) == 3  # capped to the newest 3
    assert "fact 0" not in facts and "fact 4" in facts
