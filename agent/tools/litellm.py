"""LiteLLM proxy introspection tools.

Query the LiteLLM proxy (config.litellm_base_url) for which model_names it serves,
how they map to backends, and proxy health. Each docstring is read by the model to
decide WHEN to call the tool.

Tools return short human-readable strings (and never raise): on failure they return
an error line the model can relay.
"""

import httpx
from agno.tools import tool

from core.config import config

_TIMEOUT = 10.0


def _headers() -> dict:
    key = config.litellm_api_key
    return {"Authorization": f"Bearer {key}"} if key else {}


def _get(path: str, timeout: float = _TIMEOUT) -> dict:
    r = httpx.get(
        f"{config.litellm_base_url}{path}", headers=_headers(), timeout=timeout
    )
    r.raise_for_status()
    return r.json()


@tool(
    description="List model names served by the configured LiteLLM proxy.",
    instructions="Use when asked which LiteLLM models are configured or callable by the app. Takes no arguments.",
    show_result=True,
)
def list_litellm_models() -> str:
    """List the model_names served by the LiteLLM proxy.

    Use when asked what models can be called / are configured in litellm. These
    are the ids the app uses (e.g. as LEAD_MODEL_ID / MEMBER_MODEL_ID).
    """
    try:
        data = _get("/v1/models")
    except Exception as e:
        return f"Failed to reach LiteLLM proxy at {config.litellm_base_url}: {e}"
    ids = [m.get("id") for m in data.get("data", []) if m.get("id")]
    if not ids:
        return "LiteLLM proxy lists no models."
    return "LiteLLM models:\n" + "\n".join(f"- {i}" for i in ids)


@tool(
    description="Show LiteLLM model-name to backend mappings and token limits.",
    instructions="Use to identify which provider/backend a LiteLLM model routes to. Optional model filters by model_name.",
    show_result=True,
)
def litellm_model_info(model: str | None = None) -> str:
    """Show LiteLLM model_name -> backend mapping (and per-model token limits).

    Optional `model` filters to one model_name. Use to see which provider/backend
    a model_name routes to (e.g. ollama/... or databricks/...) and its token caps.
    """
    try:
        data = _get("/model/info")
    except Exception as e:
        return f"Failed to read LiteLLM model info: {e}"
    rows = data.get("data", [])
    if model:
        rows = [r for r in rows if r.get("model_name") == model]
    if not rows:
        return f"No LiteLLM model info{f' for {model!r}' if model else ''}."
    lines = []
    for r in rows:
        backend = (r.get("litellm_params") or {}).get("model", "?")
        info = r.get("model_info") or {}
        ctx = info.get("max_input_tokens") or info.get("max_tokens")
        lines.append(
            f"- {r.get('model_name')} -> {backend}"
            + (f" (max_tokens={ctx})" if ctx else "")
        )
    return "LiteLLM model mapping:\n" + "\n".join(lines)


@tool(
    description="Check LiteLLM proxy health for configured model endpoints.",
    instructions="Use to diagnose model-call failures through LiteLLM. This can take a few seconds. Takes no arguments.",
    show_result=True,
)
def litellm_health() -> str:
    """Check LiteLLM proxy health: which model endpoints are healthy/unhealthy.

    Use to diagnose why a model call is failing. The proxy pings each backend, so
    this can take a few seconds.
    """
    try:
        data = _get("/health", timeout=30.0)
    except Exception as e:
        return f"Failed to reach LiteLLM proxy at {config.litellm_base_url}: {e}"
    healthy = data.get("healthy_count", "?")
    unhealthy = data.get("unhealthy_count", "?")
    lines = [f"healthy={healthy}, unhealthy={unhealthy}"]
    for ep in (data.get("unhealthy_endpoints") or [])[:10]:
        err = str(ep.get("error", ""))[:120]
        lines.append(f"DOWN {ep.get('model', '?')}: {err}")
    return "LiteLLM health:\n" + "\n".join(lines)
