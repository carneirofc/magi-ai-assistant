# Purpose

The engine's pytest suite. One `tests/test_<area>.py` per subsystem, mirroring
`src/magi/`.

# Local Contracts

- **Tests run against `Config` defaults** — nothing on the host flips them.
  `conftest.py` pins secrets and points `MAGI_HOME` at an empty temp dir *before*
  `core.config` is imported, and an autouse fixture restores the config
  singleton after every test, so `configure(...)` in a test never leaks. For a
  one-off variant without touching the singleton use `derive(config, ...)`.
- **async is auto** (`asyncio_mode = "auto"` in `pyproject.toml`) — write
  `async def test_*` directly, no `@pytest.mark.asyncio`.
- Tests must not reach a live model, Qdrant, S3, or network. Exercise the
  degrade-when-absent paths; inject fakes/callables at the seams the code already
  provides.

# Verification

`uv run pytest -q` and `uv run basedpyright` (from repo root). Tests are
type-checked too. Add or update the matching `test_*` file with
every behavior change to `src/magi/`.
