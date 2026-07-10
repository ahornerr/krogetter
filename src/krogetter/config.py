"""Configuration loading from environment variables and optional config file."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "krogetter"
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "krogetter" / "config.toml"


def _parse_bool(value: str) -> bool:
    """Parse a boolean from a string (true/false/1/0, case-insensitive)."""
    v = value.strip().lower()
    if v in ("true", "1", "yes", "on"):
        return True
    if v in ("false", "0", "no", "off"):
        return False
    raise ValueError(f"Cannot parse boolean from: {value!r}")


@dataclass
class Config:
    """Application configuration."""

    data_dir: Path
    log_level: str = "INFO"
    default_chain: str = "KINGSOOPERS"
    default_zip: str | None = None
    poll_interval: int = 3600  # seconds
    use_web_fetcher: bool = True

    @classmethod
    def from_env(cls) -> "Config":
        """Load config from environment variables + optional TOML config file.

        Environment variables (highest priority):
            KROGETTER_LOG_LEVEL (default: INFO)
            KROGETTER_DATA_DIR (default: ~/.local/share/krogetter)
            KROGETTER_DEFAULT_CHAIN (default: KINGSOOPERS)
            KROGETTER_DEFAULT_ZIP (default: None)
            KROGETTER_POLL_INTERVAL (default: 3600)
            KROGETTER_USE_WEB_FETCHER (default: true)

        Optional TOML config file at ~/.config/krogetter/config.toml can set
        any of these (env vars override file values).
        """
        # ------------------------------------------------------------------ #
        #  Defaults
        # ------------------------------------------------------------------ #
        data_dir: Path | None = None
        log_level = "INFO"
        default_chain = "KINGSOOPERS"
        default_zip: str | None = None
        poll_interval = 3600
        use_web_fetcher = True

        # ------------------------------------------------------------------ #
        #  Load from TOML config file (if it exists)
        # ------------------------------------------------------------------ #
        if DEFAULT_CONFIG_PATH.exists():
            try:
                raw = tomllib.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
            except (tomllib.TOMLDecodeError, OSError) as exc:
                raise ValueError(
                    f"Failed to parse config file {DEFAULT_CONFIG_PATH}: {exc}"
                ) from exc

            krogetter_section: dict = raw.get("krogetter", {})
            if isinstance(krogetter_section, dict):
                if "log_level" in krogetter_section:
                    log_level = str(krogetter_section["log_level"]).upper()
                if "data_dir" in krogetter_section:
                    data_dir = Path(krogetter_section["data_dir"]).expanduser()
                if "default_chain" in krogetter_section:
                    default_chain = str(krogetter_section["default_chain"]).upper()
                if "default_zip" in krogetter_section:
                    default_zip = str(krogetter_section["default_zip"])
                if "poll_interval" in krogetter_section:
                    poll_interval = int(krogetter_section["poll_interval"])
                if "use_web_fetcher" in krogetter_section:
                    raw_val = krogetter_section["use_web_fetcher"]
                    if isinstance(raw_val, bool):
                        use_web_fetcher = raw_val
                    else:
                        use_web_fetcher = _parse_bool(str(raw_val))

        # ------------------------------------------------------------------ #
        #  Override with environment variables (higher priority)
        # ------------------------------------------------------------------ #
        if os.environ.get("KROGETTER_LOG_LEVEL"):
            log_level = os.environ["KROGETTER_LOG_LEVEL"].upper()
        if os.environ.get("KROGETTER_DATA_DIR"):
            data_dir = Path(os.environ["KROGETTER_DATA_DIR"]).expanduser()
        if os.environ.get("KROGETTER_DEFAULT_CHAIN"):
            default_chain = os.environ["KROGETTER_DEFAULT_CHAIN"].upper()
        if os.environ.get("KROGETTER_DEFAULT_ZIP"):
            default_zip = os.environ["KROGETTER_DEFAULT_ZIP"]
        if os.environ.get("KROGETTER_POLL_INTERVAL"):
            poll_interval = int(os.environ["KROGETTER_POLL_INTERVAL"])
        if os.environ.get("KROGETTER_USE_WEB_FETCHER"):
            use_web_fetcher = _parse_bool(os.environ["KROGETTER_USE_WEB_FETCHER"])

        # ------------------------------------------------------------------ #
        #  Build and return
        # ------------------------------------------------------------------ #
        return cls(
            data_dir=data_dir or DEFAULT_DATA_DIR,
            log_level=log_level,
            default_chain=default_chain,
            default_zip=default_zip,
            poll_interval=poll_interval,
            use_web_fetcher=use_web_fetcher,
        )
