"""`magi setup` — first-run wizard.

Asks for the model backend, the channels to serve, and the secrets those
channels need, then writes `$MAGI_HOME/config.yaml` (only the values that
differ from the defaults) and merges the secrets into `$MAGI_HOME/.env`
(mode 0600). Re-running it edits the existing files. Input goes through the
injectable `Prompter`, so tests drive it without a terminal.
"""

import getpass
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TextIO, get_args

from magi.core.config import ChannelName, ModelProvider
from magi.core.config_file import (
    CONFIG_FILENAME,
    read_config_file,
    validate_mapping,
    write_config_file,
    write_env_file,
)
from magi.core.types import JsonObject

_PROVIDER_DEFAULTS: dict[str, tuple[str, str]] = {
    # provider: (base-url field, suggested url)
    "llamacpp": ("llamacpp_base_url", "http://127.0.0.1:8080/v1"),
    "openai": ("openai_base_url", "https://api.openai.com/v1"),
    "litellm": ("litellm_base_url", "http://localhost:4000"),
    "ollama": ("ollama_host", "http://localhost:11434"),
}
_PROVIDER_SECRET: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "litellm": "LITELLM_MASTER_KEY",
    "llamacpp": "LLAMACPP_API_KEY",
}


class Prompter(Protocol):
    def ask(self, question: str, default: str) -> str: ...

    def secret(self, question: str) -> str: ...


@dataclass
class TerminalPrompter:
    out: TextIO

    def ask(self, question: str, default: str) -> str:
        answer = input(f"{question} [{default}]: ").strip()
        return answer or default

    def secret(self, question: str) -> str:
        return getpass.getpass(f"{question} (hidden, blank to skip): ").strip()


@dataclass
class SetupResult:
    config_path: Path
    env_path: Path
    config: JsonObject
    secrets_written: list[str] = field(default_factory=list)


def _choice[T: str](prompter: Prompter, question: str, options: tuple[T, ...], default: T) -> T:
    while True:
        answer = prompter.ask(f"{question} ({'/'.join(options)})", default)
        for option in options:
            if answer == option:
                return option


def run_setup(home: Path, prompter: Prompter) -> SetupResult:
    config_path = home / CONFIG_FILENAME
    env_path = home / ".env"
    data: JsonObject = read_config_file(config_path) if config_path.is_file() else {}
    env: dict[str, str] = {}

    providers: tuple[ModelProvider, ...] = get_args(ModelProvider.__value__)
    current = data.get("model_provider")
    provider = _choice(
        prompter,
        "Model backend",
        providers,
        current if isinstance(current, str) and current in providers else "llamacpp",
    )
    data["model_provider"] = provider
    url_field, url_default = _PROVIDER_DEFAULTS[provider]
    existing_url = data.get(url_field)
    data[url_field] = prompter.ask(
        "Backend URL", existing_url if isinstance(existing_url, str) else url_default
    )
    if provider != "ollama":
        existing_model = data.get("lead_model_id")
        model_id = prompter.ask(
            "Model id", existing_model if isinstance(existing_model, str) else "default"
        )
        data["lead_model_id"] = model_id
        data["member_model_id"] = model_id
    ctx = prompter.ask("Context window (tokens)", str(data.get("lead_num_ctx") or 32_000))
    if ctx.isdigit():
        data["lead_num_ctx"] = int(ctx)
        data["member_num_ctx"] = int(ctx)
    if secret_env := _PROVIDER_SECRET.get(provider):
        if key := prompter.secret(f"{secret_env}"):
            env[secret_env] = key

    channel_names: tuple[ChannelName, ...] = get_args(ChannelName.__value__)
    raw_channels = prompter.ask(f"Channels to serve ({', '.join(channel_names)})", "api")
    chosen = [c.strip() for c in raw_channels.split(",") if c.strip() in channel_names]
    data["channels"] = {"enabled": list(dict.fromkeys(chosen or ["api"]))}

    if "discord" in chosen and (token := prompter.secret("DISCORD_BOT_TOKEN")):
        env["DISCORD_BOT_TOKEN"] = token
    if "api" in chosen or "admin" in chosen or "desktop" in chosen:
        if prompter.ask("Generate API/admin bearer tokens?", "yes").lower().startswith("y"):
            env.setdefault("API_AUTH_TOKEN", secrets.token_urlsafe(32))
            env.setdefault("ADMIN_AUTH_TOKEN", secrets.token_urlsafe(32))

    validate_mapping(data, source=str(config_path))
    write_config_file(config_path, data)
    if env:
        write_env_file(env_path, env)
    return SetupResult(config_path, env_path, data, sorted(env))
