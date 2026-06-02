"""Base entity for the R2D2 integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import R2D2Coordinator


class R2D2Entity(CoordinatorEntity[R2D2Coordinator]):
    """Common base for all R2D2 entities.

    Provides the shared device info and entity naming, and keeps every entity
    available even while the droid is disconnected — a control press wakes and
    reconnects on demand, so the controls must stay pressable.  Connectivity is
    surfaced separately by the binary sensor (coordinator.is_connected).
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        """Set up shared device info."""
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=coordinator.droid_name,
            manufacturer="Sphero",
            model="R2-D2 / Q5",
        )

    @property
    def available(self) -> bool:
        return True
