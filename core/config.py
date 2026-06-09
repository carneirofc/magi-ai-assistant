"""Env-driven configuration. Single source of truth for models + channels.

Everything reaches its backend through the LiteLLM proxy (see litellm.config.yaml),
so the only model wiring this app needs is *which* proxy model_name to use per
role and how big a context window to give it. Change a model by editing the env
vars / defaults here — no agent code touched.
"""

import os
from dataclasses import dataclass, fields

from agno.utils.log import log_info
from dotenv import load_dotenv

from core.prompts import load_prompt

load_dotenv()


def _int_env(name: str, default: int) -> int:
    """Read an int env var, falling back to `default` when unset/blank."""
    raw = os.getenv(name)
    return int(raw) if raw else default


def _bool_env(name: str, default: bool) -> bool:
    """Read a bool env var. Truthy: 1/true/yes/on (case-insensitive)."""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _ollama_host(default: str = "http://localhost:11434") -> str:
    """Resolve OLLAMA_HOST into a connectable client URL.

    Tolerates two common mistakes when the var is set for the *server* instead
    of the client: a missing scheme, and 0.0.0.0 (a bind address, unroutable as
    a connect target — rewrite to 127.0.0.1).
    """
    raw = (os.getenv("OLLAMA_HOST") or "").strip()
    if not raw:
        return default
    if "://" not in raw:
        raw = f"http://{raw}"
    return raw.replace("://0.0.0.0", "://127.0.0.1")


def _mask(secret: str | None) -> str:
    """Render a secret for logs: presence + length, never the value."""
    if not secret:
        return "<unset>"
    return f"<set, {len(secret)} chars, ...{secret[-4:]}>"


@dataclass(frozen=True)
class Config:
    # --- LiteLLM proxy: the one gateway to every backend (Ollama, Databricks…) ---
    litellm_base_url: str = os.getenv("LITELLM_BASE_URL", "http://localhost:4000")
    litellm_api_key: str | None = os.getenv("LITELLM_MASTER_KEY")
    # Only used by the direct-Ollama builder (proxy-bypass path).
    ollama_host: str = _ollama_host()

    # --- Models (ids are litellm proxy model_names; see litellm.config.yaml) ---
    # Which builder serves the roles: `litellm` (the proxy gateway, default) or
    # `ollama` (direct to OLLAMA_HOST, bypassing the proxy — local/offline dev).
    model_provider: str = os.getenv("MODEL_PROVIDER", "litellm")
    # Lead / router brain. Multimodal (image + audio) and a large context window
    # so it can hold long histories plus media tokens.
    lead_model_id: str = os.getenv("LEAD_MODEL_ID", "Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q6_K:latest")
    lead_num_ctx: int = _int_env("LEAD_NUM_CTX", 131072)  # 128k tokens

    # Specialist members. They get focused subtasks, so a smaller window is fine.
    member_model_id: str = os.getenv("MEMBER_MODEL_ID", "Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q6_K:latest")
    member_num_ctx: int = _int_env("MEMBER_NUM_CTX", 32768)

    model_temperature: float = float(os.getenv("MODEL_TEMPERATURE", "0.7"))

    # --- Agent behavior. Edit prompts/system.md to change the default brain; the
    # SYSTEM_PROMPT env var overrides the file for per-deploy customization. ---
    system_prompt: str = os.getenv("SYSTEM_PROMPT") or load_prompt("system.md")

    # --- Persistence (sessions + long-term user memories) ---
    db_file: str = os.getenv("DB_FILE", "data/chatbot.db")

    # --- Deliberate memory (see core/memory). Plain markdown files the model
    # reads/writes on purpose via tools — NOT framework auto-extraction. ---
    memory_dir: str = os.getenv("MEMORY_DIR", "data/memory")
    short_term_max: int = _int_env("SHORT_TERM_MAX", 20)  # turns kept per session
    # The base persona lives in prompts/team/lead.md (injected as the team's
    # instructions). This memory file holds only the adjustments the model makes
    # to itself over time via evolve_persona, so it starts empty. Set PERSONA_SEED
    # to pre-seed it with a base persona for the memory layer.
    persona_seed: str = os.getenv("PERSONA_SEED", "")

    # --- Team behavior / robustness ---
    # Hard cap on tool calls per run (incl. member delegations) so a lead can't
    # loop forever delegating. None/0 = no limit.
    tool_call_limit: int = _int_env("TOOL_CALL_LIMIT", 12)

    # --- Context-size monitoring. We can't perfectly count provider tokens, so
    # estimate (~4 chars/token) and warn when the assembled context crosses this
    # fraction of the lead's window. Purely a guardrail/log — never truncates. ---
    ctx_warn_ratio: float = float(os.getenv("CTX_WARN_RATIO", "0.75"))

    # --- Short-term session summarization. When turns roll out of the live
    # window, batch them and fold a rolling "session so far" summary that's kept
    # in context; on session close (!flush) that summary is also recorded as a
    # global episode. Off by default. ---
    session_summary: bool = _bool_env("SESSION_SUMMARY", False)
    summarize_every: int = _int_env("SUMMARIZE_EVERY", 10)  # evicted turns per summary

    # --- Long-term summarization. Once enough durable facts pile up, condense
    # long_term.md with an LLM into long_term_summary.md and inject the summary
    # plus the most recent raw facts (instead of the whole file). Off by default. ---
    long_term_summary: bool = _bool_env("LONG_TERM_SUMMARY", False)
    long_term_summarize_every: int = _int_env("LONG_TERM_SUMMARIZE_EVERY", 20)  # facts per re-summary
    long_term_recent_raw: int = _int_env("LONG_TERM_RECENT_RAW", 5)  # raw facts kept alongside summary

    # --- Semantic memory search (Qdrant + an embedding model via the proxy).
    # When off, build_context injects long-term/episodic whole (current behavior);
    # when on, it retrieves only the top-k most relevant entries for the query. ---
    semantic_memory: bool = _bool_env("SEMANTIC_MEMORY", False)
    embedding_model_id: str = os.getenv("EMBEDDING_MODEL_ID", "nomic-embed-text")
    qdrant_url: str = os.getenv("QDRANT_API_BASE", "http://localhost:6333")
    qdrant_api_key: str | None = os.getenv("QDRANT_API_KEY")
    semantic_top_k: int = _int_env("SEMANTIC_TOP_K", 5)

    # --- Discord bot ---
    DISCORD_BOT_TOKEN: str | None = os.getenv("DISCORD_BOT_TOKEN")

    def log_settings(self) -> None:
        """Dump the effective config to the console (secrets masked).

        Single startup banner so you can confirm *which* settings are live —
        proxy url, model ids, context windows, paths — without grepping env.
        """
        # Secrets that must never hit the log verbatim.
        masked = {"litellm_api_key", "DISCORD_BOT_TOKEN", "qdrant_api_key"}
        # Long prose: log the length, not the body.
        prose = {"system_prompt", "persona_seed"}

        log_info("=== effective config ===")
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name in masked:
                shown = _mask(value)
            elif f.name in prose:
                shown = f"<{len(value)} chars>"
            else:
                shown = value
            log_info(f"  {f.name} = {shown}")
        log_info("========================")


config = Config()
