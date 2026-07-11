"""Accurate token counting against the live llama-server (best-effort).

Context accounting elsewhere estimates ~4 chars/token because a proxy hides the
real tokenizer. A llamacpp deployment talks straight to llama-server, which
exposes its actual tokenizer at `POST /tokenize` (server root, not under /v1) —
so the context inspector can report real numbers instead of a ballpark.

Best-effort by contract: `count_tokens` returns None when the provider isn't
llamacpp, the server is unreachable, or the reply is malformed — callers fall
back to the estimate. Used OFF the reply path only (the stats endpoint); the
per-turn logging keeps the free estimate. A tiny content-hash cache absorbs
repeat stats calls over unchanged sections.
"""

import hashlib
from typing import Optional

import httpx

from magi.core.config import config

# content-hash -> token count. Bounded by wholesale clear — the working set is
# a handful of context sections, so anything fancier is wasted machinery.
_cache: dict[str, int] = {}
_CACHE_MAX = 512


def _tokenize_root() -> str:
    """llama-server's native endpoints live at the server root; the configured
    base URL points at its OpenAI-compatible /v1."""
    base = config.llamacpp_base_url.rstrip("/")
    return base[: -len("/v1")] if base.endswith("/v1") else base


def count_tokens(text: str) -> Optional[int]:
    """Real token count for `text` via llama-server, or None to fall back."""
    if config.model_provider != "llamacpp":
        return None
    if not text:
        return 0
    key = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if key in _cache:
        return _cache[key]

    headers = {}
    if config.llamacpp_api_key:
        headers["Authorization"] = f"Bearer {config.llamacpp_api_key}"
    try:
        resp = httpx.post(
            f"{_tokenize_root()}/tokenize",
            json={"content": text},
            headers=headers,
            timeout=5.0,
        )
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        tokens = resp.json().get("tokens")
    except ValueError:
        return None
    if not isinstance(tokens, list):
        return None

    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    _cache[key] = len(tokens)
    return len(tokens)
