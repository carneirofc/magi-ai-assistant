"""Tests for the generic MCP registry (spec merge + failure containment) and
the new capability tools: web_search gating, reminders, read_document."""

import json

import pytest

from magi.core.config import config, configure
from magi.core.memory import manager as manager_mod
from magi.core.memory.manager import MemoryManager
from magi.core.memory.store import FileMemoryStore


@pytest.fixture(autouse=True)
def _reset_scope():
    token = manager_mod._scope.set(None)
    yield
    manager_mod._scope.reset(token)


# --- MCP registry ------------------------------------------------------------------


def test_effective_specs_merge_operator_over_code(tmp_path, monkeypatch):
    from magi.agent.tools import mcp as mcp_mod
    from magi.core.settings import OperatorSettingsStore

    store = OperatorSettingsStore(tmp_path / "settings.json")
    store.set_mcp(
        [
            {"name": "comfyui", "enabled": False},  # disable a code-declared server
            {"name": "extra", "url": "http://x/mcp"},  # add a new one
        ]
    )
    monkeypatch.setattr("magi.core.memory.operator_settings_store", lambda: store)

    old = config.mcp_servers
    configure(
        mcp_servers=[
            {"name": "comfyui", "url": "http://c/mcp", "attach": "member"},
            {"name": "search", "url": "http://s/mcp", "attach": "lead"},
        ]
    )
    try:
        specs = {s["name"]: s for s in mcp_mod.effective_mcp_specs()}
    finally:
        configure(mcp_servers=old)

    assert "comfyui" not in specs  # operator disabled it
    assert specs["search"]["attach"] == "lead"  # code entry untouched
    assert specs["extra"]["url"] == "http://x/mcp"  # operator addition
    # The operator override merged FIELD-wise onto the code entry before the
    # enabled filter — url survived even though the entry is disabled.


def test_bad_specs_are_skipped_not_fatal():
    from magi.agent.tools.mcp import build_mcp_lead_toolkits, build_mcp_members

    old = config.mcp_servers
    configure(
        mcp_servers=[
            {"name": "no-url", "attach": "lead"},  # malformed: http transport, no url
            {"name": "no-cmd", "transport": "stdio", "attach": "member"},  # no command
        ]
    )
    try:
        assert build_mcp_lead_toolkits() == []

        class _Model:  # never touched — both specs fail before Agent creation
            id = "m"

        assert build_mcp_members(_Model()) == []
    finally:
        configure(mcp_servers=old)


def test_operator_settings_store_mcp_roundtrip(tmp_path):
    from magi.core.settings import OperatorSettingsStore

    store = OperatorSettingsStore(tmp_path / "settings.json")
    assert store.read_mcp() == []

    stored = store.set_mcp([{"name": "a", "url": "http://a"}, {"noname": True}])
    assert stored == [{"name": "a", "url": "http://a"}]  # nameless entries dropped

    assert store.set_mcp([]) == []  # clearing removes the section
    assert "mcp" not in json.loads(store.path.read_text(encoding="utf-8"))


# --- web search gating -----------------------------------------------------------


def test_websearch_disabled_yields_no_tools():
    from magi.agent.tools.websearch import build_websearch_tools

    assert build_websearch_tools() == []  # websearch_enabled defaults False


# --- reminders -----------------------------------------------------------------------


def _reminder_manager(tmp_path) -> MemoryManager:
    mgr = MemoryManager(store=FileMemoryStore(tmp_path / "memory"), short_term_max=3)
    mgr.set_scope(user_id="u1", session_id="s1")
    return mgr


def test_reminder_tools_gated_by_config(tmp_path):
    from magi.agent.tools.reminders import build_reminder_tools

    assert build_reminder_tools(_reminder_manager(tmp_path)) == []


def test_reminder_set_list_cancel_and_due_text(tmp_path):
    from magi.agent.tools import reminders as rem

    mgr = _reminder_manager(tmp_path)
    old = config.reminders_enabled
    configure(reminders_enabled=True)
    try:
        set_reminder, list_reminders, cancel_reminder = rem.build_reminder_tools(mgr)

        out = set_reminder.entrypoint(text="water the plants", due="2020-01-01T09:00")
        assert out.success
        rid = out.data.reminder.id

        # Due in the past → surfaces in the greeting text.
        due = rem.due_reminders_text(mgr.store.root, "u1")
        assert "water the plants" in due

        assert list_reminders.entrypoint().data.count == 1

        assert cancel_reminder.entrypoint(reminder_id=rid).success
        assert rem.due_reminders_text(mgr.store.root, "u1") == ""  # done → not due

        # Future reminders don't surface yet.
        set_reminder.entrypoint(text="far future", due="2099-01-01")
        assert "far future" not in rem.due_reminders_text(mgr.store.root, "u1")

        # Unparseable due dates are rejected, not stored.
        assert not set_reminder.entrypoint(text="buy milk", due="next friday").success
        assert not cancel_reminder.entrypoint(reminder_id="nope").success
    finally:
        configure(reminders_enabled=old)


# --- read_document ---------------------------------------------------------------------


class _DocStore:
    def __init__(self, data: bytes, ctype: str, filename: str = "doc.txt"):
        self._blob = (data, ctype, {"filename": filename})

    def get_bytes(self, key):
        return self._blob


class _DocMemory:
    def __init__(self):
        from types import SimpleNamespace

        self._scope = SimpleNamespace(user_id="u1")

    def scope(self):
        return self._scope


def _read_document_tool(store):
    from magi.agent.tools.storage import build_storage_tools

    tools = build_storage_tools(store, _DocMemory())
    return next(t for t in tools if t.name == "read_document")


async def test_read_document_extracts_text():
    tool = _read_document_tool(_DocStore(b"hello world, this is the doc", "text/plain"))
    out = await tool.entrypoint(reference="ref1")
    assert out.success and "hello world" in out.data.text and not out.data.truncated


async def test_read_document_truncates_and_marks():
    tool = _read_document_tool(_DocStore(b"x" * 5000, "text/plain"))
    out = await tool.entrypoint(reference="ref1", max_chars=1_000)
    assert out.success and out.data.truncated and "…[truncated" in out.data.text


async def test_read_document_rejects_binary():
    tool = _read_document_tool(_DocStore(b"\xff\xfe\x00binary", "image/png", "pic.png"))
    out = await tool.entrypoint(reference="ref1")
    assert not out.success
