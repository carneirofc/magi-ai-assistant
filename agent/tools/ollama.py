"""Ollama introspection tools.

Query the Ollama server (config.ollama_host) for what models exist, their
capabilities / context length, and what is currently loaded. Each docstring is
read by the model to decide WHEN to call the tool — keep it precise.

Tools return short human-readable strings (and never raise): on failure they
return an error line the model can relay, so a down server degrades gracefully.
"""

import httpx
from agno.tools import tool

from core.config import config

_TIMEOUT = 10.0


def _get(path: str, timeout: float = _TIMEOUT) -> dict:
    r = httpx.get(f"{config.ollama_host}{path}", timeout=timeout)
    r.raise_for_status()
    return r.json()


def _post(path: str, body: dict, timeout: float = _TIMEOUT) -> dict:
    r = httpx.post(f"{config.ollama_host}{path}", json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _context_length(model_info: dict) -> int | None:
    """Pull the "<arch>.context_length" value out of an /api/show model_info."""
    return next(
        (v for k, v in (model_info or {}).items() if k.endswith(".context_length")),
        None,
    )


@tool
def list_ollama_models() -> str:
    """List models installed on the Ollama server.

    Use when asked what models Ollama has / are available locally. Returns each
    model's name, parameter size, quantization, and on-disk size.
    """
    try:
        data = _get("/api/tags")
    except Exception as e:
        return f"Failed to reach Ollama at {config.ollama_host}: {e}"
    models = data.get("models", [])
    if not models:
        return "No models installed on Ollama."
    lines = []
    for m in models:
        d = m.get("details", {}) or {}
        size_gb = (m.get("size", 0) or 0) / 1e9
        lines.append(
            f"- {m.get('name')}: {d.get('parameter_size', '?')} params, "
            f"{d.get('quantization_level', '?')}, {size_gb:.1f} GB"
        )
    return "Ollama models:\n" + "\n".join(lines)


@tool
def show_ollama_model(model: str) -> str:
    """Show details for one Ollama model: capabilities, context length, params.

    `model` is the model name (e.g. "qwen3.5-9b-uncensored" or "gemma4:e4b"). Use
    to check whether a model supports vision/tools or how large its native context
    window is.
    """
    try:
        data = _post("/api/show", {"model": model})
    except Exception as e:
        return f"Failed to show '{model}': {e}"
    caps = data.get("capabilities", []) or []
    ctx = _context_length(data.get("model_info", {}))
    d = data.get("details", {}) or {}
    return (
        f"Model {model}:\n"
        f"- family: {d.get('family', '?')} "
        f"({d.get('parameter_size', '?')}, {d.get('quantization_level', '?')})\n"
        f"- capabilities: {', '.join(caps) or 'unknown'}\n"
        f"- native context length: {ctx or 'unknown'}"
    )


@tool
def list_running_ollama_models() -> str:
    """List models currently loaded in Ollama memory, with loaded context size.

    Use to see what is running now and the context window each model was loaded
    with — handy to confirm a model actually loaded at the expected num_ctx.
    """
    try:
        data = _get("/api/ps")
    except Exception as e:
        return f"Failed to reach Ollama at {config.ollama_host}: {e}"
    models = data.get("models", [])
    if not models:
        return "No models currently loaded in Ollama."
    lines = []
    for m in models:
        vram_gb = (m.get("size_vram", 0) or 0) / 1e9
        ctx = m.get("context_length")
        lines.append(f"- {m.get('name')}: loaded_ctx={ctx or '?'}, vram={vram_gb:.1f} GB")
    return "Loaded Ollama models:\n" + "\n".join(lines)
