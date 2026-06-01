"""The Sphero R2D2 / Q5 Droid integration."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.service import async_extract_config_entry_ids

from .const import DOMAIN, PLATFORMS
from .coordinator import R2D2Coordinator
from .droid.data import DriveFlags

_LOGGER = logging.getLogger(__name__)

SERVICE_DRIVE = "drive"
SERVICE_STOP = "stop"

ATTR_SPEED = "speed"
ATTR_HEADING = "heading"
ATTR_DIRECTION = "direction"

_DIRECTION_FLAGS = {
    "forward": DriveFlags.FORWARD,
    "backward": DriveFlags.BACKWARD,
    "turbo": DriveFlags.TURBO,
    "fast_turn": DriveFlags.FAST_TURN,
}

# Allow the target keys (device_id / entity_id / area_id / …) through; they are
# resolved by async_extract_config_entry_ids, not by the schema.
DRIVE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SPEED): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
        vol.Optional(ATTR_HEADING, default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=359)),
        vol.Optional(ATTR_DIRECTION, default="forward"): vol.In(list(_DIRECTION_FLAGS)),
    },
    extra=vol.ALLOW_EXTRA,
)

STOP_SCHEMA = vol.Schema({}, extra=vol.ALLOW_EXTRA)


async def _resolve_coordinators(hass: HomeAssistant, call: ServiceCall) -> list[R2D2Coordinator]:
    """Map a service call's target (device/entity/area) to R2D2 coordinators.

    With no target, applies to every configured droid (the common single-droid
    case, where targeting is unnecessary).
    """
    store: dict = hass.data.get(DOMAIN, {})
    entry_ids = await async_extract_config_entry_ids(hass, call)
    if not entry_ids:
        return list(store.values())
    return [store[e] for e in entry_ids if e in store]


def _async_register_services(hass: HomeAssistant) -> None:
    """Register the domain-level drive/stop services once."""

    async def _handle_drive(call: ServiceCall) -> None:
        flags = _DIRECTION_FLAGS[call.data[ATTR_DIRECTION]]
        speed = call.data[ATTR_SPEED]
        heading = call.data[ATTR_HEADING]
        for coordinator in await _resolve_coordinators(hass, call):
            await coordinator.async_drive(speed, heading, flags)

    async def _handle_stop(call: ServiceCall) -> None:
        for coordinator in await _resolve_coordinators(hass, call):
            await coordinator.async_stop_drive()

    hass.services.async_register(DOMAIN, SERVICE_DRIVE, _handle_drive, schema=DRIVE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_STOP, _handle_stop, schema=STOP_SCHEMA)


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

    # Register domain services once (shared across all droid entries).
    if not hass.services.has_service(DOMAIN, SERVICE_DRIVE):
        _async_register_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        coordinator: R2D2Coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_disconnect()

        # Remove domain services when the last droid entry is unloaded.
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_DRIVE)
            hass.services.async_remove(DOMAIN, SERVICE_STOP)

    return unload_ok
