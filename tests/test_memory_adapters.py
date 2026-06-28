"""Unit tests for the four file-shape adapters (core/memory/adapters).

These pin the on-disk formats byte-for-byte so existing memory files round-trip
unchanged after the store was refactored onto adapters.
"""

from core.memory.adapters import BulletLog, Blob, JsonFacts, JsonWindow
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


def test_json_facts_add_returns_stable_unique_ids(tmp_path):
    facts = JsonFacts(tmp_path / "long_term_facts.json")

    id_a = facts.add("uses SQLite")
    id_b = facts.add("prefers terse replies")

    assert id_a != id_b
    stored = facts.read()
    assert [f["id"] for f in stored] == [id_a, id_b]
    assert facts.texts() == ["uses SQLite", "prefers terse replies"]


def test_json_facts_update_replaces_in_place_by_id(tmp_path):
    facts = JsonFacts(tmp_path / "long_term_facts.json")
    fid = facts.add("uses Postgres")

    assert facts.update(fid, "uses SQLite (was Postgres)") is True
    assert facts.texts() == ["uses SQLite (was Postgres)"]  # same single fact
    assert facts.update("nope", "ghost") is False  # unknown id, no-op


def test_json_facts_remove_drops_by_id(tmp_path):
    facts = JsonFacts(tmp_path / "long_term_facts.json")
    fid = facts.add("temporary")
    facts.add("durable")

    assert facts.remove(fid) is True
    assert facts.texts() == ["durable"]
    assert facts.remove(fid) is False  # already gone


def test_json_facts_trim_keeps_newest(tmp_path):
    facts = JsonFacts(tmp_path / "long_term_facts.json")
    for i in range(5):
        facts.add(f"fact {i}")

    assert facts.trim(2) == 3  # dropped the 3 oldest
    assert facts.texts() == ["fact 3", "fact 4"]
    assert facts.trim(0) == 0  # disabled


def test_json_facts_unreadable_file_degrades_to_empty(tmp_path):
    path = tmp_path / "long_term_facts.json"
    path.write_text("{not json", encoding="utf-8")

    assert JsonFacts(path).read() == []
