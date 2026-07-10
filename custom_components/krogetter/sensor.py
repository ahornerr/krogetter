"""Sensor platform for Krogetter."""
from __future__ import annotations
import logging
from dataclasses import dataclass
from homeassistant.components.sensor import SensorEntity, SensorEntityDescription, SensorDeviceClass, SensorStateClass
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
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:currency-usd",
    ),
    KrogetterSensorDescription(
        key="effective_unit_price",
        name="Effective Unit Price",
        native_unit_of_measurement="$",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
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
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:piggy-bank",
    ),
    KrogetterSensorDescription(
        key="savings_percent",
        name="Savings Percent",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:percent",
    ),
]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up Krogetter sensors with dynamic add/remove."""
    coordinator: DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    # Track entities by UPC so we can remove them when items are removed
    entities_by_upc: dict[str, list[KrogetterSensor]] = {}

    def _sync_entities() -> None:
        """Create entities for new items, remove entities for deleted items."""
        current_upcs = {item["upc"] for item in coordinator.data or []}

        # Remove entities for items no longer tracked
        removed = set(entities_by_upc) - current_upcs
        for upc in removed:
            for entity in entities_by_upc.pop(upc):
                entity.async_remove()

        # Add entities for newly tracked items
        for item in coordinator.data or []:
            upc = item["upc"]
            if upc not in entities_by_upc:
                new_entities = [
                    KrogetterSensor(coordinator, desc, item) for desc in SENSORS
                ]
                entities_by_upc[upc] = new_entities
                async_add_entities(new_entities)

    # Initial population
    _sync_entities()

    # Single integration-level sensor for last refresh time
    async_add_entities([KrogetterLastRefreshSensor(coordinator, entry.entry_id)])

    # Listen for coordinator updates — creates/removes entities dynamically
    entry.async_on_unload(coordinator.async_add_listener(_sync_entities))


class KrogetterLastRefreshSensor(CoordinatorEntity, SensorEntity):
    """Sensor showing when the coordinator last refreshed data."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: DataUpdateCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"krogetter_{entry_id}_last_refresh"
        self._attr_name = "Krogetter Last Refresh"

    @property
    def native_value(self):
        return self.coordinator.last_update_success_time

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
            return latest.get("regular")
        elif key == "effective_unit_price":
            return latest.get("effective_unit_price")
        elif key == "offer":
            return latest.get("synthetic_description")
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
