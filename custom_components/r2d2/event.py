"""Event entities for the R2D2 integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import R2D2Coordinator
from .entity import R2D2Entity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up R2D2 event entities."""
    coordinator: R2D2Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([R2D2CollisionEvent(coordinator, entry)])


class R2D2CollisionEvent(R2D2Entity, EventEntity):
    """Fires when the droid detects a physical collision (a bump)."""

    _attr_translation_key = "collision"
    _attr_device_class = EventDeviceClass.MOTION
    _attr_event_types = ["collision"]

    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        """Initialise the collision event entity."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_collision"

    async def async_added_to_hass(self) -> None:
        """Subscribe to collision notifications from the coordinator."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.coordinator.signal_collision, self._handle_collision
            )
        )

    @callback
    def _handle_collision(self, data: dict[str, Any]) -> None:
        """Record a collision and notify HA."""
        self._trigger_event("collision", data)
        self.async_write_ha_state()
