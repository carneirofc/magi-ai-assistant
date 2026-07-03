"""magi.client — the desktop-app front door.

One ergonomic surface (`MagiClient`) with two interchangeable backends, so a GUI
codes against the same calls whether the brain runs in-process or behind HTTP:

    from magi.client import embed, connect, SyncClient

    # In-process: the whole assistant embedded in a Python GUI, no server.
    client = embed(user_id="local", model_provider="llamacpp",
                   llamacpp_base_url="http://127.0.0.1:8888/v1")

    # Remote: talk to a running `python main_api.py`.
    client = connect("http://127.0.0.1:8000", user_id="local")

    # Either one, wrapped for a GUI's blocking/threaded world:
    ui = SyncClient(client)
    print(ui.send("hello").text)
    for chunk in ui.stream("tell me more"):
        ...
    ui.close()

The async surface (`await client.send(...)`, `async for x in client.stream(...)`)
is there directly for async apps; `SyncClient` is the blocking adapter for
toolkits that own the main thread (Tkinter, PyQt/PySide, wx). See
`examples/desktop_chat.py` for a runnable end-to-end GUI.

`embed` and `connect` are the composition roots; the client classes stay thin,
fully-injected adapters (easy to test). `embed` is code-first like the app
entrypoints — pass config overrides straight through and it applies them via
`configure()` before building the stack (secrets still come from `.env`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Optional

from magi.client.base import MagiClient
from magi.client.embedded import EmbeddedClient
from magi.client.http import HttpClient
from magi.client.sync import SyncClient
from magi.client.types import Delta, InboundImage, Media, Reply

if TYPE_CHECKING:
    from agno.agent import Agent
    from agno.db.base import BaseDb
    from agno.models.base import Model

__all__ = [
    "MagiClient",
    "EmbeddedClient",
    "HttpClient",
    "SyncClient",
    "Reply",
    "Delta",
    "Media",
    "InboundImage",
    "embed",
    "connect",
]


def embed(
    user_id: str,
    session_id: str = "default",
    *,
    channel_guidance: Optional[str] = None,
    db: "Optional[BaseDb]" = None,
    member_builders: "Optional[Sequence[Callable[[Model], Agent]]]" = None,
    platform: str = "api",
    **config_overrides: object,
) -> EmbeddedClient:
    """Build an in-process client: wire the full brain from config and wrap it.

    `**config_overrides` are applied via `configure()` before anything is built
    (code-first, exactly like the app entrypoints) — e.g.
    `embed("local", model_provider="llamacpp", llamacpp_base_url=...)`. Secrets
    still come from `.env`.

    Roster mirrors the HTTP channel: the Discord specialist needs a live Discord
    context, so it's left off a desktop deployment unless you pass your own
    `member_builders`. `channel_guidance` defaults to the API channel's output
    guidance (the generic, non-Discord one).
    """
    from magi.agent.members import MEMBER_BUILDERS, build_discord_agent
    from magi.channels.api import _collect_mcp_toolkits
    from magi.channels.bootstrap import build_conversation_service
    from magi.core.config import configure
    from magi.core.prompts import load_prompt

    if config_overrides:
        configure(**config_overrides)

    if member_builders is None:
        member_builders = [b for b in MEMBER_BUILDERS if b is not build_discord_agent]

    conversation = build_conversation_service(
        channel_guidance=channel_guidance
        if channel_guidance is not None
        else load_prompt("channels/api.md"),
        db=db,
        member_builders=member_builders,
    )
    return EmbeddedClient(
        conversation,
        user_id=user_id,
        session_id=session_id,
        platform=platform,
        mcp_toolkits=_collect_mcp_toolkits(conversation.runner),
    )


def connect(
    base_url: str,
    user_id: str,
    session_id: str = "default",
    *,
    auth_token: Optional[str] = None,
    timeout: float = 120.0,
) -> HttpClient:
    """Build a remote client for a running magi HTTP service (`main_api.py`).

    `auth_token` is the service's `API_AUTH_TOKEN` when auth is on. `user_id`
    scopes durable memory (namespaced server-side under the same platform the
    embedded client uses, so the two share one scope for the same id).
    """
    return HttpClient(
        base_url,
        user_id=user_id,
        session_id=session_id,
        auth_token=auth_token,
        timeout=timeout,
    )
