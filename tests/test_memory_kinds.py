"""Tests for the scoped memory kinds through their protocols (core/memory/kinds).

The manager-level tests in test_memory.py cover behavior end-to-end; these pin the
kind seam directly: which kinds fold and the session→episode close hand-off.
"""


from magi.core.memory.kinds import Episodes, Folds, LongTerm, Renders, Session
from magi.core.memory.store import FileMemoryStore


def _scope(tmp_path):
    return FileMemoryStore(tmp_path / "mem").scoped("u1", "s1")


def test_only_session_folds():
    lt = LongTerm(None, 5, 2)
    ep = Episodes(None, 5, 5)
    se = Session(3, None, 2)

    assert isinstance(se, Renders) and isinstance(se, Folds)
    assert isinstance(lt, Renders) and not isinstance(lt, Folds)  # curator owns durable memory
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
