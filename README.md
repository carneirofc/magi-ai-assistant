# chatbot

Personal multi-channel AI assistant on the [Agno](https://www.agno.com/) framework.
One shared agent brain, many channel adapters. Model-agnostic (Claude default, Ollama local).

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
POST /v1/sessions/{session_id}/messages   {"user_id": "...", "text": "..."}
POST /v1/sessions/{session_id}/flush      {"user_id": "..."}
GET  /v1/sessions/{session_id}/context    ?user_id=...
```

The client owns the ids: `user_id` scopes memory (durable per person),
`session_id` scopes one conversation. Configure with `API_HOST`, `API_PORT`;
set `API_AUTH_TOKEN` to require `Authorization: Bearer <token>` on `/v1`.
