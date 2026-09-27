#!/usr/bin/env bash
# Install magi as a user-level tool, then run the setup wizard.
#
#   curl -LsSf https://raw.githubusercontent.com/carneirofc/magi-ai-assistant/master/scripts/install.sh | bash
#
# Environment knobs:
#   MAGI_EXTRAS   comma-separated extras, e.g. "telegram,semantic,mcp" (default: telegram)
#   MAGI_SOURCE   what to install instead of the PyPI release: a path to a
#                 checkout or a git URL (git+https://github.com/…/magi-ai-assistant)
#   MAGI_NO_SETUP set to 1 to skip `magi setup`
#
# Everything lands under your user: the tool in uv's tool dir (~/.local/bin/magi),
# config and secrets in ~/.magi. Nothing needs root.
set -euo pipefail

extras="${MAGI_EXTRAS-telegram}"
source_spec="${MAGI_SOURCE:-magi-ai-assistant}"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }

if ! command -v uv >/dev/null 2>&1; then
    say "installing uv (https://docs.astral.sh/uv/)"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

if [[ -n "$extras" ]]; then
    if [[ "$source_spec" == git+* || "$source_spec" == */* || "$source_spec" == . ]]; then
        target="magi-ai-assistant[$extras] @ $source_spec"
        [[ -d "$source_spec" ]] && target="$source_spec[$extras]"
    else
        target="$source_spec[$extras]"
    fi
else
    target="$source_spec"
fi

say "installing $target (Python 3.14)"
uv tool install --python 3.14 --force "$target"

if ! command -v magi >/dev/null 2>&1; then
    say "adding uv's tool dir to PATH (restart your shell afterwards)"
    uv tool update-shell || true
    export PATH="$(uv tool dir --bin):$PATH"
fi

if [[ "${MAGI_NO_SETUP:-0}" != "1" && -t 0 ]]; then
    say "running the setup wizard"
    magi setup
elif [[ "${MAGI_NO_SETUP:-0}" != "1" ]]; then
    say "no terminal attached — run \`magi setup\` next"
fi

cat <<'NEXT'

Next steps:
  magi doctor                 # re-check the config and backends
  magi run                    # serve in the foreground
  magi gateway install        # or: run as a systemd user service (starts at login)
NEXT
