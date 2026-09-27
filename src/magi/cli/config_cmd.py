"""`magi config path | show | get KEY | set KEY VALUE`."""

from pathlib import Path
from typing import TextIO

from magi.core.config import config
from magi.core.config_file import (
    CONFIG_FILENAME,
    find_config_file,
    get_dotted,
    magi_home,
    parse_value,
    write_config_value,
)


def target_file(explicit: Path | None) -> Path:
    """The file `config set` edits: `--file`, else the one in use, else home."""
    if explicit is not None:
        return explicit
    return find_config_file() or magi_home() / CONFIG_FILENAME


def cmd_path(out: TextIO) -> int:
    found = find_config_file()
    out.write(f"MAGI_HOME   {magi_home()}\n")
    out.write(f"config file {found or '(none — defaults only)'}\n")
    return 0


def cmd_show(out: TextIO) -> int:
    for line in config.settings_lines():
        out.write(line + "\n")
    return 0


def cmd_get(key: str, out: TextIO) -> int:
    try:
        value = get_dotted(config, key)
    except KeyError:
        out.write(f"unknown setting: {key}\n")
        return 2
    out.write(f"{value}\n")
    return 0


def cmd_set(key: str, raw_value: str, file: Path | None, out: TextIO) -> int:
    path = target_file(file)
    write_config_value(path, key, parse_value(raw_value))
    out.write(f"{key} set in {path}\n")
    return 0
