"""Env-driven configuration. Single source of truth for model + server settings."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from core.prompts import load_prompt

load_dotenv()


@dataclass(frozen=True)
class Config:
    # Model selection (model-agnostic: swap provider without touching agent code).
    model_provider: str = os.getenv("MODEL_PROVIDER", "anthropic")  # anthropic | ollama
    model_id: str = os.getenv("MODEL_ID", "claude-sonnet-4-5-20250929")
    anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY")
    anthropic_auth_token: str | None = os.getenv("ANTHROPIC_AUTH_TOKEN")
    anthropic_base_url: str | None = os.getenv("ANTHROPIC_BASE_URL")
    ollama_host: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")

    # Agent behavior. Edit prompts/system.md to change the default brain; the
    # SYSTEM_PROMPT env var overrides the file for per-deploy customization.
    system_prompt: str = os.getenv("SYSTEM_PROMPT") or load_prompt(
        "system.md", "You are a helpful personal AI assistant."
    )

    # Persistence (sessions + long-term user memories).
    db_file: str = os.getenv("DB_FILE", "data/chatbot.db")

    # Whether the selected model supports tool/function calling. Some local
    # Ollama models (e.g. gpt-oss) reject tools with HTTP 400 — set false there.
    tools_enabled: bool = os.getenv("MODEL_SUPPORTS_TOOLS", "true").lower() == "true"

    # Discord bot.
    DISCORD_BOT_TOKEN: str | None = os.getenv("DISCORD_BOT_TOKEN")

    # OpenAI-compatible API server.
    api_token: str = os.getenv("API_TOKEN", "change-me")
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))

    @property
    def model_name(self) -> str:
        """Name surfaced to OpenWebUI's model list."""
        return f"{self.model_provider}:{self.model_id}"


config = Config()
