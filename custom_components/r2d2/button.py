"""Button entities for the R2D2 integration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Awaitable, Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import R2D2Coordinator


@dataclass(frozen=True, kw_only=True)
class R2D2ButtonEntityDescription(ButtonEntityDescription):
    """Describe an R2D2 button entity."""
    press_fn: Callable[[R2D2Coordinator], Awaitable[Any]] = None  # type: ignore[assignment]


BUTTON_DESCRIPTIONS: tuple[R2D2ButtonEntityDescription, ...] = (
    R2D2ButtonEntityDescription(
        key="power_off",
        translation_key="power_off",
        press_fn=lambda c: c.droid.off(),
    ),
    R2D2ButtonEntityDescription(
        key="tripod",
        translation_key="tripod",
        press_fn=lambda c: c.droid.tripod(),
    ),
    R2D2ButtonEntityDescription(
        key="bipod",
        translation_key="bipod",
        press_fn=lambda c: c.droid.bipod(),
    ),
    R2D2ButtonEntityDescription(
        key="waddle_on",
        translation_key="waddle_on",
        press_fn=lambda c: c.droid.waddle(True),
    ),
    R2D2ButtonEntityDescription(
        key="waddle_off",
        translation_key="waddle_off",
        press_fn=lambda c: c.droid.waddle(False),
    ),
    R2D2ButtonEntityDescription(
        key="stop",
        translation_key="stop",
        press_fn=lambda c: c.droid.stop(),
    ),
    R2D2ButtonEntityDescription(
        key="reset_yaw",
        translation_key="reset_yaw",
        press_fn=lambda c: c.droid.reset_yaw(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up R2D2 button entities."""
    coordinator: R2D2Coordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list = [
        R2D2Button(coordinator, entry, description)
        for description in BUTTON_DESCRIPTIONS
    ]
    entities.append(R2D2ReconnectButton(coordinator, entry))
    async_add_entities(entities)


class R2D2Button(CoordinatorEntity[R2D2Coordinator], ButtonEntity):
    """A button entity for an R2D2 command."""

    entity_description: R2D2ButtonEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: R2D2Coordinator,
        entry: ConfigEntry,
        description: R2D2ButtonEntityDescription,
    ) -> None:
        """Initialise the button."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=coordinator.droid_name,
            manufacturer="Sphero",
            model="R2-D2 / Q5",
        )

    @property
    def available(self) -> bool:
        """Return True if the droid is connected."""
        return self.coordinator.droid is not None and self.coordinator.droid.connected

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.entity_description.press_fn(self.coordinator)


class R2D2ReconnectButton(CoordinatorEntity[R2D2Coordinator], ButtonEntity):
    """Reconnect button — always available so it can be pressed while disconnected."""

    _attr_has_entity_name = True
    _attr_translation_key = "reconnect"

    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        """Initialise the reconnect button."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_reconnect"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=coordinator.droid_name,
            manufacturer="Sphero",
            model="R2-D2 / Q5",
        )

    @property
    def available(self) -> bool:
        """Always available — the whole point is to press it when disconnected."""
        return True

    async def async_press(self) -> None:
        """Reconnect to the droid."""
        await self.coordinator.async_reconnect()
