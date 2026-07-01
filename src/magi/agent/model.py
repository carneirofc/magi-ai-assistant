"""Provider-agnostic model factory.

A `ModelDefinition` is a declarative spec — id, capabilities, context window.
`build_model` is the single place that turns one into a concrete agno `Model`,
dispatching per provider. Role helpers (`build_lead_model` / `build_member_model`)
read the spec from config, so all model wiring lives in one place and the team
code stays declarative.

Add a provider: extend `ModelProviderEnum`, write a `_build_<provider>` function,
and register it in `_BUILDERS`.
"""

import enum
from collections.abc import Callable
from functools import lru_cache

import pydantic
from agno.models.base import Model
from agno.utils.log import log_info

from magi.core.config import config


class ModelProviderEnum(enum.StrEnum):
    OLLAMA = "ollama"
    LITELLM = "litellm"
    LLAMACPP = "llamacpp"
    OPENAI = "openai"


# Prefix that tells the litellm SDK "this id is served by a litellm proxy at
# api_base", instead of inferring a provider from the bare name (which fails with
# "LLM Provider NOT provided").
LITELLM_PROXY_PREFIX = "litellm_proxy/"


class ModelDefinition(pydantic.BaseModel):
    """Declarative model spec — everything `build_model` needs, nothing it doesn't."""

    provider: ModelProviderEnum
    model_id: str
    has_tools: bool = False

    # Multimodal capabilities. The model formats whatever media it is handed;
    # these flags document what it can actually use, for routing / introspection.
    supports_image: bool = False
    supports_audio: bool = False

    # Generation + context controls. `num_ctx` is the context window in tokens;
    # for Ollama it maps to the `num_ctx` runtime option (see the builders).
    num_ctx: int | None = None
    temperature: float | None = None
    max_tokens: int | None = None

    # Escape hatch for provider-specific request-body params.
    extra_body: dict = pydantic.Field(default_factory=dict)


@lru_cache(maxsize=1)
def _proxy_litellm_cls():
    """A LiteLLM subclass that tolerates no-argument tool calls (built once).

    Some proxied backends (Databricks-served Claude) emit tool_use blocks with no
    input. Those get persisted as a tool_call whose `function` dict has no
    `arguments` key, and agno's stock `_format_messages` indexes `["arguments"]`
    directly — raising `KeyError: 'arguments'` on the *next* call (the
    `Error in Team run: 'arguments'` the Discord bot hit). We normalize so
    `arguments` is always present: on parse (fresh calls stored clean) and on
    format (already-poisoned history loads without crashing).
    """
    from agno.models.litellm import LiteLLM

    def _normalize(tool_calls):
        for tc in tool_calls or []:
            fn = tc.get("function")
            if isinstance(fn, dict):
                fn.setdefault("name", "")
                if not fn.get("arguments"):
                    fn["arguments"] = "{}"
        return tool_calls

    class ProxyLiteLLM(LiteLLM):
        def _parse_provider_response(self, response, **kwargs):
            mr = super()._parse_provider_response(response, **kwargs)
            _normalize(mr.tool_calls)
            return mr

        def _format_messages(self, messages, compress_tool_results: bool = False):
            for m in messages:
                if getattr(m, "role", None) == "assistant" and m.tool_calls:
                    _normalize(m.tool_calls)
            return super()._format_messages(messages, compress_tool_results)

    return ProxyLiteLLM


def _build_litellm(model: ModelDefinition) -> Model:
    # agno's LiteLLM uses the litellm *SDK*. The `litellm_proxy/` prefix routes
    # the call through our proxy; without it the SDK tries to guess a provider.
    model_id = model.model_id
    if not model_id.startswith(LITELLM_PROXY_PREFIX):
        model_id = f"{LITELLM_PROXY_PREFIX}{model_id}"

    llm = _proxy_litellm_cls()(
        id=model_id,
        api_key=config.litellm_api_key,
        api_base=config.litellm_base_url,
    )

    # Databricks-served Claude rejects `temperature` and `top_p` together; agno
    # always sends both, so drop top_p and keep temperature.
    llm.top_p = None
    if model.temperature is not None:
        llm.temperature = model.temperature
    if model.max_tokens is not None:
        llm.max_tokens = model.max_tokens

    # Body params that ride through the proxy to the backend. `num_ctx` is an
    # Ollama runtime option; sending it via extra_body guarantees it lands in the
    # HTTP body to the proxy, which forwards it to Ollama's `options.num_ctx`.
    extra_body = dict(model.extra_body)
    if model.num_ctx is not None:
        extra_body.setdefault("num_ctx", model.num_ctx)
    if extra_body:
        llm.extra_body = extra_body

    return llm


