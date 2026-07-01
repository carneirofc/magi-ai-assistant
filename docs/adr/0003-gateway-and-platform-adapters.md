# 0003 — Gateway and platform adapters

- Status: Accepted
- Date: 2026-07-01
- Builds on: `core/conversation.py` (the `Runner` Protocol precedent)

## Context

Discord and the HTTP API already drive the same channel-neutral brain
(`ConversationService`, via `channels/bootstrap.py`), but each is a bespoke
composition root with no shared contract between "a platform's presentation
layer" and "the shared brain" — they just happen to call the same
`ConversationService` methods by convention, not by anything enforced. Two
concrete gaps fell out of formalizing that convention:

- Nothing describes what a platform's presentation layer must expose for the
  gateway to run it, or names the general "run N long-lived service
  coroutines, first to end takes the rest down" shape a concurrent deployment
  needs (e.g. serving a chat adapter alongside an unrelated HTTP surface in
  one process) — every adapter would otherwise re-derive it ad hoc.
- Every channel hands `ConversationService` a bare, platform-native `user_id`:
  a Discord snowflake, a client-chosen string on the API. `FileMemoryStore`
  keys memory on `user_id` alone, so a Discord user `"123"` and an API caller
  who happens to pick `"123"` would silently share one memory directory — a
  latent identity collision, not yet triggered only because there has been
  exactly one live platform.

## Decision

1. **`PlatformAdapter` Protocol** (`src/magi/channels/gateway.py`) — the
   minimal structural contract a platform's presentation layer satisfies: a
   `platform: str` name and `async def serve_async(self) -> None`.
   Deliberately minimal (mirrors the existing `Runner` Protocol in
   `core/conversation.py`) — inbound/outbound normalization stays each
   adapter's own concern; this only formalizes what the gateway needs to run
   one.
2. **`scoped_user_id(platform, external_id) -> str`** — `f"{platform}:
   {external_id}"`, the fix for the cross-platform identity collision.
   Applied at the channel boundary only: `DiscordClient`'s `on_message` /
   `_maybe_handle_command`, and all five of the API channel's
   `conversation.*` call sites (native contract + the OpenAI-compatible shim
   share one platform name, `"api"` — they are one transport in this
   codebase's docs). Never applied inside `core` —
   `ConversationService`/`MemoryManager` keep taking an opaque string and must
   not learn about "platforms" (the existing strict downward-layering rule,
   `docs/architecture.md`).
3. **`run_gateway(*coros)`** — the general "run N long-lived service
   coroutines concurrently, first to end takes the rest down" primitive,
   living with the contract it serves. No caller on `master` needs it yet
   today, but it's the shape any future concurrent-adapter deployment (e.g.
   an admin HTTP surface alongside a chat adapter in one process) will want,
   so it's added now rather than re-derived per deployment.
4. **Migration: clean break, no migration.** Existing Discord memory under
   the old bare-snowflake path is intentionally allowed to become orphaned —
   `slug()` (`core/memory/adapters.py`) already makes `"discord:123"` a
   distinct, filesystem-safe directory (`discord_123`) from the old `"123"`.
   No migration script is provided or needed.
5. **Two real implementers, not a Protocol with one user.** `DiscordClient`
   gains a `platform = "discord"` class attribute and a `serve_async()`
   coroutine (extracted from the blocking `serve()`, which becomes a thin
   `asyncio.run(self.serve_async())` wrapper) so it satisfies `PlatformAdapter`.
   The API channel gains a small, additive `ApiAdapter` / `build_api_adapter()`
   (`channels/api.py`) that delegates to the existing `build_api_app()` and
   wraps it in a `uvicorn.Server`, so the HTTP channel also demonstrably
   satisfies the Protocol — proof it's a real contract, not a Discord-shaped
   abstraction with one implementer. `main_api.py` is not required to switch
   to it; it keeps calling `uvicorn.run(build_api_app(), ...)` directly.

Deliberately **not** in this slice: per-platform allowlists/admin-tiers, cron,
voice, circuit breakers, or any new platform. This is the seam, not parity
with Hermes' full feature set.

## Consequences

- `clients/mydiscord.py`'s three `self.conversation.*` call sites (`handle`,
  `flush`, `context_stats`) now pass `scoped_user_id(self.platform,
  message_user_id)` instead of the raw snowflake, and `serve()` is now a thin
  wrapper over the new `serve_async()` coroutine.
- `channels/api.py` gains a `_PLATFORM = "api"` constant + `_scoped()` helper
  used at all five user_id-forwarding call sites.
- `tests/test_discord_admin_alongside.py` is replaced by `tests/test_gateway.py`
  (imports `run_gateway`/`scoped_user_id`/`PlatformAdapter` from the new
  module, plus structural-conformance checks for both adapters);
  `tests/test_api.py` and `tests/test_discord_client.py` gained coverage
  asserting the namespaced id reaches `ConversationService`.
- **Known asymmetry, not a bug:** `core/discord_context.py`'s
  `DiscordRunContext.user_id` (exposed to the model via Discord moderation
  tools) stays the RAW Discord snowflake — it's Discord-native identity for
  Discord API calls (`get_partial_message`, etc.), not a memory-scope key, and
  is orthogonal to the namespaced id `ConversationService` sees. A comment at
  its definition points here.
- `channels/admin.py` is unaffected — verified `FileMemoryStore.list_users()`
  returns on-disk slugs (not the pre-slug scoped string) and `scoped()`
  re-slugs whatever it's given, so an operator browsing the user list and
  pasting an id back into a lookup round-trips correctly regardless of this
  change. No admin-side changes needed.
