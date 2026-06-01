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
    coordinator: R2D2Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        R2D2FrontLED(coordinator, entry),
        R2D2BackLED(coordinator, entry),
        R2D2HoloProjector(coordinator, entry),
        R2D2LogicDisplay(coordinator, entry),
    ])


class R2D2RGBLight(CoordinatorEntity[R2D2Coordinator], LightEntity, RestoreEntity):
    """RGB LED entity backed by coordinator.led_state[key].

    All reads go through coordinator.led_state so any entity (including the
    all-lights switch) that writes there is immediately visible here.
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

    async def async_added_to_hass(self) -> None:
        """Restore last colour/effect but never the on/off state.

        The droid always powers up with LEDs off so restoring on=True would
        show the toggles as on while the droid is actually dark.
        """
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            if rgb := last_state.attributes.get(ATTR_RGB_COLOR):
                self.coordinator.led_state[self._key]["rgb"] = (
                    int(rgb[0]), int(rgb[1]), int(rgb[2])
                )
            self.coordinator.led_state[self._key]["effect"] = (
                last_state.attributes.get(ATTR_EFFECT)
            )

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.led_state[self._key]["on"]

    @property
    def rgb_color(self) -> tuple[int, int, int]:
        return self.coordinator.led_state[self._key]["rgb"]

    @property
    def effect(self) -> str | None:
        return self.coordinator.led_state[self._key]["effect"]

    def _update_state(self, on: bool, rgb: tuple[int, int, int], effect: str | None) -> None:
        self.coordinator.led_state[self._key] = {"on": on, "rgb": rgb, "effect": effect}
        self.coordinator.async_update_listeners()

    async def async_turn_on(self, **kwargs: Any) -> None:
        state = self.coordinator.led_state[self._key]
        rgb = state["rgb"]
        effect = state["effect"]

        if ATTR_EFFECT in kwargs:
            effect = kwargs[ATTR_EFFECT]
            if effect in COLOUR_PRESETS:
                rgb = COLOUR_PRESETS[effect]
        elif ATTR_RGB_COLOR in kwargs:
            rgb = kwargs[ATTR_RGB_COLOR]
            effect = None
        elif ATTR_BRIGHTNESS in kwargs:
            current_max = max(max(rgb), 1)
            factor = kwargs[ATTR_BRIGHTNESS] / current_max
            rgb = tuple(min(255, int(c * factor)) for c in rgb)

        await self._send_color(*rgb)
        self._update_state(True, rgb, effect)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send_color(0, 0, 0)
        self._update_state(False, self.coordinator.led_state[self._key]["rgb"], None)

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
    """Brightness-only LED entity backed by coordinator.led_state[key]."""

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

    async def async_added_to_hass(self) -> None:
        """Restore last brightness but never the on/off state."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            if (b := last_state.attributes.get(ATTR_BRIGHTNESS)) is not None:
                self.coordinator.led_state[self._key]["brightness"] = int(b)

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.led_state[self._key]["on"]

    @property
    def brightness(self) -> int:
        return self.coordinator.led_state[self._key]["brightness"]

    def _update_state(self, on: bool, brightness: int) -> None:
        self.coordinator.led_state[self._key] = {"on": on, "brightness": brightness}
        self.coordinator.async_update_listeners()

    async def async_turn_on(self, **kwargs: Any) -> None:
        brightness = kwargs.get(
            ATTR_BRIGHTNESS, self.coordinator.led_state[self._key]["brightness"]
        )
        await self._send_brightness(brightness)
        self._update_state(True, brightness)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send_brightness(0)
        self._update_state(False, self.coordinator.led_state[self._key]["brightness"])

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
