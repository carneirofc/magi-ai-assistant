"""The one surface a desktop app codes against.

`MagiClient` is the structural contract both backends satisfy — the in-process
`EmbeddedClient` and the remote `HttpClient`. A GUI depends on this shape, not on
which backend is behind it, so an app can start embedded and later split to a
server (or the reverse) without touching a single call site.

`user_id` and `session_id` are bound at construction — a desktop app is naturally
one client per window/session, so ids never thread through call sites. Both share
one memory scope regardless of backend (both namespace under the same platform),
so the *same* `user_id` reaches the *same* durable memory whether embedded or
over HTTP.

Structural, not a base class (mirrors the `Runner`/`PlatformAdapter` Protocol
precedent): narrow, duck-typed, no forced inheritance. All methods are async;
`magi.client.SyncClient` wraps any `MagiClient` in a blocking API for GUI
toolkits whose main thread runs a UI loop.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol, Union, runtime_checkable

from magi.client.types import Delta, InboundImage, Reply


@runtime_checkable
class MagiClient(Protocol):
    """The async surface every backend exposes (see module docstring)."""

    user_id: str
    session_id: str

    async def aopen(self) -> "MagiClient":
        """Ready the client (connect transports / warm MCP). Idempotent; returns
        self so the client can be opened and used in one expression."""
        ...

    async def aclose(self) -> None:
        """Release the client's resources (close transports / MCP)."""
        ...

    async def send(self, text: str, *, images: Sequence[InboundImage] = ()) -> Reply:
        """Run one turn and return the whole reply."""
        ...

    def stream(
        self, text: str, *, images: Sequence[InboundImage] = ()
    ) -> AsyncIterator[Union[Delta, Reply]]:
        """Run one turn, yielding a `Delta` per text chunk then exactly one final
        `Reply` (the authoritative result — render it over the assembled deltas)."""
        ...

    async def flush(self) -> int:
        """Close the session (fold summary, wipe live turns). Returns turns dropped."""
        ...

    async def context_stats(self) -> dict[str, object]:
        """Context-size stats for this session."""
        ...
