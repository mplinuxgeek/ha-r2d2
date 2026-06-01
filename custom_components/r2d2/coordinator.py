"""DataUpdateCoordinator for the R2D2 integration."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

_HEARTBEAT_TIMEOUT = 15   # seconds of sensor silence → phantom connection
_WATCHDOG_INTERVAL = 15   # how often the watchdog fires

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
        self._reconnect_lock = asyncio.Lock()
        self._last_sensor_time: float | None = None   # heartbeat: last sensor packet
        self._connected_at: float | None = None       # heartbeat: when BLE connected
        self.dome_angle: float = 0.0   # last commanded angle
        self.dome_speed: int = 10      # 1 = slowest slew, 10 = instant
        self.keep_awake: bool = False  # set by R2D2KeepAwakeSwitch
        self._watchdog_task: asyncio.Task | None = None
        # Shared LED state — all light/switch entities read and write here so
        # they stay in sync without knowing about each other.
        self.led_state: dict[str, Any] = {
            "front_led":      {"on": False, "rgb": (255, 0, 0), "effect": "Red"},
            "back_led":       {"on": False, "rgb": (0, 102, 204), "effect": "R2D2 Blue"},
            "holo_projector": {"on": False, "brightness": 255},
            "logic_display":  {"on": False, "brightness": 255},
        }

    async def async_setup(self) -> None:
        """Register BLE advertisement watcher and attempt initial connect in background.

        Always succeeds — entities load as unavailable if the droid is off.
        """
        if self._cancel_bt_callback is None:
            # Register unconditionally so we catch advertisements even when droid is off.
            self._cancel_bt_callback = async_register_callback(
                self.hass,
                self._on_ble_advertisement,
                {"address": self.address},
                BluetoothScanningMode.ACTIVE,
            )
        # Start phantom-breaker watchdog
        self._watchdog_task = self.hass.async_create_task(self._heartbeat_watchdog())
        # Non-blocking initial connect — failure is normal if droid is off at boot
        self.hass.async_create_task(self._initial_connect())

    async def _initial_connect(self) -> None:
        """Attempt connect at startup; swallow errors — BLE callback will retry."""
        try:
            await self.async_connect()
        except Exception as exc:
            _LOGGER.info(
                "async_setup: initial connect failed (%s) — will retry on advertisement", exc
            )

    async def _heartbeat_watchdog(self) -> None:
        """Runs every 15s; disconnects phantom BLE connections.

        A phantom is when Bleak reports connected but sensor data has gone
        silent (droid powered off).  Disconnecting purges the OS socket so the
        next advertisement triggers a clean reconnect.
        """
        while True:
            await asyncio.sleep(_WATCHDOG_INTERVAL)
            if self.droid.connected and not self.is_connected:
                _LOGGER.info(
                    "_heartbeat_watchdog: sensor silence > %ds on connected client — "
                    "phantom connection, disconnecting to allow clean reconnect",
                    _HEARTBEAT_TIMEOUT,
                )
                try:
                    await self.droid.disconnect()
                except Exception as exc:
                    _LOGGER.debug("_heartbeat_watchdog: disconnect error: %s", exc)
                self.async_update_listeners()

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
        self._connected_at = time.monotonic()
        self._last_sensor_time = None
        _LOGGER.info("R2D2 droid %s connected and initialised", self.address)
        self._reset_led_state()

    @callback
    def _on_ble_advertisement(
        self,
        service_info: BluetoothServiceInfoBleak,
        change: BluetoothChange,
    ) -> None:
        """Fire when the droid is seen advertising — reconnect if needed."""
        _LOGGER.debug(
            "_on_ble_advertisement: address=%s rssi=%s connectable=%s change=%s connected=%s reconnecting=%s",
            service_info.address, service_info.rssi, service_info.connectable,
            change, self.droid.connected, self._reconnecting,
        )
        if not self.droid.connected and not self._reconnecting:
            _LOGGER.info(
                "_on_ble_advertisement: droid seen advertising (connectable=%s), triggering auto-reconnect",
                service_info.connectable,
            )
            self._reconnecting = True
            # Only hand the BLEDevice directly to the reconnect if it is
            # connectable — a non-connectable advertisement device cannot
            # support GATT writes, so the handshake would silently fail.
            # For non-connectable advertisements we let async_reconnect look
            # the device up from HA's cache (which will have a connectable
            # entry once the droid has been advertising long enough).
            ble_device = service_info.device if service_info.connectable else None
            self.hass.async_create_task(self._auto_reconnect(ble_device))
        else:
            _LOGGER.debug(
                "_on_ble_advertisement: skipping (connected=%s reconnecting=%s)",
                self.droid.connected, self._reconnecting,
            )

    async def _auto_reconnect(self, ble_device=None) -> None:
        """Attempt reconnection triggered by BLE advertisement callback."""
        _LOGGER.debug("_auto_reconnect: starting (ble_device=%s)", ble_device)
        try:
            await self.async_reconnect(ble_device=ble_device)
        except Exception as exc:
            _LOGGER.warning("_auto_reconnect: failed: %s", exc)
        finally:
            self._reconnecting = False
            _LOGGER.debug("_auto_reconnect: done, reconnecting flag cleared")

    def _reset_led_state(self) -> None:
        """Mark all LEDs as off after every connect.

        RestoreEntity may have populated led_state with the last-known on/off
        values, but the droid's LEDs are always off after a power cycle.
        """
        _LOGGER.debug("_reset_led_state: marking all LEDs off and notifying entities")
        for key, state in self.led_state.items():
            self.led_state[key] = {**state, "on": False}
        self.async_update_listeners()

    def _on_sensor_data(self, data: dict) -> None:
        """Handle live sensor push from droid. Called in the HA event loop on Linux."""
        self._last_sensor_time = time.monotonic()
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

    async def async_reconnect(self, ble_device=None, force: bool = False) -> None:
        """Re-establish BLE connection after the droid wakes from sleep.

        Serialised by _reconnect_lock — concurrent callers never race and
        the second caller returns immediately once the first has connected.
        The lock is held only during the actual connection attempt so it
        never blocks other callers for more than a few seconds.

        force=True skips the "already connected" early-return and always
        disconnects then reconnects.  Use this for the explicit Reconnect
        button to break a phantom BLE connection held open by a BT proxy.
        """
        _LOGGER.debug("async_reconnect: waiting for lock (ble_device=%s force=%s)", ble_device, force)
        async with self._reconnect_lock:
            _LOGGER.debug("async_reconnect: lock acquired, droid.connected=%s", self.droid.connected)
            if self.droid.connected and not force:
                _LOGGER.debug("async_reconnect: already connected, returning")
                return
            if self.droid.connected:
                _LOGGER.debug("async_reconnect: force=True — disconnecting phantom connection")
                await self.droid.disconnect()

            device = ble_device
            if device is None:
                _LOGGER.debug("async_reconnect: looking up device in BT cache (connectable=True)")
                device = async_ble_device_from_address(self.hass, self.address, connectable=True)
                if device is None:
                    _LOGGER.debug("async_reconnect: not found connectable, trying connectable=False")
                    device = async_ble_device_from_address(self.hass, self.address, connectable=False)

            if device is None:
                _LOGGER.warning("async_reconnect: device %s not in BT cache", self.address)
                raise HomeAssistantError(
                    f"Droid {self.address} not in range — try again once it is advertising."
                )

            _LOGGER.debug("async_reconnect: device found (%s), attempting connection", device)
            last_exc: Exception | None = None
            for attempt in range(1, 4):
                try:
                    _LOGGER.debug("async_reconnect: connect attempt %d/3", attempt)
                    await self.droid.connect(device)
                    _LOGGER.debug("async_reconnect: BLE connected, sending init")
                    await self.droid.init()
                    _LOGGER.debug("async_reconnect: init OK, enabling sensors")
                    self.droid.sensor_callback = self._on_sensor_data
                    await self.droid.enable_all_sensors()
                    self.sensor_data.clear()
                    self._connected_at = time.monotonic()
                    self._last_sensor_time = None
                    self._reset_led_state()
                    _LOGGER.info("async_reconnect: droid %s reconnected (attempt %d)", self.address, attempt)
                    self.hass.async_create_task(self.async_refresh())
                    return
                except Exception as exc:
                    last_exc = exc
                    _LOGGER.warning(
                        "async_reconnect: attempt %d/3 failed: %s", attempt, exc,
                    )
                    if attempt < 3:
                        _LOGGER.debug("async_reconnect: waiting 5s before retry")
                        await asyncio.sleep(5)

            raise HomeAssistantError(
                f"Failed to reconnect to {self.address} after 3 attempts: {last_exc}"
            )

    @property
    def is_connected(self) -> bool:
        """True when BLE is up AND sensor data has arrived recently.

        This is the canonical "droid is alive" flag used by the binary sensor
        and any logic that needs to know whether to auto-connect.  The raw
        droid.connected BLE flag can stay True for a phantom connection even
        after the droid powers off; sensor silence exposes that state.
        """
        if not self.droid.connected:
            return False
        if self._last_sensor_time is None:
            # Still waiting for first packet after connect — give it 30s
            if self._connected_at is None:
                return False
            return (time.monotonic() - self._connected_at) < 30
        return (time.monotonic() - self._last_sensor_time) < _HEARTBEAT_TIMEOUT

    async def async_ensure_connected(self) -> None:
        """Connect to the droid if not already connected.

        Called by all entity command methods so controls work whether the
        droid is on or off — if it's off, we connect first then run the command.

        If BLE shows connected but sensor data is silent (phantom), we force-
        disconnect before reconnecting so the OS socket is cleanly purged.
        """
        if self.is_connected:
            return
        # Phantom: BLE connected but sensor-silent → force-disconnect first
        force = self.droid.connected
        _LOGGER.debug(
            "async_ensure_connected: not connected (phantom=%s), attempting connect", force
        )
        await self.async_reconnect(force=force)

    async def _async_update_data(self) -> dict[str, Any]:
        """Poll battery and RSSI every 30s; binary sensor driven by is_connected."""
        _LOGGER.debug("_async_update_data: polling (connected=%s is_connected=%s)",
                      self.droid.connected, self.is_connected)

        current = dict(self.data or {})

        if not self.droid.connected:
            # Not connected — notify listeners so binary sensor updates,
            # but don't raise UpdateFailed so the coordinator keeps polling.
            self.async_update_listeners()
            return current

        # Log heartbeat state for diagnostics.
        now = time.monotonic()
        if self._last_sensor_time is not None:
            silence = now - self._last_sensor_time
            _LOGGER.debug("_async_update_data: heartbeat silence=%.1fs (alive=%s)",
                          silence, self.is_connected)
        elif self._connected_at is not None:
            _LOGGER.debug("_async_update_data: waiting for first sensor packet (%.1fs since connect)",
                          now - self._connected_at)
        self.async_update_listeners()

        battery = await self.droid.battery()
        if battery is not None:
            current["battery"] = battery
            _LOGGER.debug("_async_update_data: battery=%d%%", battery)

        service_info = async_last_service_info(self.hass, self.address, connectable=True)
        if service_info is not None:
            current[ATTR_RSSI] = service_info.rssi
            _LOGGER.debug("_async_update_data: rssi=%d dBm", service_info.rssi)
        else:
            _LOGGER.debug("_async_update_data: no service_info for RSSI")

        current.update(self.sensor_data)
        return current

    async def async_disconnect(self) -> None:
        """Unload: cancel watchdog, unregister BLE callback, sleep droid, disconnect."""
        # Cancel phantom-breaker watchdog
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
        self._watchdog_task = None

        if self._cancel_bt_callback:
            self._cancel_bt_callback()
            self._cancel_bt_callback = None

        if self.droid.connected:
            try:
                _LOGGER.debug("async_disconnect: sending sleep command before BLE teardown")
                await self.droid.off()
            except Exception as exc:
                _LOGGER.debug("async_disconnect: sleep command failed: %s", exc)

        await self.droid.disconnect()
        _LOGGER.info("R2D2 droid %s disconnected", self.address)
