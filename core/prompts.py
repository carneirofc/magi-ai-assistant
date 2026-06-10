"""Markdown-backed prompt templates.

Edit the files under `prompts/` to change agent behavior — no code edit needed.
The prompt files are part of the repo: a missing file is a packaging error and
raises immediately rather than silently running with an empty brain.

Precedence for the system prompt lives in `core.config`: an explicit env var
wins over the file. That keeps deploys overridable via env while making local
editing a matter of touching markdown.
"""

from pathlib import Path

from agno.utils.log import log_info

# Repo-root/prompts. core/ is one level down, so parent.parent is the root.
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_prompt(name: str) -> str:
    """Read `prompts/<name>` (e.g. "team/lead.md"), stripped.

    Markdown is passed through verbatim — the whole file is the prompt, so write
    it as you want the model to read it. Raises if the file is missing.
    """
    path = PROMPTS_DIR / name
    text = path.read_text(encoding="utf-8").strip()

    log_info(f"prompt '{name}' loaded from {path} ({len(text)} chars)")
    return text
