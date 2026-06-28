"""Markdown-backed prompt templates, with a private-overlay search path.

Edit the files under `prompts/` to change agent behavior — no code edit needed.
The prompt files are part of the repo: a missing file is a packaging error and
raises immediately rather than silently running with an empty brain.

Overlay precedence — a persona repo (e.g. `alyssa`) installs this engine and
supplies its *own* prompts without editing the public tree: it calls
`set_prompt_overlay(<its-prompts-dir>)` at its entrypoint, and any prompt found
there wins over the bundled demo `prompts/`. Lookups fall back to the bundled
copy, so the engine still boots out of the box with its neutral demo persona.

Precedence for the system prompt lives in `core.config`: an explicit env var
wins over the file. That keeps deploys overridable via env while making local
editing a matter of touching markdown.
"""

from pathlib import Path

from agno.utils.log import log_info

# Repo-root/prompts — the bundled demo persona. core/ is one level down, so
# parent.parent is the root. An editable install resolves __file__ to the engine
# working tree, so this points at the real prompts/ during local dev.
BUNDLED_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# Overlay dirs searched before the bundled dir (highest precedence first). Empty
# by default — the engine uses only its bundled prompts. A persona sets this at
# startup via set_prompt_overlay(); see module docstring.
_OVERLAY_DIRS: list[Path] = []


def set_prompt_overlay(*dirs: str | Path) -> None:
    """Set the private prompt overlay search path (replaces any previous value).

    Call once at the entrypoint, before building any team/channel. Earlier dirs
    win over later ones, and all win over the bundled `prompts/`. Pass no args to
    clear the overlay (back to bundled-only).
    """
    global _OVERLAY_DIRS
    _OVERLAY_DIRS = [Path(d) for d in dirs]
    log_info(
        "prompt overlay set: "
        + (", ".join(str(d) for d in _OVERLAY_DIRS) if _OVERLAY_DIRS else "(none)")
    )


def _resolve(name: str) -> Path:
    """First existing `<dir>/<name>` across overlay dirs then the bundled dir.

    Falls back to the bundled path even when it's missing so the caller raises a
    clear FileNotFoundError on read (a missing prompt is a packaging error).
    """
    for base in (*_OVERLAY_DIRS, BUNDLED_PROMPTS_DIR):
        candidate = base / name
        if candidate.is_file():
            return candidate
    return BUNDLED_PROMPTS_DIR / name


def load_prompt(name: str) -> str:
    """Read `prompts/<name>` (e.g. "team/lead.md"), stripped.

    The overlay wins over the bundled copy (see module docstring). Markdown is
    passed through verbatim — the whole file is the prompt, so write it as you
    want the model to read it. Raises if the file is missing everywhere.
    """
    path = _resolve(name)
    text = path.read_text(encoding="utf-8").strip()

    log_info(f"prompt '{name}' loaded from {path} ({len(text)} chars)")
    return text
