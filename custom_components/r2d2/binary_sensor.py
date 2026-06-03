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
from .droid.constants import (
    BATTERY_STATE_CHARGING,
    BATTERY_STATE_CHARGED,
    BATTERY_STATE_LOW,
    BATTERY_STATE_CRITICAL,
)
from .entity import R2D2Entity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: R2D2Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        R2D2ConnectivitySensor(coordinator, entry),
        R2D2ChargingSensor(coordinator, entry),
        R2D2BatteryLowSensor(coordinator, entry),
    ])


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


class R2D2ChargingSensor(R2D2Entity, BinarySensorEntity):
    """On while the droid is on its charging base and charging.

    Driven by the droid's pushed battery-state notifications, so it updates as
    soon as the droid is docked/undocked rather than on the 30s poll.
    """

    _attr_translation_key = "charging"
    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING

    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_charging"

    @property
    def available(self) -> bool:
        return self.coordinator.battery_state is not None

    @property
    def is_on(self) -> bool | None:
        state = self.coordinator.battery_state
        if state is None:
            return None
        return state in (BATTERY_STATE_CHARGING, BATTERY_STATE_CHARGED)


class R2D2BatteryLowSensor(R2D2Entity, BinarySensorEntity):
    """On when the droid reports a low or critical battery."""

    _attr_translation_key = "battery_low"
    _attr_device_class = BinarySensorDeviceClass.BATTERY

    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_battery_low"

    @property
    def available(self) -> bool:
        return self.coordinator.battery_state is not None

    @property
    def is_on(self) -> bool | None:
        state = self.coordinator.battery_state
        if state is None:
            return None
        return state in (BATTERY_STATE_LOW, BATTERY_STATE_CRITICAL)
