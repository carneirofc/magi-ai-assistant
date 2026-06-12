"""Tests for the team tool-call hook (agent/hooks).

The hook is the team's observability + robustness layer: it must let a normal
call through unchanged, and it must turn a raising tool into a lead-visible
ERROR string instead of letting the exception abort the run.
"""

from agent.hooks import tool_call_hook


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
