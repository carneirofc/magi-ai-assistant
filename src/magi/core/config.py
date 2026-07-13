"""Application configuration — code-first, set at the entrypoint.

Settings are plain Python: the `Config` dataclass below holds the defaults, and
the channel configs in `main.py` override what each
deployment needs via `configure(...)` before building anything. To find out
what a value is, read the channel config and this file — no env-var archaeology.
The startup banner (`config.log_settings()`, called from magi/channels/bootstrap.py)
prints the effective values at runtime.

Only *secrets* come from the environment / `.env` (tokens and API keys never
belong in code): DISCORD_BOT_TOKEN, LITELLM_MASTER_KEY, LLAMACPP_API_KEY,
OPENAI_API_KEY, QDRANT_API_KEY, API_AUTH_TOKEN.
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

    # --- Generic OpenAI-compatible remote serving endpoint. Point this at any
    # hosted model server that speaks the OpenAI /v1 API — real OpenAI, OpenRouter,
    # Together, Fireworks, a remote vLLM / llama-server, … — and select it with
    # `model_provider="openai"`. Differs from `litellm` (which routes through our
    # own proxy) and `llamacpp` (a local server) only in where it points; the same
    # endpoint can also serve embeddings (see `embeddings_provider`). The key is a
    # secret; the base URL lives here in code. ---
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str | None = _secret("OPENAI_API_KEY")

    # --- Models. Which builder serves the roles: `litellm` (proxy gateway),
    # `llamacpp` (direct to llamacpp_base_url), `openai` (a remote OpenAI-compatible
    # server at openai_base_url), or `ollama` (dormant). ---
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
    # The curator appends a behavior rule to the persona's "Adjustments" section per
    # turn; near-identical rules pile up and bloat every run's context. After each
    # persona write the section is deduped and capped to the newest this many bullets
    # (<= 0 caps nothing but still dedupes). The prose base is never touched.
    persona_adjustments_max: int = 40

    # --- Bot identity (see magi/core/identity). A global, operator-set profile —
    # display name, description, and profile picture — the bot presents as itself.
    # Managed from the admin frontend; the name/description are injected into every
    # run as text, and the run context tells the model it HAS a profile picture. The
    # picture is never force-fed each turn (that reads as user content and derails
    # the model); the model pulls it in on demand via its profile-picture tools
    # (view_profile_picture / send_profile_picture, magi/agent/tools/identity). ---

    # --- Mood signal (see magi/agent/mood + docs; issue #25). A per-turn delivery
    # mood for the reply, produced BEFORE the reply by a tiny extra model call on
    # the same context, constrained (response_format json_schema enum — llama-server
    # enforces it via grammar) to the vocabulary below, so the value is always a
    # valid name — reliable enough to drive an avatar now and a TTS style later.
    # Streams as an early `meta` SSE frame and rides `MessageReply.mood`. The
    # vocabulary is name -> short description (the descriptions steer the pass);
    # grow it by adding entries — the wire carries plain strings, so clients that
    # don't know a mood fall back to their neutral art. The FIRST entry is the
    # fallback when the pass fails or returns something unusable. Bump
    # mood_vocab_version when the vocabulary changes so clients can re-sync. ---
    mood_enabled: bool = False
    mood_vocabulary: dict[str, str] = field(
        default_factory=lambda: {
            "neutral": "composed, matter-of-fact delivery — the resting default",
            "warm": "friendly, encouraging, genuinely pleased to help",
            "wry": "dry humor, amused skepticism, a raised eyebrow",
            "focused": "serious, precise, heads-down on a hard problem",
        }
    )
    mood_vocab_version: int = 1

    # --- Voice sidecars (see magi/core/voice). Two OpenAI-compatible LOCAL
    # services, host-run like the chat backend: TTS speaks replies (POST
    # {tts_base_url}/audio/speech — Kokoro-FastAPI-class) and STT hears the
    # user (POST {stt_base_url}/audio/transcriptions — whisper-class). The chat
    # API fronts them at /v1/tts and /v1/stt; browser clients never reach the
    # sidecars directly. The reply's mood (mood_enabled above) picks a style
    # override from tts_mood_styles — keys merge onto the speech request
    # (voice, speed, response_format, …) so her voice tracks her face. Keys are
    # secrets; URLs live here in code. ---
    tts_enabled: bool = False
    tts_base_url: str = "http://127.0.0.1:8880/v1"
    tts_api_key: str | None = _secret("TTS_API_KEY")
    tts_model: str = "tts-1"
    tts_voice: str = "af_heart"
    tts_format: str = "mp3"
    tts_mood_styles: dict[str, dict] = field(default_factory=dict)
    stt_enabled: bool = False
    stt_base_url: str = "http://127.0.0.1:8890/v1"
    stt_api_key: str | None = _secret("STT_API_KEY")
    stt_model: str = "whisper-1"
    stt_language: str | None = None
    # One generous cap for both sidecars — local services, whole-clip calls.
    voice_timeout_seconds: float = 60.0

    # --- Generic MCP registry (see magi/agent/tools/mcp.py). Each entry wires
    # one Model Context Protocol server without hand-writing a member module:
    #   {"name": "comfyui",                       # required, unique
    #    "url": "http://localhost:39100/mcp",     # streamable-http/sse; stdio uses "command"
    #    "transport": "streamable-http",          # default
    #    "headers": {...}, "env": {...},          # auth / stdio environment
    #    "timeout_seconds": 30,
    #    "enabled": True,
    #    "attach": "member",                      # "member" (own specialist) | "lead" (tools on the team)
    #    "role": "...",                           # member role prompt (attach=member)
    #    "tool_allowlist": [...],                 # only these tools are registered
    #    "show_result_tools": [...]}              # results surfaced to the client
    # Tools are discovered at connect time; a down/misconfigured server is
    # skipped with a warning, never a boot failure. Operator-added servers from
    # the admin settings file merge over this list by name (restart to apply).
    # Needs the optional `mcp` extra. ---
    mcp_servers: list[dict] = field(default_factory=list)

    # --- Web search tool (ddgs-backed; optional `websearch` extra). Gives the
    # lead `web_search` — result titles/urls/snippets it then reads via the
    # HTTP tools. Off by default: a deployment opts into outbound search. ---
    websearch_enabled: bool = False

    # --- Reminders (see magi/agent/tools/reminders.py). Deliberate per-user
    # reminder files under the memory tree; due ones surface in the greeting
    # turn and at GET /v1/reminders. No push infra — surfacing is on-open. ---
    reminders_enabled: bool = False

    # --- Self-evolution with a human in the loop (see magi/core/evolution).
    # The assistant may PROPOSE changes to allowlisted prompts and new
    # declarative HTTP tools; nothing applies until the operator approves it in
    # the admin queue. Approved changes land in <memory_dir>/prompts-runtime
    # (register it FIRST in set_prompt_overlay at the entrypoint) and
    # <memory_dir>/tools-runtime, and take effect on restart. SOUL/lead-class
    # identity prompts do not belong in the allowlist — tone bends, identity
    # doesn't. ---
    evolution_enabled: bool = False
    evolution_proposable: list[str] = field(
        default_factory=lambda: ["curation.md", "greet.md"]
    )
    evolution_queue_max: int = 20

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
    # fraction of the lead's window. Purely a guardrail/log — never truncates.
    # (The stats endpoint upgrades to REAL counts via llama-server /tokenize
    # when the provider is llamacpp — see magi/core/tokens.) ---
    ctx_warn_ratio: float = 0.75

    # --- Context section budgets (chars). When a section is listed here, its
    # rendered body is hard-clamped to that many characters at assembly time
    # (truncation marked, so the model can see content was dropped). Keys:
    # "long_term", "episodes", "short_term", "knowledge". Empty = the historic
    # warn-only behavior (nothing truncates). Persona is never clamped — it IS
    # the assistant. Trim generously: these are a seatbelt against one runaway
    # section eating the window, not a tuning knob. ---
    context_section_budgets: dict[str, int] = field(default_factory=dict)

    # --- Pressure-triggered session fold. The turn-count fold (summarize_every)
    # can lag a session full of LONG turns; with this > 0 the per-turn fold also
    # fires whenever the rendered short-term section alone exceeds this fraction
    # of the lead's window (est. tokens) and turns are pending. 0 = off. ---
    session_fold_pressure_ratio: float = 0.0

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

    # --- Git-backed memory (see magi/core/memory/git_backend). When on, the memory
    # root (memory_dir) is a git repository this backend initializes and manages:
    # every deliberate memory write — a fact, an episode, a session summary, a
    # persona adjustment, an identity edit, a short-term turn — is staged and
    # committed, so the whole memory tree carries a full, inspectable, revertible
    # history any git tool can read. Off by default; needs the optional `git` extra
    # (uv sync --extra git; GitPython is lazy-imported, and the factory no-ops when
    # it's absent so the memory files stay plain and the app still boots). The commit
    # identity below is written into the repo so commits never depend on the host's
    # global git config. NOTE: the memory root must be its OWN top-level repo — the
    # backend refuses to initialize a nested repo inside another one, so point
    # memory_dir at a directory OUTSIDE the source tree before enabling this (the
    # default "data/memory" sits inside this checkout and would be rejected). ---
    memory_git_enabled: bool = False
    memory_git_author_name: str = "magi-memory"
    memory_git_author_email: str = "magi-memory@localhost"

    # --- Operator settings (see magi/core/settings). A small JSON file of runtime
    # overrides an operator edits from the admin UI, layered over the code defaults
    # above at startup: where memory lives (memory_dir) and its git-versioning. It
    # lives OUTSIDE the memory tree (it points *at* that tree) and defaults next to
    # the local data. Empty/absent = pure code defaults; changes apply on restart. ---
    operator_settings_path: str = "data/operator-settings.json"

    # --- Semantic memory search (Qdrant + an embedding model via the proxy).
    # When off, build_context injects long-term/episodic whole; when on, it
    # retrieves only the top-k most relevant entries for the query. Needs an
    # embedding backend (Ollama retired — second llama-server --embedding
    # instance) before turning on. ---
    semantic_memory: bool = False
    embedding_model_id: str = "nomic-embed-text"
    # Where embeddings are served, independent of the chat backend: `litellm`
    # (default — through our proxy, the historical path) or `openai` (a remote
    # OpenAI-compatible endpoint at openai_base_url / openai_api_key). Lets a
    # deployment run chat on a local llama-server while sourcing embeddings from a
    # remote model — llama-server serves only one model per instance, so semantic
    # memory + knowledge otherwise need a second local instance.
    embeddings_provider: str = "litellm"  # "litellm" | "openai"
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
    # Auto-injection into the run context: when > 0 (and knowledge is enabled),
    # ConversationService folds the top-k corpus chunks most relevant to the user's
    # message straight into context each turn — in addition to the on-demand
    # search_knowledge tool — so the model has reference material up front without
    # having to ask. 0 = off (tool-only retrieval), the default.
    knowledge_context_top_k: int = 0
    knowledge_chunk_chars: int = 1_200  # target chunk size before overlap
    knowledge_chunk_overlap: int = 150  # chars repeated between adjacent chunks
    # Subject is a hard filter at query time; tags are a soft boost (re-rank, never
    # exclude). The model passes them via search_knowledge. tag_weight scales the
    # tag-overlap bonus added to the (0..1 cosine) vector score; overfetch widens
    # the candidate pool pulled before the Python re-rank. See ADR 0002.
    knowledge_tag_weight: float = 0.15
    knowledge_overfetch: int = 4
    # The controlled-subject registry (admin-curated vocabulary); a small JSON file
    # the admin service reads/writes. See magi/core/knowledge/subjects.py + ADR 0002.
    knowledge_subjects_path: str = "data/knowledge/subjects.json"

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

    # --- Item archive (see magi/core/items). The "persist original + index" hook
    # shared by the admin-managed items — knowledge documents, durable memory facts,
    # and stored files. On a write it keeps the item's *canonical bytes* in the
    # object store (the source of truth, re-indexable) AND a *searchable vector* in a
    # Qdrant collection; on delete it drops both, so the byte original and the search
    # index never drift. It pairs the object-store backend (config.storage_backend +
    # s3_*/storage_local_dir above) with Qdrant (config.qdrant_url) — but is gated by
    # its OWN flag, independent of storage_enabled / semantic_memory, so an operator
    # can turn durable item archival on without enabling the model's file tools or
    # semantic recall. Off by default; degrades to a no-op when the object store or
    # Qdrant is unreachable (item writes must never break a chat or an ingest). ---
    items_archive_enabled: bool = False
    items_collection: str = "chatbot_items"

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
    # manage memory + organize the knowledge corpus. ADR 0002's default is a
    # SEPARATE deployable (`python main.py admin`) fronted by the Next.js BFF (web/), which
    # holds the token server-side — keeps the write-capable surface off the public
    # brain entirely. Reached only through the BFF; bind localhost / keep the port
    # unpublished. admin_auth_token (secret) gates every /admin route with
    # `Authorization: Bearer` regardless of how it's served. ---
    admin_host: str = "127.0.0.1"
    admin_port: int = 8100
    admin_auth_token: str | None = _secret("ADMIN_AUTH_TOKEN")
    # Opt-in convenience for a single-operator/dev deployment that doesn't want a
    # second process: serve the admin surface ALONGSIDE this channel's own
    # transport instead of running `python main.py admin` separately.
    #   - HTTP API channel (channels/api.py) — mounted onto the SAME FastAPI app,
    #     so it rides api_host:api_port; admin_host/admin_port are unused here.
    #   - Discord channel (channels/discord.py) — there's no ASGI app to mount
    #     onto, so a second uvicorn server is started on admin_host:admin_port,
    #     running concurrently with the gateway connection in one process (see
    #     `channels.discord.serve_with_admin`).
    # Off by default — ADR 0002's separate-process posture stays the recommended
    # production setup either way; this doesn't change auth or the bind defaults.
    admin_enabled: bool = False

    # --- Desktop shell (magi/desktop). A frameless, widget-style native window
    # (PySide6 + QtWebEngine) that renders the SAME Next.js frontend as the browser
    # (web/), served by a Node child process THIS process owns and tears down — one
    # executable, no separately-run web server. The frontend is unchanged; a
    # JS<->Python bridge (QWebChannel) is injected for native actions. Needs the
    # optional `desktop` extra (uv sync --extra desktop). See docs/desktop.md.
    # Loopback-only by construction: the child binds 127.0.0.1 on an ephemeral port.
    #
    # Serve the chat+admin API in-process (loopback) so every frontend page —
    # dashboard, memory, knowledge, subjects, team, chat — is live from this one
    # command. It's the `admin_enabled` single-app shape (one FastAPI app serving
    # /v1/* AND /admin/v1/*); the shell points the BFF at it. Turn OFF to reuse a
    # backend already running elsewhere (set CHAT_API_URL / ADMIN_API_URL for the
    # frontend instead). See magi/desktop/backend.py.
    desktop_serve_backend: bool = True
    # Secrets the web frontend's BFF needs, forwarded by the shell to the Node child
    # so it never depends on a bundled `web/.env` (absent in a frozen build):
    #   ADMIN_PASSWORD  — the single operator password its login checks.
    #   SESSION_SECRET  — HMAC key signing the session cookie; REQUIRED by the
    #                     frontend (it throws without it). The shell auto-generates
    #                     a stable one (persisted in QSettings) when this is unset,
    #                     so the desktop app works out of the box.
    # (The API_AUTH_TOKEN / ADMIN_AUTH_TOKEN the BFF presents upstream are the same
    #  api_auth_token / admin_auth_token above.)
    admin_password: str | None = _secret("ADMIN_PASSWORD")
    session_secret: str | None = _secret("SESSION_SECRET")
    # `desktop_web_dir` is the built web/ project (contains .next/); None =
    # auto-resolve (repo web/ from source, or <_MEIPASS>/web when frozen).
    desktop_web_dir: str | None = None
    # The Node executable used to run the frontend server. "node" (on PATH) by
    # default; set an absolute path for a frozen build shipping its own runtime.
    desktop_node_command: str = "node"
    # Route the window opens on. The frontend's auth gate (web/ middleware) bounces
    # an unauthenticated launch to /login first; the signed session cookie then
    # persists in the window's own profile so later launches land here directly.
    desktop_start_path: str = "/chat"
    # Initial window size (px); restored per-launch from QSettings after the first run.
    desktop_window_width: int = 420
    desktop_window_height: int = 680
    # Minimum window size (px). Floors how small the shell can be dragged. An app with
    # a fixed-frame, desktop-only web layout raises this to its supported minimum so the
    # shell never shrinks into a "window too small" state (see Alyssa's console).
    desktop_window_min_width: int = 320
    desktop_window_min_height: int = 380
    # Translucent frame around the web content: a rounded, semi-transparent panel
    # with this inset (px) and corner radius, doubling as the window's drag border.
    desktop_window_margin: int = 12
    desktop_window_radius: int = 16
    # Frameless + translucent chrome. Off (a normal titled window) is handy for
    # debugging the web content; the bridge and server behave the same either way.
    desktop_frameless: bool = True
    # How long to wait (seconds) for the Node child to start serving before giving up.
    desktop_server_ready_timeout: float = 30.0

    def log_settings(self) -> None:
        """Dump the effective config to the console (secrets masked).

        Single startup banner so you can confirm *which* settings are live —
        backend urls, model ids, context windows, paths — in one place.
        """
        # Secrets that must never hit the log verbatim.
        masked = {"litellm_api_key", "llamacpp_api_key", "openai_api_key", "DISCORD_BOT_TOKEN", "qdrant_api_key", "api_auth_token", "admin_auth_token", "seanime_token", "s3_access_key_id", "s3_secret_access_key", "admin_password", "session_secret", "tts_api_key", "stt_api_key"}
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
