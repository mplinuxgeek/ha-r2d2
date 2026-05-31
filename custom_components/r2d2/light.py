"""Light entities for the R2D2 integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
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
    """Set up R2D2 light entities."""
    coordinator: R2D2Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            R2D2FrontLED(coordinator, entry),
            R2D2BackLED(coordinator, entry),
            R2D2HoloProjector(coordinator, entry),
            R2D2LogicDisplay(coordinator, entry),
        ]
    )


class R2D2RGBLight(CoordinatorEntity[R2D2Coordinator], LightEntity):
    """Base class for R2D2 RGB LED entities."""

    _attr_has_entity_name = True
    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}

    def __init__(
        self,
        coordinator: R2D2Coordinator,
        entry: ConfigEntry,
        key: str,
        translation_key: str,
    ) -> None:
        """Initialise the RGB light."""
        super().__init__(coordinator)
        self._key = key
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=coordinator.droid_name,
            manufacturer="Sphero",
            model="R2-D2 / Q5",
        )
        self._is_on: bool = False
        self._rgb_color: tuple[int, int, int] = (255, 255, 255)
        self._brightness: int = 255

    @property
    def available(self) -> bool:
        """Return True if the droid is connected."""
        return self.coordinator.droid is not None and self.coordinator.droid.connected

    @property
    def is_on(self) -> bool:
        """Return True if the light is on."""
        return self._is_on

    @property
    def brightness(self) -> int:
        """Return the current brightness (0-255)."""
        return self._brightness

    @property
    def rgb_color(self) -> tuple[int, int, int]:
        """Return the current RGB colour."""
        return self._rgb_color

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        if ATTR_RGB_COLOR in kwargs:
            self._rgb_color = kwargs[ATTR_RGB_COLOR]
        if ATTR_BRIGHTNESS in kwargs:
            self._brightness = kwargs[ATTR_BRIGHTNESS]
        r, g, b = self._rgb_color
        factor = self._brightness / 255
        await self._send_color(int(r * factor), int(g * factor), int(b * factor))
        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self._send_color(0, 0, 0)
        self._is_on = False
        self.async_write_ha_state()

    async def _send_color(self, r: int, g: int, b: int) -> None:
        """Send colour to the droid — subclasses override."""
        raise NotImplementedError


class R2D2FrontLED(R2D2RGBLight):
    """Front LED (RGB) of the R2D2 droid."""

    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "front_led", "front_led")

    async def _send_color(self, r: int, g: int, b: int) -> None:
        await self.coordinator.droid.set_front_led(r, g, b)


class R2D2BackLED(R2D2RGBLight):
    """Back LED (RGB) of the R2D2 droid."""

    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "back_led", "back_led")

    async def _send_color(self, r: int, g: int, b: int) -> None:
        await self.coordinator.droid.set_back_led(r, g, b)


class R2D2BrightnessLight(CoordinatorEntity[R2D2Coordinator], LightEntity):
    """Base class for R2D2 brightness-only LED entities."""

    _attr_has_entity_name = True
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(
        self,
        coordinator: R2D2Coordinator,
        entry: ConfigEntry,
        key: str,
        translation_key: str,
    ) -> None:
        """Initialise the brightness light."""
        super().__init__(coordinator)
        self._key = key
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=coordinator.droid_name,
            manufacturer="Sphero",
            model="R2-D2 / Q5",
        )
        self._is_on: bool = False
        self._brightness: int = 255

    @property
    def available(self) -> bool:
        """Return True if the droid is connected."""
        return self.coordinator.droid is not None and self.coordinator.droid.connected

    @property
    def is_on(self) -> bool:
        """Return True if the light is on."""
        return self._is_on

    @property
    def brightness(self) -> int:
        """Return the current brightness (0-255)."""
        return self._brightness

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        if ATTR_BRIGHTNESS in kwargs:
            self._brightness = kwargs[ATTR_BRIGHTNESS]
        await self._send_brightness(self._brightness)
        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self._send_brightness(0)
        self._is_on = False
        self.async_write_ha_state()

    async def _send_brightness(self, brightness: int) -> None:
        """Send brightness to the droid — subclasses override."""
        raise NotImplementedError


class R2D2HoloProjector(R2D2BrightnessLight):
    """Dome holo-projector brightness control."""

    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "holo_projector", "holo_projector")

    async def _send_brightness(self, brightness: int) -> None:
        await self.coordinator.droid.set_holo_projector(brightness)


class R2D2LogicDisplay(R2D2BrightnessLight):
    """Logic display brightness control."""

    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "logic_display", "logic_display")

    async def _send_brightness(self, brightness: int) -> None:
        await self.coordinator.droid.set_logic_display(brightness)
