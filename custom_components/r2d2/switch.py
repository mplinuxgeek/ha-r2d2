"""Switch entities for the R2D2 integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import R2D2Coordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up R2D2 switch entities."""
    coordinator: R2D2Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([R2D2AllLightsSwitch(coordinator, entry)])


class R2D2AllLightsSwitch(CoordinatorEntity[R2D2Coordinator], SwitchEntity):
    """Toggle all droid lights on/off at full brightness."""

    _attr_has_entity_name = True
    _attr_translation_key = "all_lights"

    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        """Initialise the all-lights switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_all_lights"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=coordinator.droid_name,
            manufacturer="Sphero",
            model="R2-D2 / Q5",
        )
        self._is_on: bool = False

    @property
    def available(self) -> bool:
        """Return True if the droid is connected."""
        return self.coordinator.droid is not None and self.coordinator.droid.connected

    @property
    def is_on(self) -> bool:
        """Return True if lights are on."""
        return self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on all lights at full brightness."""
        droid = self.coordinator.droid
        await droid.set_front_led(255, 255, 255)
        await droid.set_back_led(255, 255, 255)
        await droid.set_holo_projector(255)
        await droid.set_logic_display(255)
        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off all lights."""
        droid = self.coordinator.droid
        await droid.set_front_led(0, 0, 0)
        await droid.set_back_led(0, 0, 0)
        await droid.set_holo_projector(0)
        await droid.set_logic_display(0)
        self._is_on = False
        self.async_write_ha_state()
