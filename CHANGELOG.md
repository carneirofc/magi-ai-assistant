# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Top-level `Assistant.create(config)` factory (`magi/app.py`) as the library
  front door — one call builds the whole stack (memory, team, conversation
  service). Exported from the package root alongside `Config` and `AgentContext`.
- `AgentContext` (`magi/core/context.py`): the injected runtime bundle carrying
  the immutable `Config` plus lazily-built shared services (the db today), threaded
  through the whole build graph.

### Changed
- Configuration is now **instance-scoped**: `Config` is a frozen value object a
  deployment constructs directly (`Config(model_provider="llamacpp", ...)`) and
  threads through `AgentContext`. Two assistants with different configs can coexist
  in one process. Vary an existing config immutably with `dataclasses.replace`.
- Every builder now takes explicit config/context: `build_conversation_service(ctx, ...)`,
  `build_team(ctx, memory, ...)`, `build_api_app(ctx, ...)`, `build_admin_app(ctx)`,
  `build_discord_client(ctx, ...)`, the model/memory/knowledge/storage/embedding
  factories, and the tool builders (`build_http_tools(config)`, etc.).
- Team member builders now take `(ctx, model)` instead of `(model)` — the seam a
  persona/plugin extends via `register_member`.
- The opt-in tool suites (seanime, danbooru, litellm, ollama) are now
  `build_*_tools(config)` factories instead of module-level tool-list constants.
- Entrypoints' `apply_deployment_config()` returns a `Config`; container entrypoints
  overlay their host deltas with `dataclasses.replace` instead of a second mutate.

### Removed
- **BREAKING:** the process-global `magi.core.config.config` singleton and the
  `configure(**overrides)` function. Construct a `Config` and pass it to
  `Assistant.create` / the channel builders (which take an `AgentContext`).
- **BREAKING:** the legacy `s3_enabled` config shim (it lived in `configure()`).
  Select the S3 object-store backend with `storage_enabled=True` +
  `storage_backend="s3"`.
