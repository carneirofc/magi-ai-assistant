"""Tests for the `magi` CLI (magi/cli): dispatch, config commands, doctor, setup."""

import io
import os
from pathlib import Path

import pytest

from magi.cli import build_parser, main
from magi.cli import doctor as doctor_mod
from magi.cli.run import prepare, run_channels
from magi.cli.setup import run_setup
from magi.core.config import config, configure, derive
from magi.core.config_file import load_config_file


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    h = tmp_path / "home"
    monkeypatch.setenv("MAGI_HOME", str(h))
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)  # prepare() chdirs; monkeypatch restores it
    return h


def test_parser_rejects_unknown_channel():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "irc"])


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "COMMAND" in capsys.readouterr().out


def test_config_set_get_roundtrip(home, capsys):
    assert main(["config", "set", "api_port", "9400"]) == 0
    assert load_config_file(home / "config.yaml") == {"api_port": 9400}
    assert main(["config", "get", "api_port"]) == 0
    assert capsys.readouterr().out.strip().endswith("9400")


def test_config_set_invalid_exits_2(home, capsys):
    assert main(["config", "set", "api_port", "many"]) == 2
    assert "api_port" in capsys.readouterr().err


def test_config_show_masks_secrets(home, capsys, monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "very-secret-token")
    configure(api_auth_token=None)
    assert main(["config", "show"]) == 0
    out = capsys.readouterr().out
    assert "very-secret-token" not in out
    assert "api_auth_token = <set" in out


def test_prepare_uses_home_config_and_chdirs(home):
    home.mkdir()
    (home / "config.yaml").write_text("api_port: 9401\n", encoding="utf-8")
    assert prepare() == (home / "config.yaml").resolve()
    assert config.api_port == 9401
    assert Path.cwd() == home.resolve()


def test_prepare_overlays_later_files(home, tmp_path):
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    a.write_text("api_port: 1\napi_host: 0.0.0.0\n", encoding="utf-8")
    b.write_text("api_port: 2\n", encoding="utf-8")
    prepare([a, b])
    assert (config.api_port, config.api_host) == (2, "0.0.0.0")
    assert Path.cwd() == tmp_path.resolve()


def test_run_channels_defaults_to_config(monkeypatch):
    called: list[list[str]] = []
    monkeypatch.setattr("magi.channels.registry.serve", lambda names: called.append(list(names)))
    configure(channels={"enabled": ["api", "telegram"]})
    run_channels([])
    assert called == [["api", "telegram"]]


def test_run_channels_desktop_runs_alone(monkeypatch):
    called: list[bool] = []
    monkeypatch.setattr("magi.cli.run.run_desktop", lambda frameless: called.append(frameless))
    run_channels(["desktop"])
    assert called == [True]


def test_doctor_reports_missing_discord_token(monkeypatch):
    monkeypatch.setattr(doctor_mod, "http_status", lambda url, headers=None: 200)
    cfg = derive(config, channels={"enabled": ["discord"]}, DISCORD_BOT_TOKEN=None)
    results = doctor_mod.run_checks(cfg)
    by_name = {r.name: r for r in results}
    assert by_name["discord token"].status == "fail"
    assert by_name["model (litellm)"].status == "ok"
    out = io.StringIO()
    assert doctor_mod.report(results, out) == 1


def test_doctor_model_unreachable_fails(monkeypatch):
    monkeypatch.setattr(doctor_mod, "http_status", lambda url, headers=None: "ConnectError")
    cfg = derive(config, model_provider="llamacpp")
    (result,) = doctor_mod.check_model(cfg)
    assert result.status == "fail" and "ConnectError" in result.detail


def test_doctor_flags_missing_extra(monkeypatch):
    monkeypatch.setattr(doctor_mod.importlib.util, "find_spec", lambda name: None)
    cfg = derive(config, websearch_enabled=True)
    (result,) = doctor_mod.check_extras(cfg)
    assert result.status == "fail" and "websearch" in result.detail


def test_doctor_crashing_check_is_a_failure():
    def boom(_cfg):
        raise RuntimeError("nope")

    (result,) = doctor_mod.run_checks(config, [boom])
    assert result.status == "fail"


class _Scripted:
    def __init__(self, answers: dict[str, str], secrets: dict[str, str]) -> None:
        self.answers = answers
        self.secrets = secrets

    def ask(self, question: str, default: str) -> str:
        for key, value in self.answers.items():
            if question.startswith(key):
                return value
        return default

    def secret(self, question: str) -> str:
        return self.secrets.get(question, "")


def test_setup_writes_config_and_private_env(tmp_path):
    prompter = _Scripted(
        {"Model backend": "openai", "Model id": "gpt-x", "Channels": "api, discord, bogus"},
        {"OPENAI_API_KEY": "sk-1", "DISCORD_BOT_TOKEN": "tok"},
    )
    result = run_setup(tmp_path, prompter)
    data = load_config_file(result.config_path)
    assert data["model_provider"] == "openai"
    assert data["openai_base_url"] == "https://api.openai.com/v1"
    assert data["lead_model_id"] == "gpt-x"
    assert data["channels"] == {"enabled": ["api", "discord"]}
    env = result.env_path.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=sk-1" in env and "DISCORD_BOT_TOKEN=tok" in env
    assert "API_AUTH_TOKEN=" in env
    assert os.stat(result.env_path).st_mode & 0o777 == 0o600


def test_setup_rerun_keeps_previous_answers(tmp_path):
    run_setup(tmp_path, _Scripted({"Model backend": "ollama"}, {}))
    result = run_setup(tmp_path, _Scripted({}, {}))
    assert result.config["model_provider"] == "ollama"


def test_doctor_telegram_needs_token_and_warns_on_empty_allowlist(monkeypatch):
    monkeypatch.setattr(doctor_mod, "http_status", lambda url, headers=None: 200)
    cfg = derive(
        config,
        channels={"enabled": ["telegram"]},
        telegram_bot_token=None,
        telegram_allowed_users=[],
    )
    by_name = {r.name: r for r in doctor_mod.check_channels(cfg)}
    assert by_name["telegram token"].status == "fail"
    assert by_name["telegram access"].status == "warn"


def test_doctor_warns_when_nothing_can_learn():
    off = doctor_mod.check_memory_learning(derive(config, memory_curation=False))
    assert [(r.name, r.status) for r in off] == [("memory learning", "warn")]

    gated = doctor_mod.check_memory_learning(
        derive(config, memory_curation=True, persona_learning="propose", evolution_enabled=False)
    )
    assert [r.status for r in gated] == ["ok", "warn"]
    assert "evolution_enabled" in gated[1].detail
