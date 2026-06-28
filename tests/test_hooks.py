"""Tests for the team tool-call hook (agent/hooks).

The hook is the team's observability + robustness layer: it must let a normal
call through unchanged, and it must turn a raising tool into a lead-visible
ERROR string instead of letting the exception abort the run.
"""

from magi.agent.hooks import _preview, tool_call_hook
from magi.agent.tools.outputs import FlexiblePayload, ok


async def test_hook_passes_through_success():
    async def ok(**kwargs):
        return f"did {kwargs['x']}"

    result = await tool_call_hook("some_tool", ok, {"x": "thing"})
    assert result == "did thing"


async def test_hook_converts_failure_to_lead_visible_error():
    async def boom(**kwargs):
        raise RuntimeError("backend down")

    result = await tool_call_hook("delegate_task_to_member", boom, {"member_id": "x", "task": "t"})
    assert "ERROR" in result
    assert "delegate_task_to_member" in result
    assert "backend down" in result


async def test_hook_warns_on_empty_member_result(caplog):
    async def empty(**kwargs):
        return "   "

    result = await tool_call_hook("delegate_task_to_member", empty, {"member_id": "x", "task": "t"})
    assert result == "   "  # passed through untouched


async def test_hook_materializes_async_generator_member_result():
    async def delegate(**kwargs):
        async def events():
            yield "first"
            yield "second"

        return events()

    result = await tool_call_hook("delegate_task_to_member", delegate, {"member_id": "x", "task": "t"})
    assert result == "first\nsecond"


async def test_hook_reassembles_streamed_member_content_deltas():
    """Streaming path: the member's reply arrives as RunContent token deltas.

    agno yields event objects (not strings) here; the hook must concatenate
    their content into the answer instead of dropping it (which would feed the
    lead an empty delegation) and must not log one line per token.
    """

    class _Delta:
        def __init__(self, content):
            self.event = "RunContent"
            self.content = content

    class _Lifecycle:
        event = "RunCompleted"
        content = None

    async def delegate(**kwargs):
        async def events():
            yield _Delta("Hello")
            yield _Delta(", ")
            yield _Lifecycle()  # non-content event: ignored for the answer text
            yield _Delta("world")

        return events()

    result = await tool_call_hook("delegate_task_to_member", delegate, {"member_id": "x", "task": "t"})
    assert result == "Hello, world"


def test_preview_serializes_pydantic_outputs_as_json():
    result = ok("done", FlexiblePayload(text="payload"))

    preview = _preview(result)

    assert '"message":"done"' in preview
    assert '"data":{"text":"payload"}' in preview
