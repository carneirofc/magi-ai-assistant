# chatbot

Personal multi-channel AI assistant on the [Agno](https://www.agno.com/) framework.
One shared agent brain, many channel adapters. Model-agnostic (Claude default, Ollama local).

## Architecture

```
core/agent_service.py   shared brain: messages -> agent.run() -> reply
agent/model.py          provider-agnostic model factory (provider from config)
agent/agents.py         single-agent builders (generic + channel presets)
agent/tools/            callable skills (one per file) + tool-gating helper
agent/members/          team specialists (one per file) + TEAM_MEMBERS registry
agent/team.py           assembles members into a routing Team
core/config.py          env-driven settings (single source of truth)
core/prompts.py         loads editable markdown prompts from prompts/
channels/openai_api.py  OpenAI-compatible HTTP shim (M1 client: OpenWebUI)
main.py                 entrypoint (serves the API)
```

## Edit prompts

Agent instructions live as markdown in [`prompts/`](prompts/README.md) — edit
`prompts/system.md` (single-agent brain) or `prompts/team/*.md` (team roles), no
code change. `SYSTEM_PROMPT` env var overrides the file when set.

Channels normalize their inbound payload into OpenAI-style messages and call
`AgentService`. Discord, Telegram, and email monitoring are later milestones.

## Why an OpenAI-compatible shim

Agno has no native `/v1/chat/completions` server. OpenWebUI connects to anything
speaking the OpenAI Chat Completions protocol, so we expose a thin FastAPI shim
that maps OpenAI requests to `agent.run()`.

## Setup

```sh
uv sync
cp .env.example .env   # then set ANTHROPIC_API_KEY and API_TOKEN
uv run python main.py
```

## Connect OpenWebUI

Admin Settings → Connections → OpenAI:
- URL: `http://<host>:8000/v1`
- API key: your `API_TOKEN`

Then pick the `anthropic:...` model and chat.

## Swap model

Edit `.env`: `MODEL_PROVIDER=ollama`, `MODEL_ID=llama3.1` (run Ollama locally). No code change.

## Endpoints

- `GET /v1/models` — lists the agent as a model
- `POST /v1/chat/completions` — streaming + non-streaming, Bearer auth

## Status

M1 complete: core agent + OpenWebUI endpoint. Next: tools + model swap (M2),
Discord (M3), Telegram (M4), email monitoring (M5).
