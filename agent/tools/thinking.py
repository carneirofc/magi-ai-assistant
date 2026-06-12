"""Runtime thinking/reasoning toggle for the llama-server backend.

llama-server renders the chat template per request; Qwen3.5 honors
`chat_template_kwargs.enable_thinking` in the request body. The team's model
objects are built once at startup, so these tools mutate their `extra_body` in
place — the next request picks it up. This is a global switch shared by every
session, not per-conversation state.

Why it exists: with thinking ON this finetune sometimes emits a tool call
inside an unclosed think block; the server then can't extract it and the raw
`<tool_call>` XML leaks into `reasoning_content` while `content` stays empty.
Thinking OFF makes tool calls parse reliably (verified on b9550).
"""

from collections.abc import Sequence
from typing import Annotated

from agno.models.base import Model
from agno.tools import tool
from agno.utils.log import log_info
from pydantic import BaseModel, Field

from agent.tools.outputs import ToolOutput, ok


class ThinkingSetData(BaseModel):
    enabled: bool = Field(description="Whether thinking/reasoning mode is enabled.")
    applies_from: str = Field(description="When the changed setting takes effect.")


class ThinkingStateData(BaseModel):
    enabled: bool | None = Field(description="Current thinking state, or null when following the server default.")
    source: str = Field(description="Where the setting came from.")


def _kwargs_of(model: Model) -> dict:
    body = getattr(model, "extra_body", None) or {}
    return body.get("chat_template_kwargs") or {}


def build_thinking_tools(models: Sequence[Model]) -> list:
    """Tools bound to the live model instances (lead + the shared member model)."""

    @tool(
        description="Turn the team's model thinking/reasoning mode on or off.",
        instructions=(
            "Use when the user asks to enable or disable thinking/reasoning. "
            "The change is global and takes effect from the next reply."
        ),
        show_result=True,
    )
    def set_thinking(
        enabled: Annotated[
            bool,
            Field(description="True to enable thinking/reasoning mode; false to disable it."),
        ],
    ) -> ToolOutput[ThinkingSetData]:
        """Turn the model's internal thinking/reasoning mode on or off.

        Use when the user asks to enable or disable thinking (also called
        reasoning). Applies to the whole team from the next reply onward —
        the reply carrying out this call was already generated under the old
        setting.
        """
        for m in models:
            body = dict(getattr(m, "extra_body", None) or {})
            kwargs = dict(body.get("chat_template_kwargs") or {})
            kwargs["enable_thinking"] = enabled
            body["chat_template_kwargs"] = kwargs
            m.extra_body = body
        log_info(f"thinking mode -> {enabled} on {len(models)} model(s)")
        state = "enabled" if enabled else "disabled"
        return ok(
            f"Thinking is now {state} (takes effect from the next reply).",
            ThinkingSetData(enabled=enabled, applies_from="next_reply"),
        )

    @tool(
        description="Report whether model thinking/reasoning mode is enabled.",
        instructions="Use when the user asks whether thinking/reasoning is on. Takes no arguments.",
        show_result=True,
    )
    def get_thinking() -> ToolOutput[ThinkingStateData]:
        """Report whether the thinking/reasoning mode is currently on or off."""
        state = _kwargs_of(models[0]).get("enable_thinking")
        if state is None:
            return ok(
                "Thinking follows the server's default (no override set).",
                ThinkingStateData(enabled=None, source="server_default"),
            )
        return ok(
            f"Thinking is currently {'enabled' if state else 'disabled'}.",
            ThinkingStateData(enabled=bool(state), source="override"),
        )

    return [set_thinking, get_thinking]
