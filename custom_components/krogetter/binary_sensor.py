"""Binary sensor platform for Krogetter."""
from __future__ import annotations
import logging
from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up Krogetter binary sensors with dynamic add/remove."""
    coordinator: DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    # Track entities by UPC so we can remove them when items are removed
    entities_by_upc: dict[str, KrogetterOnSaleSensor] = {}

    def _sync_entities() -> None:
        """Create entities for new items, remove entities for deleted items."""
        current_upcs = {item["upc"] for item in coordinator.data or []}

        # Remove entities for items no longer tracked
        removed = set(entities_by_upc) - current_upcs
        for upc in removed:
            entities_by_upc.pop(upc).async_remove()

        # Add entities for newly tracked items
        for item in coordinator.data or []:
            upc = item["upc"]
            if upc not in entities_by_upc:
                entity = KrogetterOnSaleSensor(coordinator, item)
                entities_by_upc[upc] = entity
                async_add_entities([entity])

    # Initial population
    _sync_entities()

    # Listen for coordinator updates — creates/removes entities dynamically
    entry.async_on_unload(coordinator.async_add_listener(_sync_entities))

class KrogetterOnSaleSensor(CoordinatorEntity, BinarySensorEntity):
    def __init__(self, coordinator, item: dict) -> None:
        super().__init__(coordinator)
        self._upc = item["upc"]
        self._attr_unique_id = f"krogetter_{self._upc}_on_sale"
        self._attr_name = f"{item['label']} On Sale"
        self._attr_device_info = DeviceInfo(
            identifiers={("krogetter", self._upc)},
            name=item["label"],
            manufacturer="Kroger",
            model=item.get("modality", ""),
        )

    def _get_item(self) -> dict | None:
        for item in self.coordinator.data or []:
            if item["upc"] == self._upc:
                return item
        return None

    @property
    def is_on(self) -> bool | None:
        item = self._get_item()
        if not item or not item.get("latest"):
            return None
        return item["latest"].get("is_on_sale", False)

    @property
    def extra_state_attributes(self):
        item = self._get_item()
        if not item or not item.get("latest"):
            return None
        latest = item["latest"]
        return {
            "available": latest.get("available", True),
            "inventory_level": latest.get("inventory_level"),
            "offer_description": latest.get("synthetic_description"),
            "savings": latest.get("savings"),
            "savings_percent": latest.get("savings_percent"),
            "checked_at": latest.get("checked_at"),
        }
