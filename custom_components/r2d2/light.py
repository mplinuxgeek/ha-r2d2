"""Light entities for the R2D2 integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import R2D2Coordinator

_LOGGER = logging.getLogger(__name__)

# Named colour presets available in the light card effect dropdown.
# Values are (r, g, b) at full brightness.
COLOUR_PRESETS: dict[str, tuple[int, int, int]] = {
    "White":   (255, 255, 255),
    "Red":     (255,   0,   0),
    "Orange":  (255, 128,   0),
    "Yellow":  (255, 220,   0),
    "Green":   (  0, 255,   0),
    "Cyan":    (  0, 255, 255),
    "Blue":    (  0,   0, 255),
    "Purple":  (128,   0, 255),
    "Magenta": (255,   0, 255),
    "Pink":    (255,   0, 128),
}


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


class R2D2RGBLight(CoordinatorEntity[R2D2Coordinator], LightEntity, RestoreEntity):
    """Base class for R2D2 RGB LED entities.

    _rgb_color always holds the exact (r, g, b) values last sent to the droid.
    HA derives the displayed brightness from max(r, g, b), so no separate
    brightness property is needed and there is no double-scaling.
    """

    _attr_has_entity_name = True
    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect_list = list(COLOUR_PRESETS)

    def __init__(
        self,
        coordinator: R2D2Coordinator,
        entry: ConfigEntry,
        key: str,
        translation_key: str,
    ) -> None:
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
        self._effect: str | None = None

    async def async_added_to_hass(self) -> None:
        """Restore last state so the colour picker shows the actual last colour."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            self._is_on = last_state.state == STATE_ON
            if rgb := last_state.attributes.get(ATTR_RGB_COLOR):
                self._rgb_color = (int(rgb[0]), int(rgb[1]), int(rgb[2]))
            self._effect = last_state.attributes.get(ATTR_EFFECT)

    @property
    def available(self) -> bool:
        return self.coordinator.droid is not None and self.coordinator.droid.connected

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def rgb_color(self) -> tuple[int, int, int]:
        """Return the exact colour currently sent to the droid."""
        return self._rgb_color

    @property
    def effect(self) -> str | None:
        return self._effect

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on.

        Priority: effect > rgb_color > brightness-only rescale.
        """
        if ATTR_EFFECT in kwargs:
            effect = kwargs[ATTR_EFFECT]
            if effect in COLOUR_PRESETS:
                self._rgb_color = COLOUR_PRESETS[effect]
                self._effect = effect
        elif ATTR_RGB_COLOR in kwargs:
            self._rgb_color = kwargs[ATTR_RGB_COLOR]
            self._effect = None  # custom colour clears preset name
        elif ATTR_BRIGHTNESS in kwargs:
            # Rescale current colour to new brightness while preserving hue.
            new_brightness = kwargs[ATTR_BRIGHTNESS]
            current_max = max(max(self._rgb_color), 1)
            factor = new_brightness / current_max
            self._rgb_color = tuple(min(255, int(c * factor)) for c in self._rgb_color)

        await self._send_color(*self._rgb_color)
        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send_color(0, 0, 0)
        self._is_on = False
        self.async_write_ha_state()

    async def _send_color(self, r: int, g: int, b: int) -> None:
        raise NotImplementedError


class R2D2FrontLED(R2D2RGBLight):
    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "front_led", "front_led")

    async def _send_color(self, r: int, g: int, b: int) -> None:
        await self.coordinator.droid.set_front_led(r, g, b)


class R2D2BackLED(R2D2RGBLight):
    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "back_led", "back_led")

    async def _send_color(self, r: int, g: int, b: int) -> None:
        await self.coordinator.droid.set_back_led(r, g, b)


class R2D2BrightnessLight(CoordinatorEntity[R2D2Coordinator], LightEntity, RestoreEntity):
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

    async def async_added_to_hass(self) -> None:
        """Restore last brightness so the slider shows the correct value."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            self._is_on = last_state.state == STATE_ON
            if (brightness := last_state.attributes.get(ATTR_BRIGHTNESS)) is not None:
                self._brightness = int(brightness)

    @property
    def available(self) -> bool:
        return self.coordinator.droid is not None and self.coordinator.droid.connected

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def brightness(self) -> int:
        return self._brightness

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            self._brightness = kwargs[ATTR_BRIGHTNESS]
        await self._send_brightness(self._brightness)
        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send_brightness(0)
        self._is_on = False
        self.async_write_ha_state()

    async def _send_brightness(self, brightness: int) -> None:
        raise NotImplementedError


class R2D2HoloProjector(R2D2BrightnessLight):
    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "holo_projector", "holo_projector")

    async def _send_brightness(self, brightness: int) -> None:
        await self.coordinator.droid.set_holo_projector(brightness)


class R2D2LogicDisplay(R2D2BrightnessLight):
    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "logic_display", "logic_display")

    async def _send_brightness(self, brightness: int) -> None:
        await self.coordinator.droid.set_logic_display(brightness)
