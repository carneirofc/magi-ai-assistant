"""Tests for the runtime thinking toggle.

The tools mutate the live model objects' `extra_body` so the *next* request
renders the chat template with `enable_thinking` flipped. We verify the flag
lands in the outgoing request params, existing extra_body keys survive, and
every model in the team (lead + shared member model) is updated together.
"""

from magi.agent.model import ModelDefinition, ModelProviderEnum, build_model
from magi.agent.tools.thinking import build_thinking_tools


def _tool_text(result: dict) -> str:
    return result.get("message", "")


def _model(**kwargs):
    return build_model(
        ModelDefinition(
            provider=ModelProviderEnum.LLAMACPP,
            model_id="qwen3.5-9b",
            has_tools=True,
            **kwargs,
        )
    )


def _tools(*models):
    set_thinking, get_thinking = build_thinking_tools(list(models))
    assert set_thinking.show_result is True
    assert get_thinking.show_result is True
    return set_thinking, get_thinking


def test_disable_thinking_reaches_request_params():
    model = _model()
    set_thinking, _ = _tools(model)

    msg = set_thinking.entrypoint(False)

    assert "disabled" in _tool_text(msg)
    body = model.get_request_params()["extra_body"]
    assert body["chat_template_kwargs"]["enable_thinking"] is False


def test_enable_thinking_overrides_env_default():
    """A model built with thinking off (the .env default) can be flipped back on."""
    model = _model(extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    set_thinking, _ = _tools(model)

    set_thinking.entrypoint(True)

    body = model.get_request_params()["extra_body"]
    assert body["chat_template_kwargs"]["enable_thinking"] is True


def test_toggle_preserves_other_extra_body_keys():
    """Sampling overrides riding extra_body must survive the toggle."""
    model = _model(extra_body={"top_k": 20, "min_p": 0})
    set_thinking, _ = _tools(model)

    set_thinking.entrypoint(False)

    body = model.get_request_params()["extra_body"]
    assert body["top_k"] == 20 and body["min_p"] == 0
    assert body["chat_template_kwargs"]["enable_thinking"] is False


def test_toggle_hits_every_model_in_the_team():
    """Lead and the shared member model flip together — no half-toggled team."""
    lead, member = _model(), _model()
    set_thinking, _ = _tools(lead, member)

    set_thinking.entrypoint(False)

    for m in (lead, member):
        kwargs = m.get_request_params()["extra_body"]["chat_template_kwargs"]
        assert kwargs["enable_thinking"] is False


def test_get_thinking_reports_state():
    model = _model()
    set_thinking, get_thinking = _tools(model)

    assert "default" in _tool_text(get_thinking.entrypoint())
    set_thinking.entrypoint(False)
    assert "disabled" in _tool_text(get_thinking.entrypoint())
    set_thinking.entrypoint(True)
    assert "enabled" in _tool_text(get_thinking.entrypoint())
