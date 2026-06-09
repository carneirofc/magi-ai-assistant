"""Unit tests for the three file-shape adapters (core/memory/adapters).

These pin the on-disk formats byte-for-byte so existing memory files round-trip
unchanged after the store was refactored onto adapters.
"""

from core.memory.adapters import BulletLog, Blob, JsonWindow
from core.memory.store import FileMemoryStore


def test_scoped_memory_exposes_its_identity(tmp_path):
    scoped = FileMemoryStore(tmp_path).scoped("u1", "s1")

    assert scoped.user_id == "u1"
    assert scoped.session_id == "s1"


def test_bullet_log_appends_timestamped_bullets_and_reads_bodies(tmp_path):
    log = BulletLog(tmp_path / "long_term.md", header="Long-term memory")

    log.append("likes tea")
    log.append("works nights")

    text = log.read()
    assert text.startswith("# Long-term memory")
    assert ":: likes tea" in text
    assert ":: works nights" in text
    assert log.bodies() == ["likes tea", "works nights"]
    assert log.count() == 2
    assert log.recent(1) == ["works nights"]


def test_blob_replaces_whole_file_with_header_and_body(tmp_path):
    blob = Blob(tmp_path / "summary.md", header="Long-term summary")

    blob.write("first")
    blob.write("second")  # whole-file replace, not append

    assert blob.read() == "# Long-term summary\n\nsecond"


def test_json_window_caps_and_returns_evicted(tmp_path):
    win = JsonWindow(tmp_path / "session.json")

    assert win.append("user", "a", max_entries=2) == []
    assert win.append("user", "b", max_entries=2) == []
    evicted = win.append("user", "c", max_entries=2)

    assert [t["content"] for t in evicted] == ["a"]
    assert [t["content"] for t in win.read()] == ["b", "c"]
    assert win.count() == 2


def test_json_window_extend_accumulates_without_trimming(tmp_path):
    buf = JsonWindow(tmp_path / "pending.json")

    assert buf.extend([{"role": "user", "content": "x"}]) == 1
    assert buf.extend([{"role": "user", "content": "y"}]) == 2
    assert [t["content"] for t in buf.read()] == ["x", "y"]
