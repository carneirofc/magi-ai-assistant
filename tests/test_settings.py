"""Tests for operator settings (core/settings) and how the memory factory overlays
them on the code defaults (core/memory.resolve_memory_settings).

These pin the two-layer contract: an absent/blank override inherits the `config`
default, a set override wins, the file round-trips, and the version token moves on a
write (so the admin editor's optimistic concurrency has something to check).
"""

from magi.core.config import config
from magi.core.memory import resolve_memory_settings
from magi.core.settings import MemoryOverrides, OperatorSettingsStore


def test_absent_file_reads_as_no_overrides(tmp_path):
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")

    overrides = store.read_memory()
    assert overrides.is_empty
    assert store.version() == OperatorSettingsStore(tmp_path / "missing.json").version()


def test_set_and_read_round_trips(tmp_path):
    store = OperatorSettingsStore(tmp_path / "s.json")

    store.set_memory(
        MemoryOverrides(
            memory_dir="~/mem",
            git_enabled=True,
            git_author_name="op",
            git_author_email="op@host",
        )
    )
    again = store.read_memory()
    assert again.memory_dir == "~/mem"
    assert again.git_enabled is True
    assert again.git_author_name == "op" and again.git_author_email == "op@host"


def test_version_moves_on_write(tmp_path):
    store = OperatorSettingsStore(tmp_path / "s.json")
    before = store.version()

    store.set_memory(MemoryOverrides(memory_dir="/data/mem"))

    assert store.version() != before


def test_blank_fields_clear_the_override(tmp_path):
    store = OperatorSettingsStore(tmp_path / "s.json")
    store.set_memory(MemoryOverrides(memory_dir="/data/mem", git_author_name="op"))

    # A later save with blanks (None) drops those keys → back to inheriting defaults.
    store.set_memory(MemoryOverrides(memory_dir=None, git_enabled=True))
    overrides = store.read_memory()

    assert overrides.memory_dir is None
    assert overrides.git_author_name is None
    assert overrides.git_enabled is True


def test_corrupt_file_degrades_to_no_overrides(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{not json", encoding="utf-8")

    assert OperatorSettingsStore(path).read_memory().is_empty


def test_resolve_overlays_overrides_on_config_defaults():
    # Empty overrides -> the code defaults verbatim.
    base = resolve_memory_settings(MemoryOverrides())
    assert base.memory_dir == config.memory_dir
    assert base.git_enabled == config.memory_git_enabled
    assert base.git_author_name == config.memory_git_author_name

    # A set override wins; memory_dir is ~-expanded but raw_memory_dir keeps the input.
    over = resolve_memory_settings(
        MemoryOverrides(memory_dir="~/mem", git_enabled=not config.memory_git_enabled)
    )
    assert over.raw_memory_dir == "~/mem"
    assert "~" not in over.memory_dir  # expanded
    assert over.git_enabled == (not config.memory_git_enabled)
