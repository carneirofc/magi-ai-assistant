# Purpose

The engine's pytest suite. One `tests/test_<area>.py` per subsystem, mirroring
`src/magi/`.

# Local Contracts

- **Config is code-first**, so tests run against `Config` dataclass defaults —
  nothing on the host flips them. Only secrets read from the environment; pin those
  in `conftest.py` *before* `core.config` is imported (e.g. `LITELLM_MASTER_KEY`).
- **async is auto** (`asyncio_mode = "auto"` in `pyproject.toml`) — write
  `async def test_*` directly, no `@pytest.mark.asyncio`.
- Tests must not reach a live model, Qdrant, S3, or network. Exercise the
  degrade-when-absent paths; inject fakes/callables at the seams the code already
  provides.

# Verification

`uv run pytest -q` (from repo root). Add or update the matching `test_*` file with
every behavior change to `src/magi/`.
