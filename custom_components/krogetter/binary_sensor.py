"""Binary sensor platform for Krogetter."""
from __future__ import annotations
import logging
from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    entities = []
    for item in coordinator.data or []:
        entities.append(KrogetterOnSaleSensor(coordinator, item))
    async_add_entities(entities)

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
