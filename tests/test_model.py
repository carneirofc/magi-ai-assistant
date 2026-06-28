"""Regression tests for `build_model` LiteLLM routing.

These guard the two bugs that made the Discord team crash with
`Error in Team run: 'arguments'`:

1. The litellm SDK could not infer a provider from the bare proxy model name
   ("LLM Provider NOT provided") -> the proxy call failed and agno then choked
   formatting the empty response (`KeyError: 'arguments'`). Fixed by prefixing
   the id with `litellm_proxy/`.
2. Databricks-served Claude rejects `temperature` and `top_p` together. agno
   always sends both -> 400 BadRequest. Fixed by dropping `top_p`.
"""

import pytest

from magi.agent.model import (
    LITELLM_PROXY_PREFIX,
    ModelDefinition,
    ModelProviderEnum,
    _provider,
    build_model,
    lead_model_def,
    member_model_def,
)
from magi.core.config import config


def test_provider_resolves_known_names():
    assert _provider("litellm") is ModelProviderEnum.LITELLM
    assert _provider("ollama") is ModelProviderEnum.OLLAMA
    assert _provider("llamacpp") is ModelProviderEnum.LLAMACPP


def test_provider_rejects_unknown_name():
    with pytest.raises(ValueError, match="MODEL_PROVIDER"):
        _provider("made-up")


def test_role_specs_share_the_configured_provider():
    """Both roles read the one configured provider (default: litellm proxy)."""
    want = _provider(config.model_provider)
    assert lead_model_def().provider == want
    assert member_model_def().provider == want


def _litellm(model_id: str, **kwargs):
    return build_model(
        ModelDefinition(
            has_tools=True,
            provider=ModelProviderEnum.LITELLM,
            model_id=model_id,
            **kwargs,
        )
    )


def test_litellm_id_gets_proxy_prefix():
    """Bare proxy model name (as used in team.py) must be prefixed for SDK routing."""
    model = _litellm("databricks-claude-sonnet-4-6")
    assert model.id == "litellm_proxy/databricks-claude-sonnet-4-6"
    assert model.id.startswith(LITELLM_PROXY_PREFIX)


def test_litellm_prefix_not_doubled():
    """An already-prefixed id is left untouched."""
    model = _litellm("litellm_proxy/databricks-claude-sonnet-4-6")
    assert model.id == "litellm_proxy/databricks-claude-sonnet-4-6"


def test_litellm_drops_top_p():
    """top_p must be None so it is omitted from the request."""
    model = _litellm("databricks-claude-sonnet-4-6")
    assert model.top_p is None


def test_request_params_omit_top_p_keep_temperature():
    """The actual outgoing params must not carry top_p (Databricks rejects both)."""
    model = _litellm("databricks-claude-sonnet-4-6")
    params = model.get_request_params()
    assert "top_p" not in params
    assert "temperature" in params


def test_litellm_uses_proxy_credentials():
    """Proxy base_url + key are wired (not direct Databricks creds)."""
    model = _litellm("databricks-claude-sonnet-4-6")
    assert model.api_base == "http://localhost:4000"
    assert model.api_key == "test-key"


def test_format_messages_tolerates_missing_arguments():
    """No-arg tool calls (Databricks Claude) lack `arguments`; must not KeyError.

    Reproduces `Error in Team run: 'arguments'`: an assistant tool_call whose
    function dict has no `arguments` key, as persisted by agno for a no-input
    tool_use, used to crash agno's stock `_format_messages` at the `["arguments"]`
    index. Our hardened subclass normalizes it to "{}".
    """
    from agno.models.message import Message

    model = _litellm("databricks-claude-sonnet-4-6")
    msg = Message(
        role="assistant",
        content="",
        tool_calls=[
            {
                "id": "toolu_x",
                "type": "function",
                "function": {"name": "agent_introspection"},  # no "arguments"
            }
        ],
    )

    formatted = model._format_messages([msg])

    fn = formatted[0]["tool_calls"][0]["function"]
    assert fn["name"] == "agent_introspection"
    assert fn["arguments"] == "{}"


def test_unsupported_provider_raises():
    with pytest.raises(ValueError):
        build_model(
            ModelDefinition.model_construct(
                provider="made-up", model_id="x", has_tools=False
            )
        )


def test_num_ctx_travels_via_extra_body():
    """`num_ctx` must reach the proxy in the request body (-> Ollama options)."""
    model = _litellm("qwen3.5-9b-uncensored", num_ctx=131072)
    assert model.extra_body["num_ctx"] == 131072
    # And it must actually be emitted in the outgoing request params.
    assert model.get_request_params()["extra_body"]["num_ctx"] == 131072


def test_no_extra_body_without_overrides():
    """A plain definition sends no extra_body (nothing to leak into the call)."""
    model = _litellm("qwen3.5-9b-uncensored")
    assert not model.extra_body
    assert "extra_body" not in model.get_request_params()


def test_temperature_override_applied():
    model = _litellm("qwen3.5-9b-uncensored", temperature=0.2)
    assert model.temperature == 0.2


def _llamacpp(model_id: str = "qwen3.5-9b", **kwargs):
    return build_model(
        ModelDefinition(
            has_tools=True,
            provider=ModelProviderEnum.LLAMACPP,
            model_id=model_id,
            **kwargs,
        )
    )


def test_llamacpp_targets_configured_server():
    """Direct builder hits LLAMACPP_BASE_URL with the bare model id (no prefix)."""
    model = _llamacpp()
    assert model.id == "qwen3.5-9b"
    assert model.base_url == "http://localhost:8080/v1"


def test_llamacpp_num_ctx_is_budget_only():
    """llama-server fixes the window at launch (--ctx-size); `num_ctx` must
    never leave the app — there is no per-request context option to set."""
    model = _llamacpp(num_ctx=128000)
    params = model.get_request_params()
    assert "num_ctx" not in (params.get("extra_body") or {})
    assert "num_ctx" not in params


def test_llamacpp_sampling_rides_extra_body():
    """llama.cpp-native sampling params (top_k, min_p, ...) are accepted on
    /v1/chat/completions per-request; extra_body must reach the wire verbatim."""
    model = _llamacpp(extra_body={"top_k": 20, "min_p": 0})
    assert model.get_request_params()["extra_body"] == {"top_k": 20, "min_p": 0}


def test_llamacpp_defers_sampling_to_server_by_default():
    """No overrides set -> nothing sent; the server's launch flags (the model's
    recommended sampling) rule."""
    model = _llamacpp()
    assert model.get_request_params() == {}


def test_llamacpp_temperature_override_applied():
    model = _llamacpp(temperature=0.6)
    assert model.get_request_params()["temperature"] == 0.6


def test_lead_spec_is_multimodal_router_brain():
    """The lead is the multimodal router brain; its window is configured."""
    spec = lead_model_def()
    assert spec.provider == ModelProviderEnum.LITELLM  # default provider (proxy)
    assert spec.model_id == config.lead_model_id
    assert spec.has_tools
    assert spec.supports_image
    assert not spec.supports_audio  # llama-server mmproj: vision only
    assert spec.num_ctx == config.lead_num_ctx


def test_member_window_no_larger_than_lead():
    """Members get a window no larger than the lead's (kept equal for GPU fit)."""
    spec = member_model_def()
    assert spec.has_tools
    assert spec.num_ctx == config.member_num_ctx
    assert spec.num_ctx <= lead_model_def().num_ctx
