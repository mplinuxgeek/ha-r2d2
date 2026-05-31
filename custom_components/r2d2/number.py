"""Number entities for the R2D2 integration."""
from __future__ import annotations

import asyncio

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import DEGREE, PERCENTAGE, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import R2D2Coordinator

_STEP_DEGREES = 5.0  # degrees per interpolation step


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: R2D2Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        DomeRotation(coordinator, entry),
        DomeSpeed(coordinator, entry),
        VolumeControl(coordinator, entry),
    ])


class DomeRotation(CoordinatorEntity[R2D2Coordinator], NumberEntity, RestoreEntity):
    """Dome rotation angle (−160 to +180 °).

    When dome_speed < 10 the move is interpolated in _STEP_DEGREES increments
    so the dome pans visibly rather than snapping to the target.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "dome_rotation"
    _attr_native_min_value = -160.0
    _attr_native_max_value = 180.0
    _attr_native_step = 5.0
    _attr_native_unit_of_measurement = DEGREE
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_dome_rotation"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=coordinator.droid_name,
            manufacturer="Sphero",
            model="R2-D2 / Q5",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            try:
                self.coordinator.dome_angle = float(last_state.state)
            except ValueError:
                pass

    @property
    def available(self) -> bool:
        return self.coordinator.droid is not None and self.coordinator.droid.connected

    @property
    def native_value(self) -> float:
        return self.coordinator.dome_angle

    async def async_set_native_value(self, value: float) -> None:
        speed = self.coordinator.dome_speed
        current = self.coordinator.dome_angle
        delta = value - current

        if speed >= 10 or abs(delta) <= _STEP_DEGREES:
            await self.coordinator.droid.rotate(value)
            self.coordinator.dome_angle = value
        else:
            # Interpolate: step through intermediate angles at a rate
            # governed by speed (1 = slowest, 9 = fastest slew).
            # delay_per_step at speed 1 ≈ 0.135 s → 90° ≈ 2.4 s total.
            delay = (10 - speed) * 0.015
            steps = int(abs(delta) / _STEP_DEGREES)
            direction = 1.0 if delta > 0 else -1.0
            for i in range(1, steps + 1):
                intermediate = current + direction * _STEP_DEGREES * i
                await self.coordinator.droid.rotate(intermediate)
                self.coordinator.dome_angle = intermediate
                self.coordinator.async_update_listeners()
                await asyncio.sleep(delay)
            # Final command to land exactly on the target
            await self.coordinator.droid.rotate(value)
            self.coordinator.dome_angle = value

        self.coordinator.async_update_listeners()


class DomeSpeed(CoordinatorEntity[R2D2Coordinator], NumberEntity, RestoreEntity):
    """Dome rotation speed (1 = slow slew, 10 = instant snap)."""

    _attr_has_entity_name = True
    _attr_translation_key = "dome_speed"
    _attr_native_min_value = 1.0
    _attr_native_max_value = 10.0
    _attr_native_step = 1.0
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_dome_speed"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=coordinator.droid_name,
            manufacturer="Sphero",
            model="R2-D2 / Q5",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            try:
                self.coordinator.dome_speed = int(float(last_state.state))
            except ValueError:
                pass

    @property
    def available(self) -> bool:
        return True  # speed preference is meaningful even when disconnected

    @property
    def native_value(self) -> float:
        return float(self.coordinator.dome_speed)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.dome_speed = int(value)
        self.async_write_ha_state()


class VolumeControl(CoordinatorEntity[R2D2Coordinator], NumberEntity):
    """Audio volume (0–100 %)."""

    _attr_has_entity_name = True
    _attr_translation_key = "volume"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1.0
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_volume"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=coordinator.droid_name,
            manufacturer="Sphero",
            model="R2-D2 / Q5",
        )
        self._current_value: float = 100.0

    @property
    def available(self) -> bool:
        return self.coordinator.droid is not None and self.coordinator.droid.connected

    @property
    def native_value(self) -> float:
        return self._current_value

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.droid.set_volume(int(value * 255 / 100))
        self._current_value = value
        self.async_write_ha_state()
