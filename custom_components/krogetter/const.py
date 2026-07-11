"""Constants for the Krogetter integration."""
from __future__ import annotations

DOMAIN = "krogetter"

CONF_API_URL = "api_url"
CONF_POLL_INTERVAL = "poll_interval"

DEFAULT_POLL_INTERVAL = 300  # 5 minutes — just reading cached data from server


def date_only(value: str | None) -> str | None:
    """Truncate an ISO datetime string to date-only (YYYY-MM-DD)."""
    if not value:
        return None
    return value[:10]
