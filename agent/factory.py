"""Build the Agno Agent. Provider is config-driven so the model swaps without
touching the rest of the stack (Claude default, Ollama for local)."""

from agno.agent import Agent

from agent.tools import DEFAULT_TOOLS
from core.config import config
from core.db import db


def _build_model():
    provider = config.model_provider.lower()
    if provider == "anthropic":
        from agno.models.anthropic import Claude

        client_params = {}
        if config.anthropic_base_url:
            client_params["base_url"] = config.anthropic_base_url
        return Claude(
            id=config.model_id,
            api_key=config.anthropic_api_key,
            auth_token=config.anthropic_auth_token,
            client_params=client_params or None,
        )
    if provider == "ollama":
        from agno.models.ollama import Ollama

        return Ollama(id=config.model_id, host=config.ollama_host)
    raise ValueError(f"Unknown MODEL_PROVIDER: {config.model_provider!r}")


def build_agent() -> Agent:
    return Agent(
        model=_build_model(),
        system_message=config.system_prompt,
        # OpenWebUI sends full history each request, so agent stays stateless.
        markdown=True,
        telemetry=False,
    )


def build_discord_agent() -> Agent:
    return Agent(
        model=_build_model(),
        system_message=config.system_prompt,
        tools=DEFAULT_TOOLS,
        # Persistence: db stores sessions + user memories (see core/db.py).
        db=db,
        # Short-term memory: replay recent turns of THIS session (per Discord thread).
        add_history_to_context=True,
        num_history_runs=10,
        # Long-term memory: auto-extract durable facts per user (keyed by user_id,
        # which DiscordClient forwards as the Discord author id).
        enable_user_memories=True,
        markdown=True,
        telemetry=False,
    )
