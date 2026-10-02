# Getting started

This goes from nothing to a running assistant. magi is a Python ≥3.14 tool
managed with [uv](https://docs.astral.sh/uv/); the default deployment is
**bare-metal as your own user** — no Docker required.

## 1. Install

```bash
curl -LsSf https://raw.githubusercontent.com/carneirofc/magi-ai-assistant/master/scripts/install.sh | bash
```

The script installs `uv` if needed, then `uv tool install "magi-ai-assistant[telegram]"`
(the `magi` command lands in `~/.local/bin`), then runs `magi setup`. Pick
extras with `MAGI_EXTRAS` — optional features are lazy-imported, so skip what
you don't need:

```bash
MAGI_EXTRAS=telegram,semantic,mcp bash scripts/install.sh   # from a checkout
# telegram · semantic (Qdrant) · s3 · mcp · git · websearch · docs · desktop
```

Working on the engine itself? `uv sync --dev --all-extras` in a checkout and
prefix commands with `uv run`.

## 2. Configure

The wizard asks for the model backend and channels, writes
`~/.magi/config.yaml`, and puts secrets in `~/.magi/.env` (mode 0600):

```bash
magi setup
magi doctor      # config valid? backend reachable? extras installed?
```

From a checkout the repo's own [`magi.yaml`](../magi.yaml) is used instead
(`./magi.yaml` wins). `.env` holds *secrets only* — see
[configuration.md](configuration.md). To start from the template:
`cp .env.example ~/.magi/.env`.

At minimum: `DISCORD_BOT_TOKEN` for the Discord bot, or `API_AUTH_TOKEN` for a
network-exposed HTTP service. The rest depends on your backend (e.g.
`LITELLM_MASTER_KEY`, `S3_ACCESS_KEY_ID`).

## 3. A model backend

The bundled entrypoints expect a local `llama.cpp` `llama-server` on
`http://127.0.0.1:8888/v1` (`model_provider="llamacpp"`). Launch one with your
model and an `mmproj` for vision; set `--ctx-size` to match `lead_num_ctx` (128k by
default). To use Claude or another remote model instead, set
`model_provider: litellm` (`magi config set model_provider litellm`) and bring up the proxy
(`docker compose --profile litellm up -d`). See
[infrastructure.md](infrastructure.md).

## 4. Run

Every channel serves the **same brain**, and any mix runs in one process:

```bash
magi run                      # channels.enabled from the config
magi run api discord telegram # or name them
```

The startup banner prints every effective setting (secrets masked) — confirm your
backend URL, model ids, and feature flags there.

### As a service

```bash
magi gateway install          # ~/.config/systemd/user/magi.service, enabled + started
magi gateway status
magi gateway logs -f
loginctl enable-linger $USER  # keep it running while you're logged out
```

The unit runs `magi run` with the same config, restarts on failure, and starts at
login. `magi gateway uninstall` removes it.

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
to `0.0.0.0` (`magi config set api_host 0.0.0.0`) and set `API_AUTH_TOKEN`, since
the port is now non-local.

```bash
# 1. Start the chatbot HTTP service (shim on :8000).
python main.py api

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

Turn on the model's durable file/image archive in the config file:

```yaml
storage_enabled: true
storage_backend: local            # bytes under data/artifacts — zero setup
storage_local_dir: data/artifacts
```

For the S3 backend, run a bucket (`docker compose --profile s3 up -d`),
`uv sync --extra s3`, put `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` in `.env`, and
set `storage_backend: s3`. See [infrastructure.md](infrastructure.md#object-storage-byte-archive).

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
