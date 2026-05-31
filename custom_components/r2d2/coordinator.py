"""DataUpdateCoordinator for the R2D2 integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.bluetooth import (
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
    async_last_service_info,
    async_register_callback,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    CONF_ADDRESS,
    CONF_NAME,
    ATTR_ACCEL_X,
    ATTR_ACCEL_Y,
    ATTR_ACCEL_Z,
    ATTR_IMU_PITCH,
    ATTR_IMU_ROLL,
    ATTR_IMU_YAW,
    ATTR_HEAD_ANGLE,
    ATTR_GYRO_X,
    ATTR_GYRO_Y,
    ATTR_GYRO_Z,
    ATTR_RSSI,
)
from .droid.client import DroidClient

_LOGGER = logging.getLogger(__name__)

_POLL_INTERVAL = timedelta(seconds=30)


class R2D2Coordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that manages the droid connection and sensor data."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"R2D2 {entry.data[CONF_ADDRESS]}",
            update_interval=_POLL_INTERVAL,
        )
        self.hass = hass
        self.entry = entry
        self.address: str = entry.data[CONF_ADDRESS]
        self.droid_name: str = entry.data.get(CONF_NAME, f"Droid {self.address}")
        self.droid = DroidClient(self.address)
        self.droid.reconnect_hook = self.async_reconnect
        self.sensor_data: dict[str, Any] = {}
        self._cancel_bt_callback = None
        self._reconnecting: bool = False
        self.dome_angle: float = 0.0   # last commanded angle
        self.dome_speed: int = 10      # 1 = slowest slew, 10 = instant
        # Shared LED state — all light/switch entities read and write here so
        # they stay in sync without knowing about each other.
        self.led_state: dict[str, Any] = {
            "front_led":      {"on": False, "rgb": (255, 255, 255), "effect": None},
            "back_led":       {"on": False, "rgb": (255, 255, 255), "effect": None},
            "holo_projector": {"on": False, "brightness": 255},
            "logic_display":  {"on": False, "brightness": 255},
        }

    async def async_connect(self) -> None:
        """Connect to the droid, run handshake, enable sensors."""
        ble_device = async_ble_device_from_address(self.hass, self.address, connectable=True)
        if ble_device is None:
            raise ConfigEntryNotReady(
                f"Bluetooth device {self.address} not found. "
                "Ensure the droid is powered on and in range."
            )

        await self.droid.connect(ble_device)
        _LOGGER.debug("Connected to %s, sending init", self.address)
        await self.droid.init()
        self.droid.sensor_callback = self._on_sensor_data
        _LOGGER.debug("Enabling sensor streams on %s", self.address)
        await self.droid.enable_all_sensors()
        await self.droid.animate("wwm_whisper")
        _LOGGER.info("R2D2 droid %s connected and initialised", self.address)
        self._reset_led_state()

        if self._cancel_bt_callback is None:
            self._cancel_bt_callback = async_register_callback(
                self.hass,
                self._on_ble_advertisement,
                {"address": self.address, "connectable": True},
                BluetoothScanningMode.ACTIVE,
            )

    @callback
    def _on_ble_advertisement(
        self,
        service_info: BluetoothServiceInfoBleak,
        change: BluetoothChange,
    ) -> None:
        """Fire when the droid is seen advertising — reconnect if needed."""
        if not self.droid.connected and not self._reconnecting:
            _LOGGER.info("Droid %s detected advertising, auto-reconnecting", self.address)
            self._reconnecting = True
            # Pass the BLEDevice straight from the advertisement so we never
            # need to look it up again (the cache may have expired by then).
            self.hass.async_create_task(self._auto_reconnect(service_info.device))

    async def _auto_reconnect(self, ble_device=None) -> None:
        """Attempt reconnection; called from the BLE advertisement callback."""
        try:
            await self.async_reconnect(ble_device=ble_device)
        except Exception as exc:
            _LOGGER.warning("Auto-reconnect to %s failed: %s", self.address, exc)
        finally:
            self._reconnecting = False

    def _reset_led_state(self) -> None:
        """Mark all LEDs as off after every connect.

        RestoreEntity may have populated led_state with the last-known on/off
        values, but the droid's LEDs are always off after a power cycle.
        """
        for key, state in self.led_state.items():
            self.led_state[key] = {**state, "on": False}
        self.async_update_listeners()

    def _on_sensor_data(self, data: dict) -> None:
        """Handle live sensor push from droid. Called in the HA event loop on Linux."""
        _LOGGER.debug("Sensor data received: %s", data)
        flat: dict[str, Any] = {}

        imu = data.get("imu")
        if imu:
            flat[ATTR_IMU_PITCH] = imu.get("pitch")
            flat[ATTR_IMU_ROLL] = imu.get("roll")
            flat[ATTR_IMU_YAW] = imu.get("yaw")

        accel = data.get("accel")
        if accel:
            flat[ATTR_ACCEL_X] = accel.get("x")
            flat[ATTR_ACCEL_Y] = accel.get("y")
            flat[ATTR_ACCEL_Z] = accel.get("z")

        if "head_angle" in data:
            flat[ATTR_HEAD_ANGLE] = data["head_angle"]

        gyro = data.get("gyro")
        if gyro:
            flat[ATTR_GYRO_X] = gyro.get("x")
            flat[ATTR_GYRO_Y] = gyro.get("y")
            flat[ATTR_GYRO_Z] = gyro.get("z")

        self.sensor_data.update(flat)
        self.async_set_updated_data({**(self.data or {}), **flat})

    async def async_reconnect(self, ble_device=None) -> None:
        """Re-establish BLE connection after the droid wakes from sleep.

        ble_device may be supplied directly (e.g. from an advertisement
        callback) to avoid a cache lookup that may return None if the
        connectable-scanner cache entry has expired.

        Retries up to 3 times with a 5-second pause between attempts to
        handle transient "no backend" errors from the HA bluetooth stack.
        """
        if self.droid.connected:
            await self.droid.disconnect()

        _MAX_ATTEMPTS = 3
        _RETRY_DELAY = 5  # seconds

        last_exc: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                device = ble_device
                if device is None:
                    device = async_ble_device_from_address(
                        self.hass, self.address, connectable=True
                    )
                if device is None:
                    device = async_ble_device_from_address(
                        self.hass, self.address, connectable=False
                    )
                if device is None:
                    raise HomeAssistantError(
                        f"Droid {self.address} not found — power it on and ensure it is in range."
                    )

                await self.droid.connect(device)
                await self.droid.init()
                self.droid.sensor_callback = self._on_sensor_data
                await self.droid.enable_all_sensors()
                await self.droid.animate("wwm_whisper")
                self.sensor_data.clear()
                self._reset_led_state()
                _LOGGER.info("R2D2 droid %s reconnected (attempt %d)", self.address, attempt)
                # Immediately refresh so all entities leave unknown without
                # waiting for the next scheduled poll (up to 30 s away).
                self.hass.async_create_task(self.async_refresh())
                return
            except HomeAssistantError:
                raise  # "not found" is not a transient error — fail immediately
            except Exception as exc:
                last_exc = exc
                _LOGGER.warning(
                    "Reconnect attempt %d/%d to %s failed: %s",
                    attempt, _MAX_ATTEMPTS, self.address, exc,
                )
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_DELAY)

        raise HomeAssistantError(
            f"Failed to reconnect to {self.address} after {_MAX_ATTEMPTS} attempts: {last_exc}"
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Poll battery and RSSI every update interval."""
        if not self.droid.connected:
            raise UpdateFailed("Droid is not connected")

        battery = await self.droid.battery()
        current = dict(self.data or {})
        if battery is not None:
            current["battery"] = battery

        service_info = async_last_service_info(self.hass, self.address, connectable=True)
        if service_info is not None:
            current[ATTR_RSSI] = service_info.rssi

        current.update(self.sensor_data)
        return current

    async def async_disconnect(self) -> None:
        """Disconnect from the droid and cancel the BLE advertisement watcher."""
        if self._cancel_bt_callback:
            self._cancel_bt_callback()
            self._cancel_bt_callback = None
        await self.droid.disconnect()
        _LOGGER.info("R2D2 droid %s disconnected", self.address)
