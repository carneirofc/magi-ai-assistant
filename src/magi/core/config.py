"""Application configuration — code-first, set at the entrypoint.

Settings are plain Python: the `Config` dataclass below holds the defaults, and
each entrypoint (main.py, main_discord.py, main_api.py) overrides what its
deployment needs via `configure(...)` before building anything. To find out
what a value is, read the entrypoint and this file — no env-var archaeology.
The startup banner (`config.log_settings()`, called from magi/channels/bootstrap.py)
prints the effective values at runtime.

Only *secrets* come from the environment / `.env` (tokens and API keys never
belong in code): DISCORD_BOT_TOKEN, LITELLM_MASTER_KEY, LLAMACPP_API_KEY,
QDRANT_API_KEY, API_AUTH_TOKEN.
"""

import os
from dataclasses import dataclass, field, fields

from agno.utils.log import log_info
from dotenv import load_dotenv

from magi.core.prompts import load_prompt

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
    # Context budget for assembly/guardrails (magi/core/memory). llama-server fixes
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

    # --- Local Danbooru data (magi/agent/tools/danbooru). CSV dumps that answer tag
    # and wiki lookups offline before falling back to the rate-limited live
    # site. Missing files are fine — tools then go straight to the API. ---
    danbooru_tags_csv: str = "artifacts/danbooru_tags.csv"
    danbooru_wiki_csv: str = "artifacts/danbooru_wiki_pages.csv"

    # --- Deliberate memory (see magi/core/memory). Plain markdown files the model
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

    # --- HTTP request tool (magi/agent/tools/http). SSRF guard: the model can be
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

    # --- Long-term rendering. The durable fact sheet (long_term_facts.json) is owned
    # by the curator below; alongside it, build_context injects the most recent raw
    # facts written via `remember` so freshly-learned facts surface before the next
    # curation pass folds them in. ---
    long_term_recent_raw: int = 5  # raw facts kept alongside the curated profile

    # --- Memory curator (see magi/core/memory/curation + magi/agent/curator). A cheap
    # post-turn pass that owns durable memory: instead of the lead appending
    # facts inline (append-only, on the reply path), the curator reads each
    # finished turn and revises the long-term fact sheet PER FACT — ADD a new fact,
    # UPDATE one that changed, DELETE one now wrong, or NOOP — optionally logging an
    # episode or evolving the persona. Runs off the reply path; one member-model
    # call per turn. The lead keeps only read tools. ---
    memory_curation: bool = False
    # Per-fact size cap so a runaway curator can't park a huge fact that's replayed
    # into every later run (<= 0 disables the clamp).
    long_term_fact_max_chars: int = 1_000
    # Soft cap on the number of durable facts; beyond it the oldest are dropped with
    # a warning so the sheet can't grow unbounded if the curator stops pruning.
    long_term_facts_max: int = 200

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

    # --- Knowledge layer (RAG; see magi/core/knowledge + magi/agent/tools/knowledge). A
    # global, read-only reference corpus the agent retrieves from via the
    # search_knowledge tool — distinct from memory (per-user, conversation-derived).
    # Documents are chunked + embedded faithfully (NO LLM extraction) into a Qdrant
    # collection separate from semantic memory, reusing the same embedding model +
    # Qdrant endpoint above. Populated out-of-band (scripts/ingest_knowledge.py).
    # Off by default; degrades to no tool when Qdrant/embeddings are unreachable.
    # Documents are stored under scope "global"; the store's search(scopes=...) is
    # the hook for narrowing to per-user/session origin later. ---
    knowledge_enabled: bool = False
    knowledge_collection: str = "chatbot_knowledge"
    knowledge_top_k: int = 5
    knowledge_chunk_chars: int = 1_200  # target chunk size before overlap
    knowledge_chunk_overlap: int = 150  # chars repeated between adjacent chunks
    # Subject is a hard filter at query time; tags are a soft boost (re-rank, never
    # exclude). The model passes them via search_knowledge. tag_weight scales the
    # tag-overlap bonus added to the (0..1 cosine) vector score; overfetch widens
    # the candidate pool pulled before the Python re-rank. See ADR 0002.
    knowledge_tag_weight: float = 0.15
    knowledge_overfetch: int = 4

    # --- Object storage (see magi/core/storage + magi/agent/tools/storage). A
    # durable file/image archive the model uses as memory: it can stash a file
    # under the current user's scope and recall it later by reference. Off by
    # default; turn on per deployment via configure(). Two interchangeable
    # backends, picked by `storage_backend`:
    #   "local" — bytes on the filesystem under `storage_local_dir`. No server, no
    #             boto3, no credentials; the zero-setup default once enabled.
    #   "s3"    — any S3 API: real AWS S3 (endpoint None, region used) or a local
    #             S3-compatible server like RustFS / MinIO (set the endpoint). The
    #             default endpoint points at a local RustFS for testing — see
    #             docker-compose.yaml / README. Needs the optional `s3` extra
    #             (`uv sync --extra s3`; boto3 lazy-imported, absent => tools off).
    # Credentials are secrets (.env); the rest lives here in code. The legacy
    # `s3_enabled=True` still works (configure() maps it to storage_enabled). ---
    storage_enabled: bool = False
    storage_backend: str = "local"  # "local" | "s3"
    storage_local_dir: str = "data/artifacts"
    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_bucket: str = "chatbot-memory"
    s3_access_key_id: str | None = _secret("S3_ACCESS_KEY_ID")
    s3_secret_access_key: str | None = _secret("S3_SECRET_ACCESS_KEY")
    # Lifetime (seconds) of presigned recall URLs handed back when a stored file is
    # too large to attach inline.
    s3_presign_expiry: int = 3600

    # --- Seanime media server (magi/agent/tools/seanime). A dedicated client with a
    # fixed base URL — the model never chooses the host, so the http-tool SSRF
    # guard doesn't apply here. Token only needed when the server has a password
    # (sent as X-Seanime-Token). ---
    seanime_base_url: str = "http://127.0.0.1:43211"
    seanime_token: str | None = _secret("SEANIME_TOKEN")

    # --- Seanime via MCP (magi/agent/tools/seanime_mcp). An alternative anime
    # specialist that talks to Seanime's built-in read-only Model Context
    # Protocol server (Streamable HTTP at <base>/api/v1/mcp; opt-in there via
    # experimental.mcp) instead of the hand-rolled HTTP tools above. When
    # `seanime_use_mcp` is True the anime specialist is built from the MCP tools
    # (search/collection/details/viewer-stats, read-only) — one anime member
    # either way, selected at build time. Needs the optional `mcp` extra
    # (uv sync --extra mcp); the same SEANIME_TOKEN rides as X-Seanime-Token. ---
    seanime_use_mcp: bool = False
    seanime_mcp_url: str = "http://127.0.0.1:43211/api/v1/mcp"

    # --- Discord bot ---
    DISCORD_BOT_TOKEN: str | None = _secret("DISCORD_BOT_TOKEN")

    # --- HTTP API service (magi/channels/api). The standalone integration point for
    # external clients (desktop app, web UI, ...). Bound to localhost by
    # default; api_auth_token (secret) gates /v1 with `Authorization: Bearer`. ---
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_auth_token: str | None = _secret("API_AUTH_TOKEN")
    # Browser clients are blocked by the same-origin policy unless the service
    # returns CORS headers. List the web origins allowed to call /v1 (e.g.
    # ["https://app.example.com"], or ["*"] to allow any). Empty = no CORS
    # headers (same-origin / non-browser clients only). Auth is a Bearer token,
    # not a cookie, so credentials are not allowed and "*" is safe.
    api_cors_origins: list[str] = field(default_factory=list)

    # --- Admin service (magi/channels/admin). An operator-only tool to view and
    # manage memory + organize the knowledge corpus — a SEPARATE deployable from
    # the chat API, so its write-capable surface never rides the public brain. See
    # ADR 0002. Reached only through the Next.js BFF (web/), which holds the token
    # server-side; bind localhost / keep the port unpublished. admin_auth_token
    # (secret) gates every /admin route with `Authorization: Bearer`. ---
    admin_host: str = "127.0.0.1"
    admin_port: int = 8100
    admin_auth_token: str | None = _secret("ADMIN_AUTH_TOKEN")

    def log_settings(self) -> None:
        """Dump the effective config to the console (secrets masked).

        Single startup banner so you can confirm *which* settings are live —
        backend urls, model ids, context windows, paths — in one place.
        """
        # Secrets that must never hit the log verbatim.
        masked = {"litellm_api_key", "llamacpp_api_key", "DISCORD_BOT_TOKEN", "qdrant_api_key", "api_auth_token", "admin_auth_token", "seanime_token", "s3_access_key_id", "s3_secret_access_key"}
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

    Mutates the shared singleton in place so every `from magi.core.config import
    config` already holding the object sees the new values. The dataclass
    stays frozen so only this deliberate path can write.
    """
    # Back-compat: object storage used to be S3-only and gated by `s3_enabled`.
    # It's now backend-agnostic (`storage_enabled` + `storage_backend`); honor the
    # old key so existing entrypoints keep working, and default to the S3 backend
    # since that's what `s3_enabled` implied.
    if "s3_enabled" in overrides:
        legacy = overrides.pop("s3_enabled")
        log_info("config: 's3_enabled' is deprecated — use storage_enabled + storage_backend")
        overrides.setdefault("storage_enabled", legacy)
        if legacy:
            overrides.setdefault("storage_backend", "s3")

    valid = {f.name for f in fields(config)}
    for name, value in overrides.items():
        if name not in valid:
            raise ValueError(f"unknown config field {name!r}; valid: {sorted(valid)}")
        object.__setattr__(config, name, value)
    return config
