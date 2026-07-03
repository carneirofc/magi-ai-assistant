"""Discord entrypoints.

  python main_discord.py check   # Phase 1: connect to server, announce in #general, log messages
  python main_discord.py serve   # Phase 2: run full agno-powered bot (default)
"""

import sys


def check() -> None:
    """Phase 1 — raw discord.py, no agno. See channels/discord_check.py for event handlers."""
    import asyncio

    from magi.core.config import Config
    from magi.channels import discord_check

    token = Config().DISCORD_BOT_TOKEN
    if not token:
        print("ERROR: DISCORD_BOT_TOKEN not set in environment")
        sys.exit(1)

    asyncio.run(discord_check.run(token))


def serve() -> None:
    """Phase 2 — full agno integration via DiscordClient."""
    from main import apply_deployment_config

    from magi.channels.discord import build_discord_client
    from magi.core.context import AgentContext

    ctx = AgentContext(config=apply_deployment_config())
    print("[serve] Building agno Discord client...")
    discord_client = build_discord_client(ctx)
    print("[serve] Starting bot. Press Ctrl+C to stop.")
    discord_client.serve()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "check":
        check()
    elif cmd == "serve":
        serve()
    else:
        print(f"Unknown command: {cmd!r}. Use 'check' or 'serve'.")
        sys.exit(1)
