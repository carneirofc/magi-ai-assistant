"""Text → vector via the LiteLLM proxy — the one place embedding happens.

Both semantic memory (`core/memory/semantic`) and the knowledge layer
(`core/knowledge`) turn text into vectors through the same proxy route, so the
call lives here once rather than being copied into each. `core` stays model-free
in spirit: this is an HTTP call to *our* proxy (the `litellm_proxy/` prefix routes
it there, mirroring the model factory), not provider-SDK logic.

Crash-proof by contract: any failure — proxy down, model missing, bad response —
logs a warning and returns `None`. Callers treat `None` as "embedding unavailable"
and degrade to a no-op; embedding must never break a chat or an ingest.
"""

from typing import Optional

from agno.utils.log import log_warning

from magi.core.config import config

# LiteLLM SDK prefix that routes an embedding call through our proxy. Without it
# the SDK tries to infer a provider from the bare model id and fails.
_PROXY_PREFIX = "litellm_proxy/"


def _routed(model_id: str) -> str:
    """The model id with exactly one proxy prefix (idempotent)."""
    return f"{_PROXY_PREFIX}{model_id.removeprefix(_PROXY_PREFIX)}"


def embed_text(text: str, *, model_id: Optional[str] = None) -> Optional[list[float]]:
    """Embed one string, or `None` on empty input / any failure.

    `model_id` defaults to `config.embedding_model_id`. The proxy base URL and key
    come from config (same as the chat models).
    """
    if not text.strip():
        return None
    try:
        import litellm

        resp = litellm.embedding(
            model=_routed(model_id or config.embedding_model_id),
            input=[text],
            api_base=config.litellm_base_url,
            api_key=config.litellm_api_key,
        )
        return list(resp.data[0]["embedding"])
    except Exception as exc:  # noqa: BLE001 — embedding must never break a chat/ingest.
        log_warning(f"embeddings: failed ({type(exc).__name__}: {exc})")
        return None
