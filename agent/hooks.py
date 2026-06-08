"""Team tool-call observability + robustness.

A single async `tool_hook` is attached to every tool the lead can call — the
specialist members (`delegate_task_to_member(s)`), web search, introspection and
the memory tools (agno copies the team's `tool_hooks` onto each, see
`agno/team/_tools.py`). It gives two things the bare team lacks:

  - **Debuggability** — every call is logged with its arguments, elapsed time and
    a snippet of what came back. Member delegations are tagged `MEMBER` so you can
    see *who* the lead routed to and *what* they answered, in order.
  - **Robustness** — a tool that raises no longer aborts the whole run: the hook
    catches it, logs it, and hands the lead an explicit ``ERROR: ...`` string so
    the lead *knows* the step failed and can react (retry, reroute, or tell the
    user) instead of the run dying with a stack trace.

Note on member failures: agno's delegate tool catches a member's exception
internally and returns the message as the member's "answer" (it does not re-raise),
so the lead already sees the text. The hook still logs the call and flags an empty
member response, which is the other way a member silently fails.
"""

import time

from agno.utils.log import log_error, log_info, log_warning

# agno names the delegation tool differently for single vs. parallel routing.
_MEMBER_TOOLS = {"delegate_task_to_member", "delegate_task_to_members"}

# Keep log lines readable: arguments/results are truncated to this many chars.
_PREVIEW_LEN = 240


def _preview(value: object) -> str:
    """One-line, length-capped repr for logs."""
    text = " ".join(str(value).split())
    return text if len(text) <= _PREVIEW_LEN else f"{text[:_PREVIEW_LEN]}…"


async def tool_call_hook(function_name: str, function_call, arguments: dict):
    """Wrap one tool call: log it, time it, and turn failures into lead-visible text.

    Attached as the team's only `tool_hook`. `function_call` is the next link in
    agno's hook chain (already async here, since the run uses `arun`), so we await
    it and pass the arguments straight through.
    """
    is_member = function_name in _MEMBER_TOOLS
    label = "MEMBER" if is_member else "tool"
    log_info(f"→ {label} call: {function_name}({_preview(arguments)})")

    started = time.perf_counter()
    try:
        result = await function_call(**arguments)
    except Exception as exc:  # noqa: BLE001 — deliberately broad: keep the run alive.
        elapsed_ms = (time.perf_counter() - started) * 1000
        log_error(
            f"✗ {label} '{function_name}' FAILED after {elapsed_ms:.0f}ms: "
            f"{type(exc).__name__}: {exc}"
        )
        # Don't propagate: feed the lead an explicit failure it can act on.
        return (
            f"ERROR: {label} '{function_name}' failed ({type(exc).__name__}: {exc}). "
            "This step did not complete — tell the user or try an alternative."
        )

    elapsed_ms = (time.perf_counter() - started) * 1000
    log_info(f"← {label} '{function_name}' ok in {elapsed_ms:.0f}ms → {_preview(result)}")
    if is_member and not str(result).strip():
        log_warning(
            f"member call '{function_name}' returned empty content — the member may "
            "have failed silently; the lead will see nothing useful from it"
        )
    return result
