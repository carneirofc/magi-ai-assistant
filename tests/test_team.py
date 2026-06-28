"""Tests for the team's introspection tool (agent/team).

`agent_introspection` once returned a raw dict payload, which pydantic coerced to
a bare BaseModel — unserializable, and it crashed the tool hook on preview. This
pins the result to a concrete, serializable payload.
"""

from types import SimpleNamespace

from magi.agent.team import IntrospectionData, _build_introspection_tool
from magi.agent.tools.outputs import ToolOutput


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
