# 0005 — One process, many channels, one brain

- Status: Accepted
- Date: 2026-09-27
- Builds on: ADR 0003 (`PlatformAdapter`, `scoped_user_id`, `run_gateway`),
  ADR 0004 (`channels.enabled`, the `magi` CLI)

## Context

ADR 0003 gave every channel a shared contract, but each entry point still
built its own conversation stack and ran alone — except Discord + admin via
`serve_with_admin`. Running Discord and the HTTP API together meant two
processes, two teams, and two memory managers writing the same tree.

## Decision

1. `channels/registry.py` maps channel names to adapter builders that take an
   **injected** `ConversationService`. `serve(names)` builds the stack once and
   runs every adapter through `run_gateway`.
2. Channels differ only in output guidance, so `ConversationService` gains
   `with_guidance(text)`: a shallow view sharing runner, memory, knowledge, and
   mood pass. Safe because scope is per-message `ContextVar` state.
3. The Discord specialist joins the shared roster only when Discord is served.
4. `admin` alone stays model-free; `desktop` (Qt main loop) stays alone.
   `admin_enabled` keeps its old meaning.
5. Shutdown is graceful: adapters wrapping uvicorn implement `request_stop()`;
   `run_gateway` asks, waits a grace period, then cancels.
6. New chat platforms are deny-by-default (Telegram: `telegram_allowed_users`).

## Consequences

- One team, one memory manager, and one set of MCP connections per deployment.
- A crash in any channel still stops the process (the service manager
  restarts it — `magi gateway install`), rather than leaving a half-running bot.
- Adding a platform means one adapter module plus one registry entry.
