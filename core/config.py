"""Application configuration — code-first, set at the entrypoint.

Settings are plain Python: the `Config` dataclass below holds the defaults, and
each entrypoint (main.py, main_discord.py, main_api.py) overrides what its
deployment needs via `configure(...)` before building anything. To find out
what a value is, read the entrypoint and this file — no env-var archaeology.
The startup banner (`config.log_settings()`, called from channels/bootstrap.py)
prints the effective values at runtime.

Only *secrets* come from the environment / `.env` (tokens and API keys never
belong in code): DISCORD_BOT_TOKEN, LITELLM_MASTER_KEY, LLAMACPP_API_KEY,
QDRANT_API_KEY, API_AUTH_TOKEN.
"""

import os
from dataclasses import dataclass, field, fields

from agno.utils.log import log_info
from dotenv import load_dotenv

from core.prompts import load_prompt

load_dotenv()  # secrets only — see module docstring


def _secret(name: str):
    """A dataclass default that reads a secret from the environment at startup."""
    return field(default_factory=lambda: os.getenv(name) or None)


def _mask(secret: str | None) -> str:
    """Render a secret for logs: presence + length, never the value."""
    if not secret:
        return "<unset>"
    return f"<set, {len(secret)} chars, ...{secret[-4:]}>"


@dataclass(frozen=True)
class Config:
    """All app settings. Override per deployment via `configure(...)` — never
    mutate directly (frozen catches accidental writes)."""

    # --- LiteLLM proxy: gateway to remote backends (Databricks Claude, …) ---
    litellm_base_url: str = "http://localhost:4000"
    litellm_api_key: str | None = _secret("LITELLM_MASTER_KEY")

    # --- llama.cpp llama-server (OpenAI-compatible /v1). The chat backend: one
    # model per instance, context size fixed at launch (--ctx-size). ---
    llamacpp_base_url: str = "http://localhost:8080/v1"
    # Only needed when llama-server runs with --api-key.
    llamacpp_api_key: str | None = _secret("LLAMACPP_API_KEY")

    # Only used by the direct-Ollama builder (dormant fallback).
    ollama_host: str = "http://localhost:11434"

    # --- Models. Which builder serves the roles: `litellm` (proxy gateway),
    # `llamacpp` (direct to llamacpp_base_url), or `ollama` (dormant). ---
    model_provider: str = "litellm"
    # Lead / router brain. For the proxy the id is a litellm model_name
    # (litellm.config.yaml); for direct llama-server it is cosmetic.
    lead_model_id: str = "qwen3.5-9b-llamacpp"
    # Context budget for assembly/guardrails (core/memory). llama-server fixes
    # the real window at launch — keep this equal to its --ctx-size; it is never
    # transmitted per-request (that was an Ollama runtime option).
    lead_num_ctx: int = 128_000

    # Specialist members. Same llama-server instance serves both roles.
    member_model_id: str = "qwen3.5-9b-llamacpp"
    member_num_ctx: int = 128_000

    # None means "send nothing" — the backend's defaults rule (llama-server
    # carries the model's recommended sampling via its launch flags).
    model_temperature: float | None = None
    # Extra request-body params for every chat call. llama-server accepts its
    # native sampling params (top_k, min_p, ...) and chat_template_kwargs on
    # /v1/chat/completions; rides extra_body on the litellm path too.
    model_extra_body: dict = field(default_factory=dict)

    # --- Agent behavior. Edit prompts/system.md to change the default brain;
    # override `system_prompt` in configure() for per-deploy customization. ---
    system_prompt: str = field(default_factory=lambda: load_prompt("system.md"))

    # --- Persistence (sessions + long-term user memories) ---
    db_file: str = "data/chatbot.db"

    # --- Local Danbooru data (agent/tools/danbooru). CSV dumps that answer tag
    # and wiki lookups offline before falling back to the rate-limited live
    # site. Missing files are fine — tools then go straight to the API. ---
    danbooru_tags_csv: str = "artifacts/danbooru_tags.csv"
    danbooru_wiki_csv: str = "artifacts/danbooru_wiki_pages.csv"

    # --- Deliberate memory (see core/memory). Plain markdown files the model
    # reads/writes on purpose via tools — NOT framework auto-extraction. ---
    memory_dir: str = "data/memory"
    short_term_max: int = 20  # turns kept per session
    # Per-turn size guard: the window caps how many turns are kept, this caps how
    # big each one may be (chars, ~4/token; <= 0 disables). Without it one pasted
    # blob is replayed into every later run's context.
    short_term_turn_max_chars: int = 4_000
    # The base persona lives in prompts/team/lead.md (injected as the team's
    # instructions). This memory file holds only the adjustments the model makes
    # to itself over time via evolve_persona, so it starts empty. Set a seed
    # here to pre-populate it for the memory layer.
    persona_seed: str = ""

    # --- Team behavior / robustness ---
    # Hard cap on tool calls per run (incl. member delegations) so a lead can't
    # loop forever delegating. None/0 = no limit.
    tool_call_limit: int = 12

    # --- HTTP request tool (agent/tools/http). SSRF guard: the model can be
    # steered by untrusted page content, so by default it may not call private /
    # loopback hosts. Flip to True only for a deployment that must reach a local
    # service on purpose (keep the bind trusted). ---
    http_allow_private_hosts: bool = False

    # --- Context-size monitoring. We can't perfectly count provider tokens, so
    # estimate (~4 chars/token) and warn when the assembled context crosses this
    # fraction of the lead's window. Purely a guardrail/log — never truncates. ---
    ctx_warn_ratio: float = 0.75

    # --- Short-term session summarization. When turns roll out of the live
    # window, batch them and fold a rolling "session so far" summary that's kept
    # in context; on session close (!flush) that summary is also recorded as a
    # global episode. ---
    session_summary: bool = False
    summarize_every: int = 10  # evicted turns per summary
    # Caps that keep summarization failures from compounding: if the summarizer is
    # down, the evicted-turn buffer would otherwise grow (and its fold payload
    # with it) on every turn — beyond this many buffered turns the oldest are
    # dropped with a warning. The summary blob itself is clamped so a runaway
    # summarizer output isn't replayed into every later run (<= 0 disables).
    session_pending_max: int = 30
    session_summary_max_chars: int = 4_000

    # --- Long-term summarization. Once enough durable facts pile up, condense
    # long_term.md with an LLM into long_term_summary.md and inject the summary
    # plus the most recent raw facts (instead of the whole file). ---
    long_term_summary: bool = False
    long_term_summarize_every: int = 20  # facts per re-summary
    long_term_recent_raw: int = 5  # raw facts kept alongside summary

    # --- Semantic memory search (Qdrant + an embedding model via the proxy).
    # When off, build_context injects long-term/episodic whole; when on, it
    # retrieves only the top-k most relevant entries for the query. Needs an
    # embedding backend (Ollama retired — second llama-server --embedding
    # instance) before turning on. ---
    semantic_memory: bool = False
    embedding_model_id: str = "nomic-embed-text"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = _secret("QDRANT_API_KEY")
    semantic_top_k: int = 5

    # --- Seanime media server (agent/tools/seanime). A dedicated client with a
    # fixed base URL — the model never chooses the host, so the http-tool SSRF
    # guard doesn't apply here. Token only needed when the server has a password
    # (sent as X-Seanime-Token). ---
    seanime_base_url: str = "http://127.0.0.1:43211"
    seanime_token: str | None = _secret("SEANIME_TOKEN")

    # --- Discord bot ---
    DISCORD_BOT_TOKEN: str | None = _secret("DISCORD_BOT_TOKEN")

    # --- HTTP API service (channels/api). The standalone integration point for
    # external clients (desktop app, web UI, ...). Bound to localhost by
    # default; api_auth_token (secret) gates /v1 with `Authorization: Bearer`. ---
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_auth_token: str | None = _secret("API_AUTH_TOKEN")

    def log_settings(self) -> None:
        """Dump the effective config to the console (secrets masked).

        Single startup banner so you can confirm *which* settings are live —
        backend urls, model ids, context windows, paths — in one place.
        """
        # Secrets that must never hit the log verbatim.
        masked = {"litellm_api_key", "llamacpp_api_key", "DISCORD_BOT_TOKEN", "qdrant_api_key", "api_auth_token", "seanime_token"}
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


def configure(**overrides) -> Config:
    """Set deployment configuration in code — call once, at the entrypoint,
    before building any channel/team (values are read at build/run time).

    Mutates the shared singleton in place so every `from core.config import
    config` already holding the object sees the new values. The dataclass
    stays frozen so only this deliberate path can write.
    """
    valid = {f.name for f in fields(config)}
    for name, value in overrides.items():
        if name not in valid:
            raise ValueError(f"unknown config field {name!r}; valid: {sorted(valid)}")
        object.__setattr__(config, name, value)
    return config