def _build_llamacpp(model: ModelDefinition) -> Model:
    """Direct llama.cpp llama-server via its OpenAI-compatible /v1 endpoint.

    The server fixes its context window at launch (--ctx-size), so `num_ctx`
    here is a budget for context assembly only and is never transmitted —
    unlike Ollama, there is no per-request context option. Sampling overrides
    *are* per-request: /v1/chat/completions accepts llama.cpp-native params
    (top_k, min_p, mirostat, ...) alongside the OpenAI fields, so `extra_body`
    rides through verbatim. With no overrides set, the server's launch flags
    (the model's recommended sampling) rule.
    """
    from agno.models.openai.like import OpenAILike

    llm = OpenAILike(
        id=model.model_id,
        base_url=config.llamacpp_base_url,
        # The openai client demands a key even when the server enforces none.
        api_key=config.llamacpp_api_key or "sk-no-key",
    )
    if model.temperature is not None:
        llm.temperature = model.temperature
    if model.max_tokens is not None:
        llm.max_tokens = model.max_tokens
    if model.extra_body:
        llm.extra_body = dict(model.extra_body)
    return llm


def _build_openai(model: ModelDefinition) -> Model:
    """A generic OpenAI-compatible remote serving endpoint (config.openai_base_url).

    For any hosted server that speaks the OpenAI /v1 API — real OpenAI, OpenRouter,
    Together, a remote vLLM / llama-server, … Uses agno's `OpenAILike` (not
    `OpenAIChat`) so it tolerates servers that don't implement every OpenAI-only
    field, the same lenient client the local llamacpp builder uses. The only
    difference from `llamacpp` is where it points: a remote URL + a real API key
    (the model id is whatever the remote names it). Sampling overrides ride through
    `extra_body` verbatim, exactly as for llamacpp.
    """
    from agno.models.openai.like import OpenAILike

    llm = OpenAILike(
        id=model.model_id,
        base_url=config.openai_base_url,
        # Most hosted endpoints require a real key; keep a placeholder so the openai
        # client still constructs against a keyless dev server.
        api_key=config.openai_api_key or "sk-no-key",
    )
    if model.temperature is not None:
        llm.temperature = model.temperature
    if model.max_tokens is not None:
        llm.max_tokens = model.max_tokens
    if model.extra_body:
        llm.extra_body = dict(model.extra_body)
    return llm


def _build_ollama(model: ModelDefinition) -> Model:
    """Direct Ollama, bypassing the proxy. Handy for local dev / offline tests."""
    from agno.models.ollama import Ollama

    options = {"num_ctx": model.num_ctx} if model.num_ctx is not None else None
    if model.temperature is not None:
        options = {**(options or {}), "temperature": model.temperature}
    return Ollama(id=model.model_id, host=config.ollama_host, options=options)


_BUILDERS: dict[ModelProviderEnum, Callable[[ModelDefinition], Model]] = {
    ModelProviderEnum.LITELLM: _build_litellm,
    ModelProviderEnum.OLLAMA: _build_ollama,
    ModelProviderEnum.LLAMACPP: _build_llamacpp,
    ModelProviderEnum.OPENAI: _build_openai,
}


def build_model(model: ModelDefinition) -> Model:
    """Turn a `ModelDefinition` into a concrete agno `Model`."""
    log_info(f"building model: {model.model_dump()}")
    builder = _BUILDERS.get(model.provider)
    if builder is None:
        raise ValueError(f"unsupported model provider: {model.provider!r}")
    return builder(model)


# --- Role specs -------------------------------------------------------------
# One spec per functional role, sourced from config. This is the place to retune
# a role (model id, context window, capabilities) without touching team code.


def _provider(raw: str) -> ModelProviderEnum:
    """Map a `MODEL_PROVIDER` string to the enum, with a clear error on typos."""
    try:
        return ModelProviderEnum(raw)
    except ValueError:
        valid = [e.value for e in ModelProviderEnum]
        raise ValueError(f"unknown MODEL_PROVIDER {raw!r}; use one of {valid}") from None


def lead_model_def() -> ModelDefinition:
    """The lead/router brain: tools + multimodal + a 128k context window."""
    return ModelDefinition(
        provider=_provider(config.model_provider),
        model_id=config.lead_model_id,
        has_tools=True,
        supports_image=True,
        # llama-server with the Qwen3.5 mmproj reports modalities vision=true,
        # audio=false (GET /props) — flip when an audio-capable backend lands.
        supports_audio=False,
        num_ctx=config.lead_num_ctx,
        temperature=config.model_temperature,
        extra_body=config.model_extra_body,
    )


def member_model_def() -> ModelDefinition:
    """A specialist member: tools + multimodal, smaller context window."""
    return ModelDefinition(
        provider=_provider(config.model_provider),
        model_id=config.member_model_id,
        has_tools=True,
        supports_image=True,
        # llama-server with the Qwen3.5 mmproj reports modalities vision=true,
        # audio=false (GET /props) — flip when an audio-capable backend lands.
        supports_audio=False,
        num_ctx=config.member_num_ctx,
        temperature=config.model_temperature,
        extra_body=config.model_extra_body,
    )


def build_lead_model() -> Model:
    return build_model(lead_model_def())


def build_member_model() -> Model:
    return build_model(member_model_def())
