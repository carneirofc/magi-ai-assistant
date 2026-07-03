"""Entrypoint — the Discord bot, run inside a container.

    python main_discord_docker.py

Same brain as main.py; this only overlays the one setting that differs in a
container (still in code — no new env vars, see core/config). Needs
DISCORD_BOT_TOKEN in .env. The bot is outbound-only, so no port is published.
"""

import dataclasses

from magi.core.config import Config
from main import apply_deployment_config


def apply_container_overrides(config: Config) -> Config:
    """The container-only delta from the host deployment."""
    return dataclasses.replace(
        config,
        # llama-server runs on the host, not in this container. Docker maps the
        # host under this name (compose: extra_hosts host.docker.internal:host-gateway).
        llamacpp_base_url="http://host.docker.internal:8888/v1",
    )


def main() -> None:
    config = apply_container_overrides(apply_deployment_config())

    from magi.channels.discord import build_discord_client, serve_with_admin
    from magi.core.context import AgentContext

    ctx = AgentContext(config=config)
    discord_client = build_discord_client(ctx)
    if config.admin_enabled:
        serve_with_admin(ctx, discord_client)
    else:
        discord_client.serve()


if __name__ == "__main__":
    main()
