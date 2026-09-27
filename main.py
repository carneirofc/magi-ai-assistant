"""Repo entrypoint — kept for `python main.py <channel>` and the container
commands; new setups use the `magi` CLI (`magi setup`, `magi run`).

    python main.py api                # HTTP API service (default)
    python main.py discord            # Discord bot
    python main.py discord --check    # Discord Phase 1 (raw connect, no agno)
    python main.py admin              # operator admin API (memory + knowledge)
    python main.py desktop            # frameless native shell for the web frontend
    python main.py api --docker       # + docker/magi.docker.yaml overlay

Settings live in ./magi.yaml (validated against core.config.Config); secrets in
.env. Code-first overrides still work: call `configure(...)` after `prepare()`
— they win over the file. This is the place for anything a YAML value cannot
express (registering a persona's skills, members, prompt overlays).
"""

import argparse
from pathlib import Path

from magi.cli.run import check_discord, prepare, run_channels
from magi.core.config import configure

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "magi.yaml"
DOCKER_OVERLAY = ROOT / "docker" / "magi.docker.yaml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a magi channel.")
    parser.add_argument(
        "channel", nargs="?", default="api", choices=["api", "discord", "admin", "desktop"]
    )
    parser.add_argument("--docker", action="store_true", help="apply container-only overrides")
    parser.add_argument("--check", action="store_true", help="discord: raw connect, no agno")
    parser.add_argument("--no-frameless", action="store_true", help="desktop: titled window")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    prepare([CONFIG, DOCKER_OVERLAY] if args.docker else [CONFIG])
    if args.channel == "discord":
        if args.check:
            check_discord()
            return
        # The bot serves the admin surface only when asked; the file's
        # admin_enabled is meant for the API's single-app shape.
        configure(admin_enabled=False)
    run_channels([args.channel], frameless=not args.no_frameless)


if __name__ == "__main__":
    main()
