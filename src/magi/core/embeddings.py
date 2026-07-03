"""Text → vector — the one place embedding happens.

Both semantic memory (`magi/core/memory/semantic`) and the knowledge layer
(`magi/core/knowledge`) turn text into vectors through this single call rather
than copying it into each. It uses the litellm *SDK* either way; what changes is
where the request points, picked by `config.embeddings_provider`:

  - `litellm` (default) — our own proxy (the `litellm_proxy/` prefix routes it
                          there, mirroring the model factory).
  - `openai`            — a remote OpenAI-compatible endpoint (config.openai_base_url
                          / openai_api_key). Lets a deployment serve chat from a
                          local llama-server while sourcing embeddings remotely.

`core` stays model-free in spirit: this is an HTTP call to a configured endpoint,
not provider-SDK branching.

Crash-proof by contract: any failure — endpoint down, model missing, bad response —
logs a warning and returns `None`. Callers treat `None` as "embedding unavailable"
and degrade to a no-op; embedding must never break a chat or an ingest.
"""

from typing import Optional

from agno.utils.log import log_warning

from magi.core.config import Config

# LiteLLM SDK prefix that routes an embedding call through our proxy. Without it
# the SDK tries to infer a provider from the bare model id and fails.
_PROXY_PREFIX = "litellm_proxy/"
# litellm's prefix for "this is an OpenAI-compatible endpoint at api_base" — the
# remote-serving counterpart to the proxy prefix above.
_OPENAI_PREFIX = "openai/"


def _routed(model_id: str) -> str:
    """The model id with exactly one proxy prefix (idempotent)."""
    return f"{_PROXY_PREFIX}{model_id.removeprefix(_PROXY_PREFIX)}"


def _route(model_id: str, config: Config) -> tuple[str, str | None, str | None]:
    """(routed_model, api_base, api_key) for the configured embeddings provider.

    Returns the litellm-prefixed model id plus the endpoint + key it should hit, so
    `embed_text` has one place to call and the provider choice lives here.
    """
    if config.embeddings_provider == "openai":
        routed = f"{_OPENAI_PREFIX}{model_id.removeprefix(_OPENAI_PREFIX)}"
        return routed, config.openai_base_url, config.openai_api_key
    return _routed(model_id), config.litellm_base_url, config.litellm_api_key


def embed_text(text: str, config: Config, *, model_id: Optional[str] = None) -> Optional[list[float]]:
    """Embed one string, or `None` on empty input / any failure.

    `model_id` defaults to `config.embedding_model_id`. The endpoint and key are
    chosen by `config.embeddings_provider` (see module docstring).
    """
    if not text.strip():
        return None
    try:
        import litellm

        routed, api_base, api_key = _route(model_id or config.embedding_model_id, config)
        resp = litellm.embedding(
            model=routed,
            input=[text],
            api_base=api_base,
            api_key=api_key,
        )
        return list(resp.data[0]["embedding"])
    except Exception as exc:  # noqa: BLE001 — embedding must never break a chat/ingest.
        log_warning(f"embeddings: failed ({type(exc).__name__}: {exc})")
        return None
