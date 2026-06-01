"""Select entities for the R2D2 integration."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import R2D2Coordinator
from .droid.data import ANIMATIONS, AUDIO

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up R2D2 select entities."""
    coordinator: R2D2Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            AnimationSelect(coordinator, entry),
            AudioSelect(coordinator, entry),
        ]
    )


class AnimationSelect(CoordinatorEntity[R2D2Coordinator], SelectEntity):
    """Select an animation to play on the droid."""

    _attr_has_entity_name = True
    _attr_translation_key = "animation"
    _attr_options = list(ANIMATIONS.keys())

    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        """Initialise animation select."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_animation"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=coordinator.droid_name,
            manufacturer="Sphero",
            model="R2-D2 / Q5",
        )
        self._current_option: str | None = None

    @property
    def available(self) -> bool:
        return True

    @property
    def current_option(self) -> str | None:
        """Return the last selected animation."""
        return self._current_option

    async def async_select_option(self, option: str) -> None:
        """Play the selected animation."""
        await self.coordinator.async_ensure_connected()
        await self.coordinator.droid.animate(option)
        self._current_option = option
        self.async_write_ha_state()


class AudioSelect(CoordinatorEntity[R2D2Coordinator], SelectEntity):
    """Select an audio clip to play on the droid."""

    _attr_has_entity_name = True
    _attr_translation_key = "audio"
    _attr_options = list(AUDIO.keys())

    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        """Initialise audio select."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_audio"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=coordinator.droid_name,
            manufacturer="Sphero",
            model="R2-D2 / Q5",
        )
        self._current_option: str | None = None

    @property
    def available(self) -> bool:
        return True

    @property
    def current_option(self) -> str | None:
        """Return the last selected audio clip."""
        return self._current_option

    async def async_select_option(self, option: str) -> None:
        """Play the selected audio clip."""
        await self.coordinator.async_ensure_connected()
        await self.coordinator.droid.play_audio(option)
        self._current_option = option
        self.async_write_ha_state()
