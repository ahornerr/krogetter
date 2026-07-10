"""The Krogetter integration."""
from __future__ import annotations
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .api import KrogetterAPI
from .const import DOMAIN, CONF_API_URL, CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
from .coordinator import KrogetterCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor"]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Krogetter from a config entry."""
    api_url = entry.data[CONF_API_URL]
    poll_interval = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)

    session = async_get_clientsession(hass)
    api = KrogetterAPI(session, api_url)

    coordinator = KrogetterCoordinator(hass, api, poll_interval)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services
    await _async_register_services(hass, api, coordinator)

    return True


async def async_update_options(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Reload entry when options change (e.g. poll interval)."""
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok

async def _async_register_services(hass: HomeAssistant, api: KrogetterAPI, coordinator: KrogetterCoordinator) -> None:
    """Register HA services."""

    async def handle_add_item(call):
        url = call.data["url"]
        label = call.data.get("label")
        zip_code = call.data.get("zip_code")
        delivery = call.data.get("delivery", False)
        store_id = call.data.get("store_id")
        await api.add_item(url, label, zip_code, delivery, store_id)
        await coordinator.async_request_refresh()

    async def handle_remove_item(call):
        upc = call.data["upc"]
        await api.remove_item(upc)
        await coordinator.async_request_refresh()

    async def handle_check_now(call):
        upc = call.data.get("upc")
        if upc:
            await api.check_item(upc)
        else:
            await api.check_all()
        await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, "add_item", handle_add_item)
    hass.services.async_register(DOMAIN, "remove_item", handle_remove_item)
    hass.services.async_register(DOMAIN, "check_now", handle_check_now)
