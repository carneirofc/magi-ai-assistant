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
