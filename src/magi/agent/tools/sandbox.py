"""`run_command` — run a shell command in the user's sandboxed workspace.

Bound to the injected `SandboxService` and `MemoryManager`: the user is the
current memory scope, never an argument. Risky commands come back as an
approval request the USER must confirm with `/approve <id>` — the model can
never approve its own command.
"""

import asyncio
from typing import Annotated

from agno.tools import tool
from agno.tools.function import Function
from pydantic import BaseModel, Field

from magi.agent.tools.outputs import ToolOutput, fail, ok
from magi.core.memory import MemoryManager
from magi.core.sandbox import SandboxService


class CommandData(BaseModel):
    status: str = Field(description="'ran' or 'pending' (waiting for the user's approval).")
    exit_code: int | None = None
    output: str = ""
    approval_id: str | None = None


def build_sandbox_tools(sandbox: SandboxService, memory: MemoryManager) -> list[Function]:
    @tool(
        description="Run a shell command in the user's private sandbox workspace.",
        instructions=(
            "Use for real work that needs a shell: run a script, process files, "
            "compute something, inspect data. The working directory is the user's "
            "persistent workspace; the network may be off. If the result is "
            "'pending', STOP and tell the user exactly: reply `/approve <id>` to run "
            "it or `/deny <id>` to cancel — never claim it ran. Don't try to work "
            "around a refusal."
        ),
        show_result=True,
    )
    async def run_command(
        command: Annotated[str, Field(min_length=1, description="A bash command.")],
    ) -> ToolOutput[CommandData]:
        scope = memory.scope()
        outcome = await asyncio.to_thread(
            sandbox.request, user_id=scope.user_id, session_id=scope.session_id, command=command
        )
        if outcome.status == "refused":
            return fail(f"Refused: {outcome.reason}.")
        if outcome.status == "pending":
            return ok(
                f"Needs the user's approval: ask them to reply `/approve {outcome.approval_id}` "
                f"or `/deny {outcome.approval_id}`.",
                CommandData(status="pending", approval_id=outcome.approval_id),
            )
        result = outcome.result
        if result is None:
            return fail("The command produced no result.")
        note = " (timed out)" if result.timed_out else ""
        return ok(
            f"Exit code {result.exit_code}{note}.",
            CommandData(status="ran", exit_code=result.exit_code, output=result.output),
        )

    return [run_command]
