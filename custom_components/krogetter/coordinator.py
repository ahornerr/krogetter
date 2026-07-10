"""DataUpdateCoordinator for Krogetter."""
from __future__ import annotations
import logging
from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.core import HomeAssistant
from .api import KrogetterAPI, KrogetterAPIError
from .const import DOMAIN, CONF_API_URL, CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL

_LOGGER = logging.getLogger(__name__)

class KrogetterCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, api: KrogetterAPI, poll_interval: int) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=poll_interval),
        )
        self._api = api

    async def _async_update_data(self) -> list[dict]:
        try:
            return await self._api.get_items()
        except KrogetterAPIError as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected error: {err}") from err
