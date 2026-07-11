"""The Krogetter integration."""
from __future__ import annotations
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry
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

    # Register services (only once per hass instance)
    if not hass.data.get(DOMAIN, {}).get("services_registered"):
        await _async_register_services(hass, api, coordinator)
        hass.data.setdefault(DOMAIN, {})["services_registered"] = True

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
        # Remove services when the last entry unloads
        if not hass.data[DOMAIN]:
            for service_name in ("add_item", "remove_item", "check_now"):
                hass.services.async_remove(DOMAIN, service_name)
    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Remove a config entry device — also removes the UPC from tracking."""
    upc = next(
        (ident[1] for ident in device_entry.identifiers if ident[0] == DOMAIN),
        None,
    )
    if upc is None:
        _LOGGER.warning("Device %s has no krogetter UPC identifier", device_entry.id)
        return False

    # Remove the item from the API server so it doesn't reappear on next refresh
    api: KrogetterAPI | None = (
        hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("api")
    )
    if api:
        try:
            await api.remove_item(upc)
        except Exception:
            _LOGGER.warning("Failed to remove item %s from API server", upc)

    # Request a coordinator refresh so entities are cleaned up
    coordinator = (
        hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("coordinator")
    )
    if coordinator:
        await coordinator.async_request_refresh()

    return True


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
