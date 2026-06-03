"""Button entities for the R2D2 integration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Awaitable, Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import R2D2Coordinator
from .entity import R2D2Entity


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
    entities.append(R2D2StopAnimationButton(coordinator, entry))
    async_add_entities(entities)


class R2D2Button(R2D2Entity, ButtonEntity):
    """A button entity for an R2D2 command."""

    entity_description: R2D2ButtonEntityDescription

    def __init__(
        self,
        coordinator: R2D2Coordinator,
        entry: ConfigEntry,
        description: R2D2ButtonEntityDescription,
    ) -> None:
        """Initialise the button."""
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    async def async_press(self) -> None:
        """Connect if needed, then run the command."""
        await self.coordinator.async_ensure_connected()
        await self.entity_description.press_fn(self.coordinator)


class R2D2ReconnectButton(R2D2Entity, ButtonEntity):
    """Reconnect button — always available so it can be pressed while disconnected."""

    _attr_translation_key = "reconnect"

    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        """Initialise the reconnect button."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_reconnect"

    async def async_press(self) -> None:
        """Reconnect to the droid and confirm with a whisper."""
        await self.coordinator.async_reconnect(force=True)
        await self.coordinator.droid.animate("wwm_whisper")


class R2D2StopAnimationButton(R2D2Entity, ButtonEntity):
    """Cut the current animation short. No-op (no wake) when disconnected —
    there's nothing playing on a sleeping droid."""

    _attr_translation_key = "stop_animation"

    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_stop_animation"

    async def async_press(self) -> None:
        if self.coordinator.droid.connected:
            await self.coordinator.droid.stop_animation()
