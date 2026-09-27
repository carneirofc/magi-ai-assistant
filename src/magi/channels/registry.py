"""Run several channels in one process around ONE shared brain (ADR 0005).

`serve(["api", "discord", "telegram", "admin"])` builds the conversation stack
once (`bootstrap.build_conversation_service`), hands each adapter a view of it
with that channel's own output guidance (`ConversationService.with_guidance`),
and runs them all through `gateway.run_gateway` — the first to stop takes the
rest down.

Special cases, preserved from the single-channel entry points:

- `admin` alone runs model-free (no conversation stack is built at all).
- `desktop` owns the Qt main loop, so it cannot share a process.
- The Discord specialist joins the shared roster only when Discord is served.
- `config.admin_enabled` still mounts admin onto the API app when `api` is
  served, or adds the `admin` server when it is not.
"""

import asyncio
from collections.abc import Callable, Sequence

from agno.db.base import BaseDb
from agno.utils.log import log_info

from magi.channels.gateway import PlatformAdapter, Stoppable, run_gateway
from magi.core.config import ChannelName, config
from magi.core.conversation import ConversationService

type AdapterBuilder = Callable[[ConversationService], PlatformAdapter]

GATEWAY_CHANNELS: tuple[ChannelName, ...] = ("api", "discord", "telegram", "admin")


class ChannelConfigError(ValueError):
    """The requested channel set cannot run as one process."""


def _api(service: ConversationService) -> PlatformAdapter:
    from magi.channels.api import ApiAdapter, build_api_app_for

    return ApiAdapter(build_api_app_for(service), host=config.api_host, port=config.api_port)


def _discord(service: ConversationService) -> PlatformAdapter:
    from magi.channels.discord import build_discord_client

    return build_discord_client(conversation=service)


def _telegram(service: ConversationService) -> PlatformAdapter:
    from magi.channels.telegram import build_telegram_adapter

    return build_telegram_adapter(service)


def _admin(service: ConversationService) -> PlatformAdapter:
    from magi.channels.admin import build_admin_adapter

    return build_admin_adapter(memory_manager=service.memory)


ADAPTER_BUILDERS: dict[ChannelName, AdapterBuilder] = {
    "api": _api,
    "discord": _discord,
    "telegram": _telegram,
    "admin": _admin,
}


def resolve_channels(names: Sequence[ChannelName]) -> list[ChannelName]:
    """Validate and normalise a channel set for one gateway process."""
    chosen = list(dict.fromkeys(names))
    if not chosen:
        raise ChannelConfigError("no channels to serve")
    if "desktop" in chosen:
        raise ChannelConfigError("desktop runs its own UI loop — run it on its own")
    if config.admin_enabled and "api" not in chosen and "admin" not in chosen:
        chosen.append("admin")  # legacy: admin alongside a non-HTTP channel
    ports: dict[tuple[str, int], ChannelName] = {}
    for name, host, port in (
        ("api", config.api_host, config.api_port),
        ("admin", config.admin_host, config.admin_port),
    ):
        if name not in chosen:
            continue
        if (other := ports.get((host, port))) is not None:
            raise ChannelConfigError(f"{name} and {other} both bind {host}:{port}")
        ports[(host, port)] = name
    return chosen


def build_shared_service(
    names: Sequence[ChannelName], db: BaseDb | None = None
) -> ConversationService:
    """The one brain every served channel shares."""
    from magi.agent.members import MEMBER_BUILDERS, build_discord_agent
    from magi.channels.bootstrap import build_conversation_service

    members = [b for b in MEMBER_BUILDERS if "discord" in names or b is not build_discord_agent]
    return build_conversation_service(channel_guidance="", db=db, member_builders=members)


def build_adapters(
    names: Sequence[ChannelName],
    service: ConversationService,
    builders: dict[ChannelName, AdapterBuilder] | None = None,
) -> list[PlatformAdapter]:
    table = builders if builders is not None else ADAPTER_BUILDERS
    return [table[name](service) for name in names]


async def serve_async(names: Sequence[ChannelName], db: BaseDb | None = None) -> None:
    chosen = resolve_channels(names)
    log_info(f"gateway: serving {', '.join(chosen)} in one process")
    if chosen == ["admin"]:
        adapters: list[PlatformAdapter] = [_admin_standalone()]
    else:
        service = build_shared_service(chosen, db)
        adapters = build_adapters(chosen, service)

    def stop_all() -> None:
        for adapter in adapters:
            if isinstance(adapter, Stoppable):
                adapter.request_stop()

    await run_gateway(*(a.serve_async() for a in adapters), on_first_exit=stop_all)


def _admin_standalone() -> PlatformAdapter:
    from magi.channels.admin import build_admin_adapter

    return build_admin_adapter()  # model-free memory manager


def serve(names: Sequence[ChannelName], db: BaseDb | None = None) -> None:
    """Blocking form of `serve_async`."""
    asyncio.run(serve_async(names, db))
