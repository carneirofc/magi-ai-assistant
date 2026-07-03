"""Tests for HTTP tool host safety exceptions."""

from urllib.parse import quote

import magi.agent.tools.http as http_tools
from magi.core.config import Config

config = Config()


async def test_host_guard_allows_configured_seanime_image_proxy_get():
    url = (
        f"{config.seanime_base_url}/api/v1/image-proxy?"
        f"url={quote('https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/493.jpg', safe='')}"
    )

    allowed, reason = await http_tools._host_allowed(url, config, method="GET")

    assert allowed is True
    assert reason == ""


async def test_host_guard_still_blocks_other_localhost_paths():
    url = f"{config.seanime_base_url}/api/v1/status"

    allowed, reason = await http_tools._host_allowed(url, config, method="GET")

    assert allowed is False
    assert "non-public address" in reason


async def test_host_guard_blocks_mutating_methods_to_image_proxy():
    url = (
        f"{config.seanime_base_url}/api/v1/image-proxy?"
        f"url={quote('https://example.com/x.jpg', safe='')}"
    )

    allowed, reason = await http_tools._host_allowed(url, config, method="POST")

    assert allowed is False
    assert "non-public address" in reason
