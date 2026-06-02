"""Binary sensor entities for the R2D2 integration."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import R2D2Coordinator
from .entity import R2D2Entity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: R2D2Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([R2D2ConnectivitySensor(coordinator, entry)])


class R2D2ConnectivitySensor(R2D2Entity, BinarySensorEntity):
    """Reports whether the droid is currently connected."""

    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_connected"

    @property
    def is_on(self) -> bool:
        return self.coordinator.is_connected
