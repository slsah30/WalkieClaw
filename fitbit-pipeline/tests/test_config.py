import os
from pathlib import Path

import pytest

from fitbit_pipeline.config import BASE_SCOPES, Config, ConfigError, load_config


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def test_missing_file_falls_back_to_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = load_config()
    assert config.sync.window_days == 3
    assert config.server.host == "127.0.0.1"
    assert config.source_path is None


def test_an_explicitly_named_missing_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_values_are_read_and_typed(tmp_path):
    path = write(
        tmp_path,
        """
        [sync]
        window_days = 7
        prefer_reconciled = false
        skip_data_types = ["exercise"]

        [server]
        port = 9000
        expose_lan = true
        """,
    )
    config = load_config(path)
    assert config.sync.window_days == 7
    assert config.sync.prefer_reconciled is False
    assert config.sync.skip_data_types == ["exercise"]
    assert config.server.port == 9000
    assert config.server.bind_host() == "0.0.0.0"


def test_relative_paths_resolve_against_the_config_file(tmp_path):
    path = write(tmp_path, '[database]\npath = "./data/x.sqlite3"\n')
    config = load_config(path)
    assert config.database.path == (tmp_path / "data" / "x.sqlite3").resolve()


def test_environment_overrides_the_file(tmp_path, monkeypatch):
    path = write(tmp_path, "[sync]\nwindow_days = 7\n")
    monkeypatch.setenv("FITBIT_SYNC_SYNC_WINDOW_DAYS", "14")
    monkeypatch.setenv("FITBIT_SYNC_SERVER_EXPOSE_LAN", "true")
    config = load_config(path)
    assert config.sync.window_days == 14
    assert config.server.expose_lan is True


def test_unknown_keys_and_sections_are_rejected(tmp_path):
    with pytest.raises(ConfigError, match="unknown section"):
        load_config(write(tmp_path, "[nope]\nx = 1\n"))
    with pytest.raises(ConfigError, match="unknown key"):
        load_config(write(tmp_path, "[sync]\nnope = 1\n"))


def test_invalid_values_are_rejected_with_a_useful_message(tmp_path):
    with pytest.raises(ConfigError, match="window_days"):
        load_config(write(tmp_path, "[sync]\nwindow_days = 0\n"))
    with pytest.raises(ConfigError, match="data_source_family"):
        load_config(write(tmp_path, '[sync]\ndata_source_family = "whatever"\n'))


def test_scopes_are_read_only_and_minimal_by_default():
    scopes = Config().auth.scopes()
    assert scopes == list(BASE_SCOPES)
    assert not any("writeonly" in scope for scope in scopes)
    # The three data scopes FR-2 needs, and nothing wider.
    assert any("activity_and_fitness.readonly" in scope for scope in scopes)
    assert any("sleep.readonly" in scope for scope in scopes)
    assert any("health_metrics_and_measurements.readonly" in scope for scope in scopes)
    assert not any("ecg" in scope for scope in scopes)


def test_optional_scope_aliases_expand(tmp_path):
    config = Config()
    config.auth.extra_scopes = ["ecg", "irn"]
    scopes = config.auth.scopes()
    assert "https://www.googleapis.com/auth/googlehealth.ecg.readonly" in scopes
    assert "https://www.googleapis.com/auth/googlehealth.irn.readonly" in scopes


def test_an_unknown_scope_alias_names_the_valid_ones():
    config = Config()
    config.auth.extra_scopes = ["telepathy"]
    with pytest.raises(ConfigError, match="ecg"):
        config.auth.scopes()


def test_base_url_defaults_to_the_real_api(tmp_path):
    from fitbit_pipeline.api import BASE_URL

    assert Config().sync.base_url == ""
    config = load_config(write(tmp_path, '[sync]\nbase_url = "http://localhost:9/v4"\n'))
    assert config.sync.base_url == "http://localhost:9/v4"
    assert BASE_URL == "https://health.googleapis.com/v4"
