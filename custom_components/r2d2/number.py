"""Number entities for the R2D2 integration."""
from __future__ import annotations

from homeassistant.components.number import (
    NumberEntity,
    NumberMode,
    NumberEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import DEGREE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import R2D2Coordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up R2D2 number entities."""
    coordinator: R2D2Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DomeRotation(coordinator, entry)])


class DomeRotation(CoordinatorEntity[R2D2Coordinator], NumberEntity):
    """Dome rotation angle (-160 to 180 degrees)."""

    _attr_has_entity_name = True
    _attr_translation_key = "dome_rotation"
    _attr_native_min_value = -160.0
    _attr_native_max_value = 180.0
    _attr_native_step = 5.0
    _attr_native_unit_of_measurement = DEGREE
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        """Initialise dome rotation control."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_dome_rotation"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=coordinator.droid_name,
            manufacturer="Sphero",
            model="R2-D2 / Q5",
        )
        self._current_value: float = 0.0

    @property
    def available(self) -> bool:
        """Return True if the droid is connected."""
        return self.coordinator.droid is not None and self.coordinator.droid.connected

    @property
    def native_value(self) -> float:
        """Return the current dome angle."""
        return self._current_value

    async def async_set_native_value(self, value: float) -> None:
        """Rotate the dome to the given angle."""
        await self.coordinator.droid.rotate(value)
        self._current_value = value
        self.async_write_ha_state()
