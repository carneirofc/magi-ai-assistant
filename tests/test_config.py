"""Tests for the typed config singleton (core/config)."""

import pytest
from pydantic import ValidationError

from magi.core import config as config_mod
from magi.core.config import Config, config, configure, derive, reset_config, secret_fields


def test_configure_validates_types():
    with pytest.raises(ValidationError):
        configure(api_port="not-a-port")


def test_configure_rejects_unknown_field():
    with pytest.raises(ValidationError):
        configure(no_such_setting=True)


def test_configure_rejects_unknown_provider():
    with pytest.raises(ValidationError):
        configure(model_provider="nope")


def test_configure_keeps_singleton_identity():
    before = config
    configure(api_port=9123)
    assert config_mod.config is before
    assert before.api_port == 9123


def test_configure_coerces_like_pydantic():
    configure(api_port="9124")  # lax mode: numeric strings coerce
    assert config.api_port == 9124


def test_failed_configure_changes_nothing():
    configure(api_port=9125)
    with pytest.raises(ValidationError):
        configure(api_port=9126, admin_port="bad")
    assert config.api_port == 9125


def test_frozen_blocks_direct_assignment():
    with pytest.raises(ValidationError):
        config.api_port = 1  # type: ignore[misc]  # pyright: ignore[reportAttributeAccessIssue]


def test_legacy_s3_enabled_maps_to_storage():
    configure(s3_enabled=True)
    assert config.storage_enabled is True
    assert config.storage_backend == "s3"


def test_derive_leaves_singleton_untouched():
    other = derive(config, api_port=9127)
    assert other.api_port == 9127
    assert config.api_port != 9127 or other is not config


def test_reset_restores_defaults():
    configure(api_port=9128, websearch_enabled=True)
    reset_config()
    assert config.api_port == Config().api_port
    assert config.websearch_enabled is False


def test_secret_fields_are_masked(monkeypatch, caplog):
    assert secret_fields()["api_auth_token"] == "API_AUTH_TOKEN"
    configure(api_auth_token="super-secret-value")
    lines: list[str] = []
    monkeypatch.setattr(config_mod, "log_info", lines.append)
    config.log_settings()
    joined = "\n".join(lines)
    assert "super-secret-value" not in joined
    assert "api_auth_token = <set, 18 chars, ...alue>" in joined


def test_load_secrets_fills_unset_from_home_env(tmp_path, monkeypatch):
    monkeypatch.delenv("SEANIME_TOKEN", raising=False)
    configure(seanime_token=None)
    (tmp_path / ".env").write_text("SEANIME_TOKEN=from-home\n", encoding="utf-8")
    config_mod.load_secrets(tmp_path)
    assert config.seanime_token == "from-home"
    monkeypatch.delenv("SEANIME_TOKEN", raising=False)


def test_load_secrets_never_overrides_a_set_value(tmp_path, monkeypatch):
    configure(seanime_token="from-code")
    (tmp_path / ".env").write_text("SEANIME_TOKEN=from-home\n", encoding="utf-8")
    config_mod.load_secrets(tmp_path)
    assert config.seanime_token == "from-code"
    monkeypatch.delenv("SEANIME_TOKEN", raising=False)
