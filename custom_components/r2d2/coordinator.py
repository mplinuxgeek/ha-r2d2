"""DataUpdateCoordinator for the R2D2 integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.bluetooth import async_ble_device_from_address
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
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
        self.sensor_data: dict[str, Any] = {}

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
        _LOGGER.info("R2D2 droid %s connected and initialised", self.address)

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

    async def _async_update_data(self) -> dict[str, Any]:
        """Poll battery level every update interval."""
        if not self.droid.connected:
            raise UpdateFailed("Droid is not connected")

        battery = await self.droid.battery()
        current = dict(self.data or {})
        if battery is not None:
            current["battery"] = battery
        current.update(self.sensor_data)
        return current

    async def async_disconnect(self) -> None:
        """Disconnect from the droid."""
        await self.droid.disconnect()
        _LOGGER.info("R2D2 droid %s disconnected", self.address)
