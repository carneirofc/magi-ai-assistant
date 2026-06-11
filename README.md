# chatbot

Personal multi-channel AI assistant on the [Agno](https://www.agno.com/) framework.
One shared agent brain, many channel adapters. Model-agnostic — local
llama.cpp `llama-server` by default (through the LiteLLM proxy, or direct with
`MODEL_PROVIDER=llamacpp`); Claude via the proxy; Ollama kept as dormant fallback.

## Run

```bash
python main.py          # Discord bot (needs DISCORD_BOT_TOKEN)
python main_api.py      # standalone HTTP service for external clients
```

Both serve the same brain (`channels/bootstrap.py`); only the transport differs.

## HTTP API

For a desktop app or any other client. JSON over HTTP, session-scoped
(see `channels/api.py` for the contract):

```
GET  /healthz
POST /v1/sessions/{session_id}/messages          {"user_id": "...", "text": "..."}
POST /v1/sessions/{session_id}/messages/stream   same body, reply streamed over SSE
POST /v1/sessions/{session_id}/flush             {"user_id": "..."}
GET  /v1/sessions/{session_id}/context           ?user_id=...
```

The two message endpoints are interchangeable per request: plain JSON gives the
whole reply at once; the SSE variant emits `delta` events (`{"text": chunk}`)
while the model writes, then one terminal `done` event with the full reply JSON
(authoritative — errors arrive as `done` with `is_error: true`).

The client owns the ids: `user_id` scopes memory (durable per person),
`session_id` scopes one conversation.

## Configuration

Code-first: each entrypoint sets its deployment in `apply_deployment_config()`
(see `main.py` / `main_api.py`); defaults live in `core/config.py`. Only
secrets come from `.env` (`DISCORD_BOT_TOKEN`, `LITELLM_MASTER_KEY`,
`LLAMACPP_API_KEY`, `QDRANT_API_KEY`, `API_AUTH_TOKEN` — the last gates `/v1`
with `Authorization: Bearer <token>`). The effective values are printed at
startup by `config.log_settings()`.
