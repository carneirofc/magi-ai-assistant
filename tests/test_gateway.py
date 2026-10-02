"""Tests for `magi.channels.gateway` — the platform-neutral seam (ADR 0003):
`run_gateway`'s asyncio orchestration, `scoped_user_id`'s identity namespacing,
and that both shipped adapters (`DiscordClient`, `ApiAdapter`) structurally
satisfy `PlatformAdapter`.
"""

import asyncio

import pytest
from fastapi import FastAPI

from clients.mydiscord import DiscordClient
from magi.channels.api import ApiAdapter
from magi.channels.gateway import PlatformAdapter, run_gateway, scoped_user_id


# --- run_gateway (relocated from channels.discord._run_until_first_exit) ----
@pytest.mark.asyncio
async def test_first_to_finish_cancels_the_rest():
    cancelled = asyncio.Event()

    async def quick() -> None:
        return None

    async def forever() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    await run_gateway(quick(), forever())

    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_the_finishing_coroutines_exception_propagates():
    async def boom() -> None:
        raise RuntimeError("boom")

    async def forever() -> None:
        await asyncio.sleep(10)

    with pytest.raises(RuntimeError, match="boom"):
        await run_gateway(boom(), forever())


@pytest.mark.asyncio
async def test_a_pending_coroutines_own_exception_does_not_propagate():
    """Only the FIRST-to-finish coroutine's outcome matters — a cancelled
    pending task raising CancelledError during cleanup must not surface as this
    call's error (that would mask the real failure)."""

    async def quick() -> None:
        return None

    async def raises_on_cancel() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            raise

    await run_gateway(quick(), raises_on_cancel())


# --- scoped_user_id -----------------------------------------------------------
def test_scoped_user_id_namespaces_by_platform():
    assert scoped_user_id("discord", 123456789012345678) == "discord:123456789012345678"
    assert scoped_user_id("api", "u1") == "api:u1"


def test_scoped_user_id_distinguishes_colliding_native_ids_across_platforms():
    """The bug this fixes: two platforms whose native ids happen to match must
    not resolve to the same memory-scoping user_id."""
    assert scoped_user_id("discord", "123") != scoped_user_id("api", "123")


# --- PlatformAdapter structural conformance ------------------------------------
def test_discord_client_satisfies_platform_adapter():
    client = DiscordClient.__new__(DiscordClient)
    assert client.platform == "discord"
    assert isinstance(client, PlatformAdapter)


def test_api_adapter_satisfies_platform_adapter():
    adapter = ApiAdapter(FastAPI(), host="127.0.0.1", port=0)
    assert adapter.platform == "api"
    assert isinstance(adapter, PlatformAdapter)


# --- registry: many channels, one brain (ADR 0005) ------------------------------
from magi.channels import registry  # noqa: E402
from magi.channels.telegram import TelegramAdapter  # noqa: E402
from magi.core.config import configure  # noqa: E402
from magi.core.conversation import ConversationService  # noqa: E402
from magi.core.memory import manager as manager_mod  # noqa: E402
from magi.core.memory.manager import MemoryManager  # noqa: E402
from magi.core.memory.store import FileMemoryStore  # noqa: E402


class _NoRunner:
    async def arun(self, **_kwargs: object) -> None:  # never called by these tests
        return None


def _service(tmp_path=None) -> ConversationService:
    import tempfile
    from pathlib import Path

    root = Path(tmp_path or tempfile.mkdtemp()) / "memory"
    memory = MemoryManager(store=FileMemoryStore(root), short_term_max=3)
    return ConversationService(runner=_NoRunner(), memory=memory)  # pyright: ignore[reportArgumentType]


class _FakeAdapter:
    def __init__(self, platform: str) -> None:
        self.platform = platform

    async def serve_async(self) -> None:
        return None


def test_telegram_adapter_satisfies_platform_adapter():
    adapter = TelegramAdapter(_service(), token="t", allowed_users=[])
    assert isinstance(adapter, PlatformAdapter)
    assert adapter.platform == "telegram"


def test_every_adapter_gets_the_same_brain():
    seen: list[ConversationService] = []

    def fake(name: str) -> registry.AdapterBuilder:
        def build(service: ConversationService) -> PlatformAdapter:
            seen.append(service)
            return _FakeAdapter(name)

        return build

    service = _service()
    adapters = registry.build_adapters(
        ["api", "discord"], service, {"api": fake("api"), "discord": fake("discord")}
    )
    assert [a.platform for a in adapters] == ["api", "discord"]
    assert seen == [service, service]


def test_with_guidance_shares_runner_and_memory():
    service = _service()
    view = service.with_guidance("telegram rules")
    assert view.channel_guidance == "telegram rules"
    assert service.channel_guidance == ""
    assert view.runner is service.runner and view.memory is service.memory


def test_resolve_rejects_desktop_in_a_gateway():
    with pytest.raises(registry.ChannelConfigError, match="desktop"):
        registry.resolve_channels(["api", "desktop"])


def test_resolve_rejects_port_collision():
    configure(api_host="127.0.0.1", api_port=9500, admin_host="127.0.0.1", admin_port=9500)
    with pytest.raises(registry.ChannelConfigError, match="9500"):
        registry.resolve_channels(["api", "admin"])


def test_resolve_dedupes_and_adds_legacy_admin():
    configure(admin_enabled=True, api_port=9501, admin_port=9502)
    assert registry.resolve_channels(["discord", "discord"]) == ["discord", "admin"]
    # With the API served, admin is mounted on its app instead of a second server.
    assert registry.resolve_channels(["api", "discord"]) == ["api", "discord"]


async def test_concurrent_adapters_do_not_share_memory_scope(tmp_path):
    """Each adapter's handlers run in their own asyncio tasks; the ambient memory
    scope (a ContextVar) must never bleed from one to the other."""
    memory = MemoryManager(store=FileMemoryStore(tmp_path / "memory"), short_term_max=3)
    seen: dict[str, str] = {}

    async def adapter(platform: str) -> None:
        memory.set_scope(scoped_user_id(platform, 1), "s")
        await asyncio.sleep(0.01)  # let the other adapter set its scope
        scope = manager_mod._scope.get()
        assert scope is not None
        seen[platform] = scope.user_id

    await asyncio.gather(adapter("discord"), adapter("telegram"))
    assert seen == {"discord": "discord:1", "telegram": "telegram:1"}


async def test_on_first_exit_lets_the_rest_stop_gracefully():
    stop = asyncio.Event()
    finished_cleanly = asyncio.Event()

    async def quick() -> None:
        return None

    async def graceful() -> None:
        await stop.wait()
        finished_cleanly.set()

    await run_gateway(quick(), graceful(), on_first_exit=stop.set, grace_seconds=1)
    assert finished_cleanly.is_set()


async def test_grace_period_expiry_cancels_stragglers():
    cancelled = asyncio.Event()

    async def quick() -> None:
        return None

    async def stubborn() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    await run_gateway(quick(), stubborn(), on_first_exit=lambda: None, grace_seconds=0.01)
    assert cancelled.is_set()


async def test_first_failure_still_raises_with_graceful_stop():
    stop = asyncio.Event()

    async def boom() -> None:
        raise RuntimeError("boom")

    async def graceful() -> None:
        await stop.wait()

    with pytest.raises(RuntimeError, match="boom"):
        await run_gateway(graceful(), boom(), on_first_exit=stop.set, grace_seconds=1)
