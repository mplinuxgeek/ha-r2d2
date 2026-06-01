"""The Sphero R2D2 / Q5 Droid integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .coordinator import R2D2Coordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up R2D2 from a config entry.

    Always succeeds — entities load as unavailable if the droid is off at boot.
    The BLE advertisement callback handles auto-reconnect when it powers on.
    """
    coordinator = R2D2Coordinator(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Register BLE watcher and attempt background initial connect.
    # Does not raise — integration loads even when droid is off.
    await coordinator.async_setup()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        coordinator: R2D2Coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_disconnect()

    return unload_ok
