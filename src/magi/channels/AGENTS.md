# Purpose

The **transport layer**: adapters that carry one inbound message to the shared
`ConversationService` and its reply back out. Discord, the HTTP/chat API, the admin
API, and the shared wiring live here.

# Local Contracts

- **One shared assembler.** Every channel builds its stack through
  `build_conversation_service` (`bootstrap.py`) in a fixed order: summarizers
  (gated) → memory → team → `ConversationService`. Channels add only
  transport-specific pieces (a `discord.Client`, the FastAPI app, auth, CORS, MCP
  lifespan, channel output-guidance prompt) — never re-implement the wiring.
- **Adapters satisfy `PlatformAdapter` by shape** (`gateway.py`, a `Protocol`; see
  [ADR 0003](../../../docs/adr/0003-gateway-and-platform-adapters.md)), not by
  inheritance. The gateway owns `scoped_user_id` and hands scope to the service.
- **Channels hold no conversation logic.** Run + memory flow belong to
  `core/conversation.py`; a channel only translates transport ↔ the service's
  `handle` / `handle_stream` calls.
- The **admin API** (`admin.py`) backs the web BFF (memory + knowledge operations,
  [ADR 0002](../../../docs/adr/0002-admin-interface-for-memory-and-knowledge.md));
  the **chat API** (`api.py`) runs the team and streams SSE. Keep their auth-token
  boundaries intact — both are meant to sit behind the BFF, not exposed publicly.

# Work Guidance

Endpoint / SSE / media contracts: [../../../docs/channels.md](../../../docs/channels.md).
When an endpoint's shape changes, the web BFF and its generated API types
(`web/`) may need regenerating — flag it.

# Verification

`uv run pytest -q` (`tests/test_api.py`, `test_gateway.py`, `test_admin.py`).
