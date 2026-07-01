"""The gateway: the platform-neutral seam every channel's presentation layer
plugs into.

A **platform adapter** (`DiscordClient`, the HTTP API's `ApiAdapter`) turns one
transport's native events into calls against the shared `ConversationService`
and renders replies back out in that transport's own format — that half is
each adapter's own concern and stays out of this module. What IS shared and
belongs here:

- `PlatformAdapter` — the minimal structural contract the gateway needs to run
  an adapter (mirrors the `Runner` Protocol precedent in `core/conversation.py`:
  narrow, structural, no forced base class).
- `scoped_user_id` — every adapter must derive `ConversationService`'s
  `user_id` through this before calling `handle`/`handle_stream`/`flush`/
  `context_stats`, so two platforms whose native ids collide (a Discord
  snowflake, a client-chosen API id) never silently share one user's memory.
- `run_gateway` — run several adapters (or any other long-lived service
  coroutine, e.g. an admin uvicorn server) concurrently in one process; the
  first to finish or raise takes the rest down with it.

See ADR 0003.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PlatformAdapter(Protocol):
    """The slice of a channel's presentation layer the gateway drives.

    `platform` is this adapter's namespace for `scoped_user_id` — stable,
    lowercase (e.g. "discord", "api"). `serve_async` connects/binds and runs
    until shutdown (a discord.Client gateway connection, a uvicorn Server).
    """

    platform: str

    async def serve_async(self) -> None: ...


def scoped_user_id(platform: str, external_id: object) -> str:
    """The canonical, platform-namespaced identity fed to `ConversationService`.

    Memory is keyed by `user_id` alone (`FileMemoryStore.scoped`) — without a
    namespace, two platforms whose native ids happen to collide would
    silently share one user's memory. Every adapter derives `user_id` through
    this before calling `handle`/`handle_stream`/`flush`/`context_stats`.
    """
    return f"{platform}:{external_id}"


async def run_gateway(*coros: Coroutine[Any, Any, None]) -> None:
    """Run every coroutine concurrently until one finishes (returns or raises);
    cancel the rest and re-raise that one's exception, if any.

    Each coro is expected to run forever (a gateway connection, a uvicorn
    server) — the first to end takes the whole process down with it rather than
    leaving a half-running process. Cancellation cleanup is best-effort: the
    process exits right after, so a half-closed socket is fine.
    """
    tasks = [asyncio.ensure_future(c) for c in coros]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in pending:
        try:
            await task
        except asyncio.CancelledError:
            pass
    for task in done:
        task.result()
