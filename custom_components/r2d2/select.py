"""Select entities for the R2D2 integration."""
from __future__ import annotations

from homeassistant.components.logbook import async_log_entry
from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import R2D2Coordinator
from .droid.data import AUDIO
from .entity import R2D2Entity


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


class AnimationSelect(R2D2Entity, SelectEntity):
    """Select an animation to play on the droid."""

    _attr_translation_key = "animation"

    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        """Initialise animation select."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_animation"
        # Options depend on the droid model (R2-D2 and R2-Q5 expose different
        # emotes), so build them from the model-specific table.
        self._attr_options = list(coordinator.droid.animations.keys())
        self._current_option: str | None = None

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
        # A select only logs to the logbook when its value *changes*, so
        # replaying the same animation (the common automation case) would leave
        # no record.  Log the action explicitly every time, carrying the call
        # context so the entry is attributed to the triggering automation.
        async_log_entry(
            self.hass,
            self.name,
            f"played animation {option}",
            DOMAIN,
            self.entity_id,
            self._context,
        )


class AudioSelect(R2D2Entity, SelectEntity):
    """Select an audio clip to play on the droid."""

    _attr_translation_key = "audio"
    _attr_options = list(AUDIO.keys())

    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        """Initialise audio select."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_audio"
        self._current_option: str | None = None

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
        # Same as the animation select: log every play so replaying the same
        # clip still produces a logbook entry, attributed to the caller.
        async_log_entry(
            self.hass,
            self.name,
            f"played audio {option}",
            DOMAIN,
            self.entity_id,
            self._context,
        )
