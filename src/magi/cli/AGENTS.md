# Purpose

The `magi` command line (`[project.scripts] magi = "magi.cli:main"`): the
operator's entry point to set up, validate, and run a deployment without
editing Python.

# Ownership

- `__init__.py` — argparse tree and dispatch (`setup`, `run`, `doctor`,
  `skills`, `gateway`, `config path|show|get|set`).
- `run.py` — `prepare()` (secrets → config files → chdir to the data root) and
  the channel runners `main.py` also uses.
- `config_cmd.py` — `magi config …`; edits go through
  `core/config_file.write_config_value` (validated, atomic).
- `doctor.py` — ok/warn/fail checks over the effective `Config`.
- `service.py` — `magi gateway install|uninstall|status|logs`: a systemd
  *user* unit (`render_unit` is pure; systemctl/journalctl go through an
  injected runner).
- `skills_cmd.py` — `magi skills list`: skills (source, active) and toolsets.
- `setup.py` — first-run wizard writing `$MAGI_HOME/config.yaml` and
  `$MAGI_HOME/.env` (0600).

# Local Contracts

- **Composition root above `channels`**: `cli` → `channels` → `agent` → `core`.
  Nothing below imports `magi.cli`.
- **magi imports stay inside functions** in `__init__.py`, so the agno log
  level is set before `core.config` is first imported (keeps management
  commands quiet). `run` restores INFO before serving.
- **Only `prepare()` applies config files** to the singleton; commands never
  call `configure()` with file data directly.
- **Secrets never land in `config.yaml`** — the wizard writes them to `.env`.
- **Doctor checks are pure over `Config`**; network probes go through
  `doctor.http_status` so tests replace it. A crashing check reports `fail`.
- Interactive input goes through the `Prompter` protocol (tests script it).
- **No root, no system files**: the service is a user unit under
  `~/.config/systemd/user`; `scripts/install.sh` installs with `uv tool`.

# Verification

`uv run pytest -q tests/test_cli.py tests/test_config_file.py tests/test_service.py`
