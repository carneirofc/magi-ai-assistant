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
from inspect import isasyncgen, isgenerator

from agno.utils.log import log_error, log_info, log_warning
from pydantic import BaseModel

# agno names the delegation tool differently for single vs. parallel routing.
_MEMBER_TOOLS = {"delegate_task_to_member", "delegate_task_to_members"}

# Keep log lines readable: arguments/results are truncated to this many chars.
_PREVIEW_LEN = 240


def _preview(value: object) -> str:
    """One-line, length-capped repr for logs.

    Must never raise: previewing a result is pure observability, so a value with
    a broken serializer (e.g. a not-fully-built pydantic model whose serializer
    is still a ``MockValSer``) must degrade to ``repr`` instead of aborting the
    very tool call this hook exists to keep alive.
    """
    try:
        text = value.model_dump_json() if isinstance(value, BaseModel) else str(value)
    except Exception:  # noqa: BLE001 — a broken serializer must not kill the run.
        text = repr(value)
    text = " ".join(text.split())
    return text if len(text) <= _PREVIEW_LEN else f"{text[:_PREVIEW_LEN]}…"


def _event_text(event: object) -> str:
    content = getattr(event, "content", None)
    if content:
        return str(content)
    event_type = getattr(event, "event", None) or type(event).__name__
    return f"[{event_type}]"


class _StreamedAnswer:
    """Rebuilds a delegated member's reply from its drained event stream.

    With a tool hook attached, agno hands us the member's generator instead of
    streaming it itself (see ``Function.aexecute``), so the answer must be
    reassembled here. It arrives one of two ways: as plain strings (the
    non-streaming delegate path yields the whole reply as a block) or as a run
    of ``RunContent`` events (the streaming path yields the reply token by
    token). We keep the two apart so blocks stay newline-separated while token
    deltas concatenate seamlessly.

    Token deltas are accumulated *silently*: logging one line per token was the
    flood this used to cause (worst on the chattiest member, the Prompt Artist).
    Only the rare non-content events — run lifecycle, errors — get a line, so a
    member failure can't vanish.
    """

    def __init__(self, label: str, function_name: str) -> None:
        self._label = label
        self._function_name = function_name
        self._blocks: list[str] = []
        self._deltas: list[str] = []

    def add(self, event: object) -> None:
        if isinstance(event, str):
            self._blocks.append(event)
            return
        if str(getattr(event, "event", "")).endswith("RunContent"):
            content = getattr(event, "content", None)
            if isinstance(content, str):
                self._deltas.append(content)
            return
        log_info(f"  {self._label} '{self._function_name}' event → {_preview(_event_text(event))}")

    def text(self) -> str:
        blocks = [block for block in self._blocks if block]
        delta_text = "".join(self._deltas)
        if delta_text:
            blocks.append(delta_text)
        return "\n".join(blocks)


async def _materialize_result(result: object, label: str, function_name: str) -> object:
    if isasyncgen(result):
        answer = _StreamedAnswer(label, function_name)
        async for event in result:
            answer.add(event)
        return answer.text()
    if isgenerator(result):
        answer = _StreamedAnswer(label, function_name)
        for event in result:
            answer.add(event)
        return answer.text()
    return result


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
        result = await _materialize_result(result, label, function_name)
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
