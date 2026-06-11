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

from agno.models.base import Model
from agno.utils.log import log_info


def _kwargs_of(model: Model) -> dict:
    body = getattr(model, "extra_body", None) or {}
    return body.get("chat_template_kwargs") or {}


def build_thinking_tools(models: Sequence[Model]) -> list:
    """Tools bound to the live model instances (lead + the shared member model)."""

    def set_thinking(enabled: bool) -> str:
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
        return f"Thinking is now {state} (takes effect from the next reply)."

    def get_thinking() -> str:
        """Report whether the thinking/reasoning mode is currently on or off."""
        state = _kwargs_of(models[0]).get("enable_thinking")
        if state is None:
            return "Thinking follows the server's default (no override set)."
        return f"Thinking is currently {'enabled' if state else 'disabled'}."

    return [set_thinking, get_thinking]
