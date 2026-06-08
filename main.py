"""Entrypoint — run the Discord bot backed by the multimodal agent team."""

from channels.discord import build_discord_client


def main() -> None:
    build_discord_client().serve()


if __name__ == "__main__":
    main()
