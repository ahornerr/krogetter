"""Sensor platform for Krogetter."""
from __future__ import annotations
import logging
from dataclasses import dataclass
from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

@dataclass(frozen=True, kw_only=True)
class KrogetterSensorDescription(SensorEntityDescription):
    pass

SENSORS = [
    KrogetterSensorDescription(
        key="price",
        name="Price",
        native_unit_of_measurement="$",
        device_class="monetary",
        state_class="measurement",
        icon="mdi:currency-usd",
    ),
    KrogetterSensorDescription(
        key="effective_unit_price",
        name="Effective Unit Price",
        native_unit_of_measurement="$",
        device_class="monetary",
        state_class="measurement",
        icon="mdi:tag-multiple",
    ),
    KrogetterSensorDescription(
        key="offer",
        name="Offer",
        icon="mdi:tag",
    ),
    KrogetterSensorDescription(
        key="savings",
        name="Savings",
        native_unit_of_measurement="$",
        device_class="monetary",
        state_class="measurement",
        icon="mdi:piggy-bank",
    ),
    KrogetterSensorDescription(
        key="savings_percent",
        name="Savings Percent",
        native_unit_of_measurement="%",
        state_class="measurement",
        icon="mdi:percent",
    ),
]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up Krogetter sensors."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    entities = []
    for item in coordinator.data or []:
        upc = item["upc"]
        for description in SENSORS:
            entities.append(KrogetterSensor(coordinator, description, item))
    async_add_entities(entities)

class KrogetterSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, description, item: dict) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._upc = item["upc"]
        self._attr_unique_id = f"krogetter_{self._upc}_{description.key}"
        self._attr_name = f"{item['label']} {description.name}"
        self._attr_device_info = DeviceInfo(
            identifiers={("krogetter", self._upc)},
            name=item["label"],
            manufacturer="Kroger",
            model=item.get("modality", ""),
        )

    def _get_item(self) -> dict | None:
        """Find this item in coordinator data."""
        for item in self.coordinator.data or []:
            if item["upc"] == self._upc:
                return item
        return None

    @property
    def native_value(self):
        item = self._get_item()
        if not item or not item.get("latest"):
            return None
        latest = item["latest"]
        key = self.entity_description.key
        if key == "price":
            return latest["regular"]
        elif key == "effective_unit_price":
            return latest.get("effective_unit_price")
        elif key == "offer":
            return latest.get("synthetic_description") or "None"
        elif key == "savings":
            return latest.get("savings")
        elif key == "savings_percent":
            return latest.get("savings_percent")
        return None

    @property
    def extra_state_attributes(self):
        item = self._get_item()
        if not item or not item.get("latest"):
            return None
        latest = item["latest"]
        key = self.entity_description.key
        attrs = {"checked_at": latest.get("checked_at")}
        if key == "price":
            attrs["promo_price"] = latest.get("promo")
            attrs["current_price"] = latest.get("current_price")
        elif key == "effective_unit_price":
            attrs["offer_description"] = latest.get("synthetic_description")
        elif key == "savings":
            attrs["savings_percent"] = latest.get("savings_percent")
            attrs["regular_price"] = latest.get("regular")
            attrs["effective_unit_price"] = latest.get("effective_unit_price")
        elif key == "savings_percent":
            attrs["savings_amount"] = latest.get("savings")
            attrs["regular_price"] = latest.get("regular")
        return attrs
