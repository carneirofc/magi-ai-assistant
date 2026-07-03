# Getting started

This walks from a clean checkout to a running assistant. magi is a Python ≥3.14
project managed with [uv](https://docs.astral.sh/uv/).

## 1. Install

```bash
uv sync                       # base install
# optional features (lazy-imported; skip what you don't need):
uv sync --extra semantic      # Qdrant-backed semantic memory
uv sync --extra s3            # S3-compatible byte archive
uv sync --extra mcp           # Seanime-over-MCP specialist
```

## 2. Secrets

Copy the template and fill in only what you use. **Configuration is code-first** —
`.env` holds *secrets only*; everything else is set in the entrypoint (see
[configuration.md](configuration.md)).

```bash
cp .env.example .env
```

At minimum: `DISCORD_BOT_TOKEN` for the Discord bot, or `API_AUTH_TOKEN` for a
network-exposed HTTP service. The rest depends on your backend (e.g.
`LITELLM_MASTER_KEY`, `S3_ACCESS_KEY_ID`).

## 3. A model backend

The bundled entrypoints expect a local `llama.cpp` `llama-server` on
`http://127.0.0.1:8888/v1` (`model_provider="llamacpp"`). Launch one with your
model and an `mmproj` for vision; set `--ctx-size` to match `lead_num_ctx` (128k by
default). To use Claude or another remote model instead, switch the entrypoint to
`model_provider="litellm"` and bring up the proxy
(`docker compose up -d litellm postgres`). See
[infrastructure.md](infrastructure.md).

## 4. Run

Both entrypoints serve the **same brain** (`magi/channels/bootstrap.py`); only the
transport differs.

```bash
python main.py          # Discord bot (needs DISCORD_BOT_TOKEN)
python main_api.py      # standalone HTTP service on 127.0.0.1:8000
```

The startup banner prints every effective setting (secrets masked) — confirm your
backend URL, model ids, and feature flags there.

## 5. Talk to it over HTTP

```bash
# health
curl http://localhost:8000/healthz

# one turn (whole reply)
curl -X POST http://localhost:8000/v1/sessions/demo/messages \
  -H 'content-type: application/json' \
  -d '{"user_id": "alice", "text": "hello"}'

# streamed (SSE: delta events, then a terminal done event)
curl -N -X POST http://localhost:8000/v1/sessions/demo/messages/stream \
  -H 'content-type: application/json' \
  -d '{"user_id": "alice", "text": "tell me a joke"}'

# close the session (fold summary → episode, wipe live turns)
curl -X POST http://localhost:8000/v1/sessions/demo/flush \
  -H 'content-type: application/json' \
  -d '{"user_id": "alice"}'
```

With `API_AUTH_TOKEN` set, add `-H "authorization: Bearer <token>"` to `/v1` calls.
The full contract is in [channels.md](channels.md).

## Chat UI (Open WebUI)

[Open WebUI](https://github.com/open-webui/open-webui) is a ready-made chat front
end. Point it at the OpenAI-compatible shim and you get a full UI for free.

Open WebUI runs in Docker, so the app must be reachable from the container: bind it
to `0.0.0.0` (`api_host="0.0.0.0"` in `main_api.py`) and set `API_AUTH_TOKEN`, since
the port is now non-local.

```bash
# 1. Start the chatbot HTTP service (shim on :8000).
python main_api.py

# 2. Run Open WebUI pointed at the app (it needs a non-empty key; reuse the token).
docker run -d -p 3000:8080 \
  --add-host=host.docker.internal:host-gateway \
  -e OPENAI_API_BASE_URL=http://host.docker.internal:8000/v1 \
  -e OPENAI_API_KEY="${API_AUTH_TOKEN:-sk-noauth}" \
  -v open-webui:/app/backend/data \
  --name open-webui ghcr.io/open-webui/open-webui:main
```

A PowerShell version is in [`scripts/run-openwebui.ps1`](../scripts/run-openwebui.ps1).
Browse to <http://localhost:3000>, create the first account, and pick the `chatbot`
model (auto-discovered via `/v1/models`). Each conversation maps to its own
server-side session.

## Object storage

Turn on the model's durable file/image archive in the entrypoint's `Config`:

```python
Config(
    storage_enabled=True,
    storage_backend="local",          # bytes under data/artifacts — zero setup
    storage_local_dir="data/artifacts",
    # … the rest of the deployment's settings
)
```

For the S3 backend, run a bucket (`docker compose up -d rustfs rustfs-init`),
`uv sync --extra s3`, put `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` in `.env`, and
set `storage_backend="s3"`. See [infrastructure.md](infrastructure.md#object-storage-byte-archive).

## 6. Tests

```bash
uv run pytest
```

The suite covers memory, channels, tools, and contracts; `core` is model-free so
most of it runs with no backend.

## Where next

- [architecture.md](architecture.md) — the design and request lifecycle.
- [memory.md](memory.md) — how the assistant remembers.
- [configuration.md](configuration.md) — every knob.
- [agent-and-tools.md](agent-and-tools.md) — add a member or a tool.
