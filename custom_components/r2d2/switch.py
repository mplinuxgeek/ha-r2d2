"""Switch entities for the R2D2 integration."""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from homeassistant.components.logbook import async_log_entry

from homeassistant.components.switch import SwitchEntity
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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: R2D2Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        R2D2AllLightsSwitch(coordinator, entry),
        R2D2KeepAwakeSwitch(coordinator, entry),
    ])


class R2D2AllLightsSwitch(CoordinatorEntity[R2D2Coordinator], SwitchEntity):
    """Toggle all droid lights on/off.

    is_on is derived from coordinator.led_state so it stays in sync with the
    individual light entities — it is True only when every light is on.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "all_lights"

    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_all_lights"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=coordinator.droid_name,
            manufacturer="Sphero",
            model="R2-D2 / Q5",
        )

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return all(v["on"] for v in self.coordinator.led_state.values())

    async def async_turn_on(self, **kwargs: Any) -> None:
        droid = self.coordinator.droid
        await droid.set_front_led(255, 0, 0)
        await droid.set_back_led(0, 102, 204)
        await droid.set_holo_projector(255)
        await droid.set_logic_display(255)
        self.coordinator.led_state["front_led"]      = {"on": True, "rgb": (255, 0, 0),     "effect": "Red"}
        self.coordinator.led_state["back_led"]       = {"on": True, "rgb": (0, 102, 204),   "effect": "R2D2 Blue"}
        self.coordinator.led_state["holo_projector"] = {"on": True, "brightness": 255}
        self.coordinator.led_state["logic_display"]  = {"on": True, "brightness": 255}
        self.coordinator.async_update_listeners()

    async def async_turn_off(self, **kwargs: Any) -> None:
        droid = self.coordinator.droid
        await droid.set_front_led(0, 0, 0)
        await droid.set_back_led(0, 0, 0)
        await droid.set_holo_projector(0)
        await droid.set_logic_display(0)
        for key, state in self.coordinator.led_state.items():
            self.coordinator.led_state[key] = {**state, "on": False}
        self.coordinator.async_update_listeners()


class R2D2KeepAwakeSwitch(CoordinatorEntity[R2D2Coordinator], SwitchEntity, RestoreEntity):
    """Periodically play a random idle animation to prevent the droid sleeping.

    Fires every 6–9 minutes (random). Always available so the preference can
    be toggled even while disconnected; the loop simply skips if not connected.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "keep_awake"

    _IDLE_ANIMATIONS = ["idle_1", "idle_2", "idle_3"]
    _MIN_INTERVAL = 360   # 6 minutes
    _MAX_INTERVAL = 540   # 9 minutes

    def __init__(self, coordinator: R2D2Coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_keep_awake"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=coordinator.droid_name,
            manufacturer="Sphero",
            model="R2-D2 / Q5",
        )
        self._is_on: bool = False
        self._task: asyncio.Task | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            if last_state.state == STATE_ON:
                self._is_on = True
                self._start_task()

    async def async_will_remove_from_hass(self) -> None:
        self._cancel_task()

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._is_on = True
        self._start_task()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._is_on = False
        self._cancel_task()
        self.async_write_ha_state()

    def _start_task(self) -> None:
        self._cancel_task()
        self._task = self.hass.async_create_task(self._loop())

    def _cancel_task(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _loop(self) -> None:
        while True:
            delay = random.uniform(self._MIN_INTERVAL, self._MAX_INTERVAL)
            _LOGGER.debug("Keep-awake: next idle animation in %.0fs", delay)
            await asyncio.sleep(delay)
            if not self.coordinator.droid.connected:
                _LOGGER.debug("Keep-awake: droid not connected, skipping")
                continue
            anim = random.choice(self._IDLE_ANIMATIONS)
            try:
                await self.coordinator.droid.animate(anim)
                _LOGGER.debug("Keep-awake: played %s", anim)
                async_log_entry(
                    self.hass,
                    self.coordinator.droid_name,
                    f"keep-awake: played {anim}",
                    DOMAIN,
                    self.entity_id,
                )
            except Exception as exc:
                _LOGGER.debug("Keep-awake animation failed: %s", exc)
