"""Entrypoint — the Discord bot, run inside a container.

    python main_discord_docker.py

Same brain as main.py; this only overlays the one setting that differs in a
container (still in code — no new env vars, see core/config). Needs
DISCORD_BOT_TOKEN in .env. The bot is outbound-only, so no port is published.
"""

from magi.core.config import configure
from main import apply_deployment_config


def apply_container_overrides() -> None:
    """The container-only delta from the host deployment."""
    configure(
        # llama-server runs on the host, not in this container. Docker maps the
        # host under this name (compose: extra_hosts host.docker.internal:host-gateway).
        llamacpp_base_url="http://host.docker.internal:8888/v1",
    )


def main() -> None:
    apply_deployment_config()
    apply_container_overrides()

    from magi.channels.discord import build_discord_client, serve_with_admin
    from magi.core.config import config

    discord_client = build_discord_client()
    if config.admin_enabled:
        serve_with_admin(discord_client)
    else:
        discord_client.serve()


if __name__ == "__main__":
    main()
