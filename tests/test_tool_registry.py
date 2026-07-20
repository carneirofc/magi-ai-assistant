"""Tests for the tool registration seam (agent/tools).

`register_member` opened the team roster to persona overlays, but tools still
required editing DEFAULT_TOOLS / team.py in the engine tree. These pin the tool
twin of that seam: `register_tool` (member default set), `register_lead_toolkit`
(lead-level, memory-injected), and the `registered_lead_tools` resolver's
degrade-don't-abort contract.
"""

import pytest

from magi.agent.tools import (
    DEFAULT_TOOLS,
    LEAD_TOOLKIT_BUILDERS,
    enabled_tools,
    register_lead_toolkit,
    register_tool,
    registered_lead_tools,
)


@pytest.fixture(autouse=True)
def restore_registries():
    """Snapshot/restore the module-global registries around every test."""
    default_snapshot = list(DEFAULT_TOOLS)
    lead_snapshot = list(LEAD_TOOLKIT_BUILDERS)
    yield
    DEFAULT_TOOLS[:] = default_snapshot
    LEAD_TOOLKIT_BUILDERS[:] = lead_snapshot


def test_register_tool_reaches_enabled_tools_and_is_idempotent():
    def persona_action():
        """A persona-registered capability."""

    assert register_tool(persona_action) is persona_action  # decorator-usable
    register_tool(persona_action)  # re-registering must not duplicate

    assert DEFAULT_TOOLS.count(persona_action) == 1
    assert persona_action in enabled_tools()
    # An explicit list still overrides the default set entirely.
    assert persona_action not in enabled_tools([])


def test_register_lead_toolkit_is_idempotent_and_receives_memory():
    seen = []

    @register_lead_toolkit
    def build_persona_tools(memory):
        seen.append(memory)
        return ["tool-a", "tool-b"]

    register_lead_toolkit(build_persona_tools)
    assert LEAD_TOOLKIT_BUILDERS.count(build_persona_tools) == 1

    memory = object()
    assert registered_lead_tools(memory) == ["tool-a", "tool-b"]
    assert seen == [memory]


def test_registered_lead_tools_skips_raising_builder():
    def broken(memory):
        raise RuntimeError("persona toolkit is misconfigured")

    def healthy(memory):
        return ["tool-c"]

    register_lead_toolkit(broken)
    register_lead_toolkit(healthy)

    # The broken builder degrades to nothing; the healthy one still lands.
    assert registered_lead_tools(object()) == ["tool-c"]
