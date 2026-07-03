"""Tests for the team assembly (agent/team).

`agent_introspection` once returned a raw dict payload, which pydantic coerced to
a bare BaseModel — unserializable, and it crashed the tool hook on preview. This
pins the result to a concrete, serializable payload.

A second regression: storage tools were built but never spliced into the lead's
`tools=[...]`, so the byte-archive (store/retrieve/list) never reached the model
even with storage enabled. The build test below pins them onto the lead.
"""

import dataclasses
from types import SimpleNamespace

from magi.agent.team import IntrospectionData, _build_introspection_tool, build_team
from magi.agent.tools.outputs import ToolOutput
from magi.core.config import Config
from magi.core.context import AgentContext
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


def _lead_tool_names(team) -> list[str]:
    return [getattr(t, "name", type(t).__name__) for t in (team.tools or [])]


def test_build_team_attaches_storage_tools_when_enabled(tmp_path):
    # Local backend: no server/boto3 needed, bytes land under a temp dir.
    config = dataclasses.replace(
        Config(),
        storage_enabled=True,
        storage_backend="local",
        storage_local_dir=str(tmp_path / "artifacts"),
        memory_dir=str(tmp_path / "memory"),
    )
    ctx = AgentContext(config=config)
    team = build_team(ctx, build_memory_from_config(config))
    names = _lead_tool_names(team)
    # The regression: these were absent despite storage being enabled.
    for tool_name in ("store_file", "retrieve_file", "list_files"):
        assert tool_name in names, f"{tool_name} not attached to the lead"


def test_build_team_omits_storage_tools_when_disabled(tmp_path):
    config = dataclasses.replace(
        Config(), storage_enabled=False, memory_dir=str(tmp_path / "memory")
    )
    ctx = AgentContext(config=config)
    team = build_team(ctx, build_memory_from_config(config))
    names = _lead_tool_names(team)
    assert not ({"store_file", "retrieve_file", "list_files"} & set(names))
