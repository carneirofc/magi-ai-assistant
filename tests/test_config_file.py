"""Tests for the typed config file (core/config_file)."""

from pathlib import Path

import pytest

from magi.core.config import config, configure
from magi.core.config_file import (
    ConfigFileError,
    data_root,
    find_config_file,
    get_dotted,
    load_config,
    load_config_file,
    magi_home,
    parse_value,
    set_dotted,
    write_config_value,
    write_env_file,
)


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    h = tmp_path / "home"
    monkeypatch.setenv("MAGI_HOME", str(h))
    return h


def test_magi_home_from_env(home):
    assert magi_home() == home


def test_find_prefers_local_magi_yaml(tmp_path, home):
    home.mkdir()
    (home / "config.yaml").write_text("api_port: 1\n", encoding="utf-8")
    cwd = tmp_path / "proj"
    cwd.mkdir()
    assert find_config_file(cwd) == home / "config.yaml"
    (cwd / "magi.yaml").write_text("api_port: 2\n", encoding="utf-8")
    assert find_config_file(cwd) == cwd / "magi.yaml"


def test_find_none_when_absent(tmp_path, home):
    assert find_config_file(tmp_path) is None
    assert data_root(None) == home


def test_load_applies_file_over_defaults(tmp_path, home):
    path = tmp_path / "magi.yaml"
    path.write_text("api_port: 9200\nchannels:\n  enabled: [api, discord]\n", encoding="utf-8")
    assert load_config(path, home=home) == path
    assert config.api_port == 9200
    assert config.channels.enabled == ["api", "discord"]


def test_code_configure_wins_over_file(tmp_path, home):
    path = tmp_path / "magi.yaml"
    path.write_text("api_port: 9201\n", encoding="utf-8")
    load_config(path, home=home)
    configure(api_port=9202)
    assert config.api_port == 9202


def test_bad_value_names_the_key(tmp_path):
    path = tmp_path / "magi.yaml"
    path.write_text("api_port: lots\n", encoding="utf-8")
    with pytest.raises(ConfigFileError, match="api_port"):
        load_config_file(path)


def test_unknown_key_rejected(tmp_path):
    path = tmp_path / "magi.yaml"
    path.write_text("api_prot: 1\n", encoding="utf-8")
    with pytest.raises(ConfigFileError, match="api_prot"):
        load_config_file(path)


def test_non_mapping_rejected(tmp_path):
    path = tmp_path / "magi.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ConfigFileError, match="mapping"):
        load_config_file(path)


def test_empty_file_is_defaults(tmp_path):
    path = tmp_path / "magi.yaml"
    path.write_text("", encoding="utf-8")
    assert load_config_file(path) == {}


def test_parse_value_yaml_scalars():
    assert parse_value("8000") == 8000
    assert parse_value("true") is True
    assert parse_value("[api, discord]") == ["api", "discord"]
    assert parse_value("hello world") == "hello world"
    assert parse_value("null") is None


def test_dotted_set_and_get():
    assert set_dotted({"a": 1}, "channels.enabled", ["api"]) == {
        "a": 1,
        "channels": {"enabled": ["api"]},
    }
    assert get_dotted(config, "channels.enabled") == config.channels.enabled
    with pytest.raises(KeyError):
        get_dotted(config, "channels.nope")


def test_write_config_value_validates_and_roundtrips(tmp_path):
    path = tmp_path / "sub" / "config.yaml"
    write_config_value(path, "api_port", 9300)
    write_config_value(path, "channels.enabled", ["discord"])
    assert load_config_file(path) == {"api_port": 9300, "channels": {"enabled": ["discord"]}}
    with pytest.raises(ConfigFileError):
        write_config_value(path, "api_port", "nope")
    assert load_config_file(path)["api_port"] == 9300  # failed write left the file alone


def test_write_env_file_merges_and_is_private(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# comment\nKEEP=1\nAPI_AUTH_TOKEN=old\n", encoding="utf-8")
    write_env_file(path, {"API_AUTH_TOKEN": "new", "DISCORD_BOT_TOKEN": "d"})
    assert path.read_text(encoding="utf-8").splitlines() == [
        "# comment",
        "KEEP=1",
        "API_AUTH_TOKEN=new",
        "DISCORD_BOT_TOKEN=d",
    ]
    assert path.stat().st_mode & 0o777 == 0o600


def test_repo_magi_yaml_is_valid():
    root = Path(__file__).resolve().parent.parent
    load_config_file(root / "magi.yaml")
    load_config_file(root / "docker" / "magi.docker.yaml")
