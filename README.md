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

Media flows both ways. Replies carry the media the agent delivered (base64 or a
URL). Requests may carry inbound images for the agent to see — `images: [{...}]`
on the message body (`data_base64` or `url`; a `data:` URI works too). Whether
the model actually *sees* them depends on the backend having vision (e.g. a
llama-server with an mmproj loaded).

### OpenAI-compatible shim

The same brain also answers OpenAI's chat-completions format, so off-the-shelf
chat UIs (Open WebUI, LibreChat, …) work with no custom code:

```
GET  /v1/models                 advertises one model id, "chatbot"
POST /v1/chat/completions        OpenAI chat completions; set "stream": true for SSE
```

It bridges stateless wire → stateful brain: OpenAI clients resend the whole
transcript, but the agent keeps its own session memory, so only the last user
message is forwarded. OpenAI carries no session id, so one is derived from a
stable hash of the chat's first message (same chat → same server session); send
`X-Session-Id` (and `X-User-Id`) to be exact. Uploaded images (sent as
`image_url` `data:` URIs) are forwarded to the agent; reply media is folded back
into the message text as markdown.

## Chat UI (Open WebUI)

[Open WebUI](https://github.com/open-webui/open-webui) is a ready-made chat
front end. Point it at the shim and you get a full UI for free.

Open WebUI runs in Docker, so the app must be reachable from the container: bind
it to `0.0.0.0` (set `api_host="0.0.0.0"` in `main_api.py`) and set
`API_AUTH_TOKEN` in `.env`, since the port is now non-local.

PowerShell:

```powershell
# 1. Start the chatbot HTTP service (OpenAI-compatible shim on :8000).
python main_api.py

# 2. In another terminal, run Open WebUI pointed at the app. Open WebUI needs a
#    non-empty key; use your API_AUTH_TOKEN (any string works if auth is off).
$key = if ($env:API_AUTH_TOKEN) { $env:API_AUTH_TOKEN } else { "sk-noauth" }
docker run -d -p 3000:8080 `
  --add-host=host.docker.internal:host-gateway `
  -e OPENAI_API_BASE_URL=http://host.docker.internal:8000/v1 `
  -e OPENAI_API_KEY=$key `
  -v open-webui:/app/backend/data `
  --name open-webui ghcr.io/open-webui/open-webui:main
```

Bash:

```bash
# 1. Start the chatbot HTTP service (OpenAI-compatible shim on :8000).
python main_api.py

# 2. In another terminal, run Open WebUI pointed at the app. Open WebUI needs a
#    non-empty key; use your API_AUTH_TOKEN (any string works if auth is off).
docker run -d -p 3000:8080 \
  --add-host=host.docker.internal:host-gateway \
  -e OPENAI_API_BASE_URL=http://host.docker.internal:8000/v1 \
  -e OPENAI_API_KEY="${API_AUTH_TOKEN:-sk-noauth}" \
  -v open-webui:/app/backend/data \
  --name open-webui ghcr.io/open-webui/open-webui:main
```

Browse to <http://localhost:3000>, create the first account, and pick the
`chatbot` model — it's auto-discovered via `/v1/models`. Each Open WebUI
conversation maps to its own server-side session automatically.

## Configuration

Code-first: each entrypoint sets its deployment in `apply_deployment_config()`
(see `main.py` / `main_api.py`); defaults live in `core/config.py`. Only
secrets come from `.env` (`DISCORD_BOT_TOKEN`, `LITELLM_MASTER_KEY`,
`LLAMACPP_API_KEY`, `QDRANT_API_KEY`, `API_AUTH_TOKEN` — the last gates `/v1`
with `Authorization: Bearer <token>`). The effective values are printed at
startup by `config.log_settings()`.

## Object storage (durable file archive)

A durable, S3-compatible store the agent uses as **memory for bytes**: it can
decide to archive a file or image the user may want again and recall it later by
reference (`store_file` / `retrieve_file` / `list_files`). It's the byte-world
sibling of the text memory in `core/memory` — same idea, deliberate writes scoped
per user. Code lives in `core/storage` (the `S3Store`) and `agent/tools/storage.py`
(the model-facing tools). Recall delivers the actual bytes as an attachment; the
bucket is a private archive, not a public file host.

Off by default. Enabling it takes three things: a running S3 backend, the boto3
extra, and credentials.

```bash
uv sync --extra s3        # installs boto3 (lazy-imported; absent => tools off)
```

Set the credentials in `.env` (these are the only S3 *secrets*; bucket / region /
endpoint live in code via `configure(...)`):

```dotenv
S3_ACCESS_KEY_ID=rustfsadmin
S3_SECRET_ACCESS_KEY=rustfsadmin
```

Then turn it on in the entrypoint (`main.py` / `main_api.py`):

```python
configure(
    s3_enabled=True,
    s3_endpoint_url="http://localhost:9000",  # RustFS/MinIO; None => real AWS S3
    s3_bucket="chatbot-memory",
)
```

The store degrades gracefully: if `s3_enabled` is on but boto3 is missing or the
backend is unreachable at startup, the tools are simply not attached and the bot
boots normally.

### Launch RustFS for simple testing

[RustFS](https://github.com/rustfs/rustfs) is an S3-compatible object store. The
fastest path is the bundled compose, which runs RustFS on the canonical S3 ports
(API `9000`, console `9001`) and auto-creates the `chatbot-memory` bucket:

```bash
docker compose up -d rustfs rustfs-init
# S3 API → http://localhost:9000   console → http://localhost:9001
# default creds: rustfsadmin / rustfsadmin (override via S3_* in .env)
```

That matches the config defaults above, so `s3_endpoint_url="http://localhost:9000"`
works out of the box. (The compose also ships MinIO for LiteLLM's logs on host
ports `9100`/`9101`, so the two don't collide.)

Prefer a one-off container without compose:

```bash
docker run -d --name rustfs -p 9000:9000 -p 9001:9001 \
  -e RUSTFS_VOLUMES=/data \
  -e RUSTFS_ADDRESS=0.0.0.0:9000 \
  -e RUSTFS_CONSOLE_ADDRESS=0.0.0.0:9001 \
  -e RUSTFS_ACCESS_KEY=rustfsadmin \
  -e RUSTFS_SECRET_KEY=rustfsadmin \
  -v rustfs_data:/data \
  rustfs/rustfs:latest
```

Create the bucket once (browse to the console, or use the AWS CLI / `mc`):

```bash
aws --endpoint-url http://localhost:9000 s3 mb s3://chatbot-memory
```
