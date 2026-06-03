"""Agent construction.

`build_model` and `build_agent` are the injectable primitives — every argument
defaults from config but can be overridden, so behavior is parameterized rather
than hard-coded. The named presets below (stateless / discord) just pick sensible
defaults for each channel by calling `build_agent`.
"""

from collections.abc import Sequence

from agno.agent import Agent
from agno.db.base import BaseDb
from agno.models.base import Model

from agent.tools import DEFAULT_TOOLS
from core.config import config
from core.db import get_db


def build_model(provider: str | None = None, model_id: str | None = None) -> Model:
    """Provider-agnostic model factory. Defaults from config; override per call."""
    provider = (provider or config.model_provider).lower()
    model_id = model_id or config.model_id
    if provider == "anthropic":
        from agno.models.anthropic import Claude

        client_params = {}
        if config.anthropic_base_url:
            client_params["base_url"] = config.anthropic_base_url
        return Claude(
            id=model_id,
            api_key=config.anthropic_api_key,
            auth_token=config.anthropic_auth_token,
            client_params=client_params or None,
        )
    if provider == "ollama":
        from agno.models.ollama import Ollama

        return Ollama(id=model_id, host=config.ollama_host)
    raise ValueError(f"Unknown MODEL_PROVIDER: {provider!r}")


def build_agent(
    *,
    model: Model | None = None,
    system_message: str | None = None,
    tools: Sequence | None = None,
    db: BaseDb | None = None,
    add_history_to_context: bool = False,
    num_history_runs: int = 10,
    enable_user_memories: bool = False,
    markdown: bool = True,
) -> Agent:
    """Generic, fully-injectable agent builder.

    Every arg defaults from config / off, so callers opt into exactly what they
    need. Memory (`enable_user_memories`) and history both require a `db`.
    """
    return Agent(
        model=model or build_model(),
        system_message=system_message or config.system_prompt,
        tools=list(tools) if tools is not None else list(DEFAULT_TOOLS),
        db=db,
        add_history_to_context=add_history_to_context,
        num_history_runs=num_history_runs,
        enable_user_memories=enable_user_memories,
        markdown=markdown,
        telemetry=False,
    )


def build_stateless_agent(**overrides) -> Agent:
    """OpenWebUI preset: caller supplies full history each request, so no db."""
    return build_agent(**overrides)


def build_discord_agent(db: BaseDb | None = None, **overrides) -> Agent:
    """Discord preset: agent owns the session, so persist history + user memory."""
    return build_agent(
        db=db or get_db(),
        add_history_to_context=True,
        enable_user_memories=True,
        **overrides,
    )
