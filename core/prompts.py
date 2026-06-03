"""Markdown-backed prompt templates.

Edit the files under `prompts/` to change agent behavior — no code edit needed.
Each loader falls back to a built-in default when the file is missing or empty,
so a fresh checkout (no `prompts/` yet) still runs.

Precedence for the system prompt lives in `core.config`: an explicit env var
wins over the file, the file wins over the hard-coded default. That keeps deploys
overridable via env while making local editing a matter of touching markdown.
"""

from pathlib import Path

# Repo-root/prompts. core/ is one level down, so parent.parent is the root.
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_prompt(name: str, default: str = "") -> str:
    """Read `prompts/<name>` (e.g. "system.md" or "team/lead.md").

    Returns the file's text (stripped). Falls back to `default` if the file is
    absent or blank. Markdown is passed through verbatim — the whole file is the
    prompt, so write it as you want the model to read it.
    """
    path = PROMPTS_DIR / name
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return default
    return text or default
