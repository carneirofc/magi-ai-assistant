"""Tests for the team assembly (agent/team).

`agent_introspection` once returned a raw dict payload, which pydantic coerced to
a bare BaseModel — unserializable, and it crashed the tool hook on preview. This
pins the result to a concrete, serializable payload.

A second regression: storage tools were built but never spliced into the lead's
`tools=[...]`, so the byte-archive (store/retrieve/list) never reached the model
even with storage enabled. The build test below pins them onto the lead.
"""

from dataclasses import fields
from types import SimpleNamespace

import pytest

from magi.agent.team import IntrospectionData, _build_introspection_tool, build_team
from magi.agent.tools.outputs import ToolOutput
from magi.core.config import config, configure
from magi.core.memory import build_memory_from_config


def test_introspection_returns_serializable_payload():
    lead = SimpleNamespace(id="lead-model")
    members = [SimpleNamespace(name="anime", role="anime stuff")]

    tool = _build_introspection_tool(lead, members)
    result = tool.entrypoint(reason="deciding route")

    assert isinstance(result, ToolOutput)
    assert isinstance(result.data, IntrospectionData)
    # The regression: this raised PydanticSerializationError (MockValSer) before.
    payload = result.model_dump_json()
    assert '"lead_model":"lead-model"' in payload
    assert '"name":"anime"' in payload


@pytest.fixture
def restore_config():
    """Snapshot/restore the global config singleton around a test that mutates it."""
    snapshot = {f.name: getattr(config, f.name) for f in fields(config)}
    yield
    configure(**snapshot)


def _lead_tool_names(team) -> list[str]:
    return [getattr(t, "name", type(t).__name__) for t in (team.tools or [])]


def test_build_team_attaches_storage_tools_when_enabled(tmp_path, restore_config):
    # Local backend: no server/boto3 needed, bytes land under a temp dir.
    configure(
        storage_enabled=True,
        storage_backend="local",
        storage_local_dir=str(tmp_path / "artifacts"),
        memory_dir=str(tmp_path / "memory"),
    )
    team = build_team(build_memory_from_config())
    names = _lead_tool_names(team)
    # The regression: these were absent despite storage being enabled.
    for tool_name in ("store_file", "retrieve_file", "list_files"):
        assert tool_name in names, f"{tool_name} not attached to the lead"


def test_build_team_omits_storage_tools_when_disabled(tmp_path, restore_config):
    configure(storage_enabled=False, memory_dir=str(tmp_path / "memory"))
    team = build_team(build_memory_from_config())
    names = _lead_tool_names(team)
    assert not ({"store_file", "retrieve_file", "list_files"} & set(names))
