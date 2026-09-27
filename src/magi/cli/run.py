"""`magi run` — load the config and serve channels.

`prepare()` is the one place the CLI turns files into live config: secrets
(`$MAGI_HOME/.env`, `./.env`), then each config file in order, then a change
into the data root so relative paths (`data/memory`, `data/chatbot.db`, …)
land beside the config file — or under `MAGI_HOME` when there is none.
"""

import os
import sys
from collections.abc import Sequence
from pathlib import Path

from magi.core.config import ChannelName, config, configure, load_secrets
from magi.core.config_file import (
    data_root,
    find_config_file,
    load_config_file,
    magi_home,
)


def prepare(config_paths: Sequence[Path] = ()) -> Path | None:
    """Apply secrets and config files to the singleton; chdir to the data root.

    `config_paths` are applied in order (later wins); when empty the usual
    lookup (`./magi.yaml`, then `$MAGI_HOME/config.yaml`) is used. Returns the
    primary config file (None = defaults only)."""
    home = magi_home()
    home.mkdir(parents=True, exist_ok=True)
    load_secrets(home)
    paths = [p.expanduser().resolve() for p in config_paths]
    if not paths and (found := find_config_file()) is not None:
        paths = [found.resolve()]
    for path in paths:
        configure(**load_config_file(path))
    primary = paths[0] if paths else None
    root = data_root(primary)
    root.mkdir(parents=True, exist_ok=True)
    os.chdir(root)
    return primary


def run_desktop(*, frameless: bool = True) -> None:
    # Lazy: the optional `desktop` extra (PySide6) is only needed here.
    from magi.desktop import run_desktop as _run_desktop

    if not frameless:
        configure(desktop_frameless=False)
    sys.exit(_run_desktop())


def check_discord() -> None:
    """`python main.py discord --check`: raw discord.py connect, no agno
    (channels/discord_check.py) — isolates token/intent problems."""
    import asyncio

    from magi.channels import discord_check

    if not config.DISCORD_BOT_TOKEN:
        raise SystemExit("DISCORD_BOT_TOKEN is not set (add it to $MAGI_HOME/.env)")
    asyncio.run(discord_check.run(config.DISCORD_BOT_TOKEN))


def run_channels(names: Sequence[ChannelName], *, frameless: bool = True) -> None:
    """Serve `names` (default: `config.channels.enabled`).

    `desktop` runs on its own (it owns the Qt loop); everything else runs in one
    gateway process around a single shared brain (`channels/registry.py`)."""
    from magi.channels.registry import ChannelConfigError, serve

    chosen = list(names) or list(config.channels.enabled)
    if chosen == ["desktop"]:
        run_desktop(frameless=frameless)
        return
    try:
        serve(chosen)
    except ChannelConfigError as exc:
        raise SystemExit(f"magi: {exc}") from exc
