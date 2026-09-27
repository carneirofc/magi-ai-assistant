"""The typed config file — `magi.yaml` / `$MAGI_HOME/config.yaml`.

A YAML mapping whose keys are `Config` field names (nested groups such as
`channels:` map onto the matching sub-model). It is validated by the same
pydantic model `configure(...)` uses, so a typo or wrong-typed value fails
with a readable error naming the key.

Resolution:

- `magi_home()` is `$MAGI_HOME`, else `~/.magi` — secrets (`.env`), skills,
  logs, and (with the home config) the data directory live here.
- `find_config_file()` prefers `./magi.yaml` (a project-local deployment)
  over `$MAGI_HOME/config.yaml`.
- Relative paths in the config (`memory_dir`, `db_file`, …) resolve against
  the *data root*: the config file's directory, or `MAGI_HOME` when there is
  no file. The CLI changes into it before serving.

Precedence, lowest to highest: `Config` defaults < config file < `configure()`
in code < CLI flags. Loading is always explicit (`load_config`), never at
import, so tests and personas that never call it are unaffected.
"""

import os
import tempfile
from functools import cache
from pathlib import Path

import yaml
from pydantic import TypeAdapter, ValidationError

from magi.core.config import Config, configure, derive, load_secrets
from magi.core.types import JsonObject, JsonValue

CONFIG_FILENAME = "config.yaml"
LOCAL_CONFIG_FILENAME = "magi.yaml"

_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_VALUE: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


class ConfigFileError(ValueError):
    """The config file is unreadable, not a mapping, or fails validation."""


def magi_home() -> Path:
    """`$MAGI_HOME`, or `~/.magi` when unset."""
    raw = os.environ.get("MAGI_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".magi"


def find_config_file(cwd: Path | None = None) -> Path | None:
    """`./magi.yaml` if present, else `$MAGI_HOME/config.yaml` if present."""
    local = (cwd or Path.cwd()) / LOCAL_CONFIG_FILENAME
    if local.is_file():
        return local
    home = magi_home() / CONFIG_FILENAME
    return home if home.is_file() else None


def data_root(path: Path | None) -> Path:
    """Where relative config paths resolve: the file's directory, else home."""
    return path.parent.resolve() if path is not None else magi_home()


def read_config_file(path: Path) -> JsonObject:
    """The raw (unvalidated) mapping in `path`; `{}` for an empty file."""
    try:
        loaded: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigFileError(f"{path}: cannot read config: {exc}") from exc
    if loaded is None:
        return {}
    try:
        return _OBJECT.validate_python(loaded)
    except ValidationError as exc:
        raise ConfigFileError(f"{path}: top level must be a mapping of settings") from exc


@cache
def _defaults() -> Config:
    return Config()


def validate_mapping(data: JsonObject, *, source: str) -> Config:
    """`data` validated as `Config` overrides, with errors naming each key."""
    try:
        return derive(_defaults(), **data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ConfigFileError(f"{source}: invalid config — {problems}") from exc


def load_config_file(path: Path) -> JsonObject:
    """The validated override mapping from `path` (raises `ConfigFileError`)."""
    data = read_config_file(path)
    validate_mapping(data, source=str(path))
    return data


def load_config(path: Path | None = None, *, home: Path | None = None) -> Path | None:
    """Load secrets and the config file into the `config` singleton.

    Uses `path`, else `find_config_file()`. Returns the file that was applied
    (None when there is none — pure defaults plus secrets)."""
    home = home or magi_home()
    load_secrets(home)
    path = path or find_config_file()
    if path is not None:
        configure(**load_config_file(path))
    return path


def parse_value(raw: str) -> JsonValue:
    """A CLI value parsed as a YAML scalar/collection: `8000` → int, `true` →
    bool, `[api, discord]` → list, anything else → the string itself."""
    try:
        loaded: object = yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw
    try:
        return _VALUE.validate_python(loaded)
    except ValidationError:
        return raw


def set_dotted(data: JsonObject, key: str, value: JsonValue) -> JsonObject:
    """A copy of `data` with `a.b.c` set to `value` (intermediate maps created)."""
    head, _, rest = key.partition(".")
    out = dict(data)
    if not rest:
        out[head] = value
        return out
    child = out.get(head)
    out[head] = set_dotted(child if isinstance(child, dict) else {}, rest, value)
    return out


def get_dotted(cfg: Config, key: str) -> object:
    """The effective value at `a.b.c` on a `Config` (raises `KeyError`)."""
    node: object = cfg
    for part in key.split("."):
        if not hasattr(node, part):
            raise KeyError(key)
        node = getattr(node, part)
    return node


def write_config_value(path: Path, key: str, value: JsonValue) -> JsonObject:
    """Set `key` in the config file at `path` (created if missing), validating
    the whole file first and writing atomically. Returns the new mapping."""
    data = read_config_file(path) if path.is_file() else {}
    updated = set_dotted(data, key, value)
    validate_mapping(updated, source=str(path))
    write_config_file(path, updated)
    return updated


def write_config_file(path: Path, data: JsonObject) -> None:
    """Atomically replace `path` with `data` as YAML."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_env_file(path: Path, values: dict[str, str]) -> None:
    """Merge `values` into the dotenv file at `path` (mode 0600), keeping any
    other lines as they are."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    remaining = dict(values)
    out: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    out.extend(f"{k}={v}" for k, v in remaining.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    path.chmod(0o600)


__all__ = [
    "CONFIG_FILENAME",
    "LOCAL_CONFIG_FILENAME",
    "ConfigFileError",
    "data_root",
    "find_config_file",
    "get_dotted",
    "load_config",
    "load_config_file",
    "magi_home",
    "parse_value",
    "read_config_file",
    "set_dotted",
    "validate_mapping",
    "write_config_file",
    "write_config_value",
    "write_env_file",
]
