"""Constants for the Krogetter integration."""
from __future__ import annotations

from homeassistant.util import dt as dt_util

DOMAIN = "krogetter"

CONF_API_URL = "api_url"
CONF_POLL_INTERVAL = "poll_interval"

DEFAULT_POLL_INTERVAL = 300  # 5 minutes — just reading cached data from server


def date_only(value: str | None) -> str | None:
    """Localize an offer datetime string for HA display.

    The Kroger API returns offer dates as naive datetime strings (e.g.,
    "2026-07-08T00:00:00") representing local store time.  HA's frontend
    interprets bare date strings (YYYY-MM-DD) as midnight UTC and converts
    to the user's timezone, shifting the displayed date back by one day.

    To prevent this, we return a full ISO datetime string with the local
    timezone offset so HA displays the correct date.
    """
    if not value:
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        # Not a parseable datetime — fall back to truncated value
        return value[:10]
    if parsed.tzinfo is None:
        # Naive datetime — assume local store time, attach local tz
        parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
    else:
        # Timezone-aware (e.g. UTC "Z" suffix) — convert to local
        parsed = dt_util.as_local(parsed)
    return parsed.isoformat()
