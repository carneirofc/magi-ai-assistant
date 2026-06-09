"""Tests for the scoped memory kinds through their protocols (core/memory/kinds).

The manager-level tests in test_memory.py cover behavior end-to-end; these pin the
kind seam directly: which kinds fold, the session→episode close hand-off, and the
long-term fold marker.
"""

import pytest

from core.memory.kinds import Episodes, Folds, LongTerm, Renders, Session
from core.memory.store import FileMemoryStore


def _scope(tmp_path):
    return FileMemoryStore(tmp_path / "mem").scoped("u1", "s1")


def test_only_session_and_long_term_fold():
    lt = LongTerm(None, 5, 2, None, 3)
    ep = Episodes(None, 5, 5)
    se = Session(3, None, 2)

    assert isinstance(lt, Renders) and isinstance(lt, Folds)
    assert isinstance(se, Renders) and isinstance(se, Folds)
    assert isinstance(ep, Renders)
    assert not isinstance(ep, Folds)  # episodes never fold


def test_session_close_returns_dropped_and_wipes(tmp_path):
    mem = _scope(tmp_path)
    session = Session(short_term_max=5, summarize_fn=None, summarize_every=2)
    for i in range(3):
        session.record_turn(mem, "user", f"t{i}")

    dropped, carried = session.close(mem)

    assert dropped == 3
    assert carried is None  # no summarizer => no rolling summary to carry
    assert mem.live_turns.read() == []


async def test_long_term_fold_marker_blocks_refold_until_threshold_again(tmp_path):
    calls = []

    async def fake(text: str) -> str:
        calls.append(text)
        return "profile"

    mem = _scope(tmp_path)
    lt = LongTerm(None, 5, 2, summarize_fn=fake, summarize_every=3)
    for i in range(3):
        lt.remember(mem, f"fact {i}")

    assert await lt.maybe_fold(mem) == "profile"
    # Marker advanced — a second immediate fold is a no-op (no new facts crossed it).
    assert await lt.maybe_fold(mem) is None
    assert len(calls) == 1
