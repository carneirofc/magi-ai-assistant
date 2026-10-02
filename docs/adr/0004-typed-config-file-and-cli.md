# 0004 — Typed config file and the `magi` CLI

- Status: Accepted
- Date: 2026-09-27
- Supersedes: the "code-first config only" contract in `src/magi/AGENTS.md`

## Context

Config was plain Python: a frozen dataclass of ~110 defaults plus
`configure(**overrides)` calls in `main.py`, one block per channel. It kept
"what is this value?" answerable by reading code, but:

- `configure()` checked names, never types — a string port or a misspelled
  provider failed deep inside a run.
- Every deployment change meant editing Python, and `main.py` repeated the
  brain config per channel with `--docker` overlays inline.
- A bare-metal install (`uv tool install`) has no editable `main.py` at all.

## Decision

1. **`Config` is a frozen pydantic model** (`extra="forbid"`). `configure()`
   validates the merged result before applying anything; closed sets are
   `Literal`s; groups are nested models (`channels`). The singleton object and
   the `configure()` API are unchanged, so every reader keeps working.
2. **A YAML config file** (`core/config_file.py`) whose keys are `Config` field
   names. Lookup: `./magi.yaml`, else `$MAGI_HOME/config.yaml`
   (`MAGI_HOME` defaults to `~/.magi`). Secrets stay in `.env`
   (`$MAGI_HOME/.env`, then `./.env`; the environment always wins).
3. **Precedence**: defaults < config file(s) (later `-c` wins) < `configure()`
   in code < CLI flags.
4. **Relative paths resolve against the data root** — the primary config
   file's directory, or `MAGI_HOME` without one. The CLI changes into it
   before serving.
5. **The `magi` CLI** (`src/magi/cli/`) is a composition root above
   `channels`: `setup` (wizard), `run`, `config path|show|get|set`, `doctor`.
   `main.py` remains as a thin code-first wrapper for the repo and containers.
6. Loading is explicit (`load_config` / `cli.run.prepare`), never at import.

## Consequences

- A typo or wrong type in any config source is a startup error naming the key.
- Operators never edit Python to deploy; code stays the place for extensions
  (skills, members, overlays) that YAML cannot express.
- `magi.yaml` + `docker/magi.docker.yaml` replace `main.py`'s per-channel and
  `--docker` blocks.
