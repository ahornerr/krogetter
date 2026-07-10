"""Tests for the config module."""

from pathlib import Path
from unittest.mock import patch

import pytest

from krogetter.config import Config, _parse_bool


class TestParseBool:
    """Tests for the _parse_bool helper."""

    def test_true_values(self):
        assert _parse_bool("true") is True
        assert _parse_bool("TRUE") is True
        assert _parse_bool("1") is True
        assert _parse_bool("yes") is True
        assert _parse_bool("on") is True

    def test_false_values(self):
        assert _parse_bool("false") is False
        assert _parse_bool("FALSE") is False
        assert _parse_bool("0") is False
        assert _parse_bool("no") is False
        assert _parse_bool("off") is False

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Cannot parse boolean"):
            _parse_bool("maybe")


class TestConfigFromEnv:
    """Tests for Config.from_env()."""

    def test_minimal_env(self, monkeypatch):
        """Minimal env (no vars set) should produce a valid config with defaults."""
        monkeypatch.delenv("KROGETTER_LOG_LEVEL", raising=False)
        monkeypatch.delenv("KROGETTER_DATA_DIR", raising=False)
        monkeypatch.delenv("KROGETTER_DEFAULT_CHAIN", raising=False)
        monkeypatch.delenv("KROGETTER_DEFAULT_ZIP", raising=False)
        monkeypatch.delenv("KROGETTER_POLL_INTERVAL", raising=False)
        monkeypatch.delenv("KROGETTER_USE_WEB_FETCHER", raising=False)

        config = Config.from_env()
        assert config.log_level == "INFO"
        assert config.default_chain == "KINGSOOPERS"
        assert config.default_zip is None
        assert config.poll_interval == 3600
        assert config.use_web_fetcher is True
        assert config.data_dir is not None  # default path

    def test_all_env_override(self, monkeypatch):
        """All env vars override defaults."""
        monkeypatch.setenv("KROGETTER_LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("KROGETTER_DATA_DIR", "/tmp/krogetter-data")
        monkeypatch.setenv("KROGETTER_DEFAULT_CHAIN", "FRED MEYER")
        monkeypatch.setenv("KROGETTER_DEFAULT_ZIP", "90210")
        monkeypatch.setenv("KROGETTER_POLL_INTERVAL", "120")
        monkeypatch.setenv("KROGETTER_USE_WEB_FETCHER", "false")

        config = Config.from_env()
        assert config.log_level == "DEBUG"
        assert config.data_dir == Path("/tmp/krogetter-data")
        assert config.default_chain == "FRED MEYER"
        assert config.default_zip == "90210"
        assert config.poll_interval == 120
        assert config.use_web_fetcher is False

    def test_toml_file_config(self, tmp_path, monkeypatch):
        """TOML config file should be loaded when env vars are not set."""
        monkeypatch.delenv("KROGETTER_LOG_LEVEL", raising=False)
        monkeypatch.delenv("KROGETTER_DATA_DIR", raising=False)
        monkeypatch.delenv("KROGETTER_DEFAULT_CHAIN", raising=False)
        monkeypatch.delenv("KROGETTER_DEFAULT_ZIP", raising=False)
        monkeypatch.delenv("KROGETTER_POLL_INTERVAL", raising=False)
        monkeypatch.delenv("KROGETTER_USE_WEB_FETCHER", raising=False)

        toml_content = """\
[krogetter]
log_level = "WARNING"
data_dir = "/tmp/toml-data"
default_chain = "QFC"
default_zip = "98101"
poll_interval = 600
use_web_fetcher = false
"""
        config_path = tmp_path / "config.toml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(toml_content, encoding="utf-8")

        with patch("krogetter.config.DEFAULT_CONFIG_PATH", config_path):
            config = Config.from_env()

        assert config.log_level == "WARNING"
        assert config.data_dir == Path("/tmp/toml-data")
        assert config.default_chain == "QFC"
        assert config.default_zip == "98101"
        assert config.poll_interval == 600
        assert config.use_web_fetcher is False

    def test_env_override_toml(self, tmp_path, monkeypatch):
        """Environment variables should override TOML values."""
        monkeypatch.setenv("KROGETTER_LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("KROGETTER_DEFAULT_CHAIN", "FRED MEYER")

        toml_content = """\
[krogetter]
log_level = "WARNING"
default_chain = "QFC"
"""
        config_path = tmp_path / "config.toml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(toml_content, encoding="utf-8")

        with patch("krogetter.config.DEFAULT_CONFIG_PATH", config_path):
            config = Config.from_env()

        assert config.log_level == "DEBUG"
        assert config.default_chain == "FRED MEYER"

    def test_toml_boolean_true(self, tmp_path, monkeypatch):
        """TOML boolean true should work natively."""
        monkeypatch.delenv("KROGETTER_USE_WEB_FETCHER", raising=False)

        toml_content = """\
[krogetter]
use_web_fetcher = true
"""
        config_path = tmp_path / "config.toml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(toml_content, encoding="utf-8")

        with patch("krogetter.config.DEFAULT_CONFIG_PATH", config_path):
            config = Config.from_env()

        assert config.use_web_fetcher is True

    def test_toml_boolean_string_false(self, tmp_path, monkeypatch):
        """TOML boolean as string '0' should be parsed."""
        monkeypatch.delenv("KROGETTER_USE_WEB_FETCHER", raising=False)

        toml_content = """\
[krogetter]
use_web_fetcher = "0"
"""
        config_path = tmp_path / "config.toml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(toml_content, encoding="utf-8")

        with patch("krogetter.config.DEFAULT_CONFIG_PATH", config_path):
            config = Config.from_env()

        assert config.use_web_fetcher is False

    def test_invalid_toml_raises(self, tmp_path, monkeypatch):
        """Malformed TOML should raise ValueError."""
        config_path = tmp_path / "config.toml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("this is not valid toml {{{", encoding="utf-8")

        with patch("krogetter.config.DEFAULT_CONFIG_PATH", config_path):
            with pytest.raises(ValueError, match="Failed to parse config"):
                Config.from_env()

    def test_no_toml_file_no_env_defaults(self, monkeypatch):
        """Without TOML file, defaults are used."""
        monkeypatch.delenv("KROGETTER_LOG_LEVEL", raising=False)
        monkeypatch.delenv("KROGETTER_DATA_DIR", raising=False)

        # Patch DEFAULT_CONFIG_PATH to a non-existent path
        with patch(
            "krogetter.config.DEFAULT_CONFIG_PATH",
            Path("/nonexistent/config.toml"),
        ):
            config = Config.from_env()

        assert config.log_level == "INFO"
        assert config.default_chain == "KINGSOOPERS"

    def test_use_web_fetcher_default(self, monkeypatch):
        """use_web_fetcher defaults to True."""
        monkeypatch.delenv("KROGETTER_USE_WEB_FETCHER", raising=False)
        config = Config.from_env()
        assert config.use_web_fetcher is True

    def test_use_web_fetcher_env_override(self, monkeypatch):
        """KROGETTER_USE_WEB_FETCHER env var should override default."""
        monkeypatch.setenv("KROGETTER_USE_WEB_FETCHER", "false")
        config = Config.from_env()
        assert config.use_web_fetcher is False
