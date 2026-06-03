"""DataUpdateCoordinator for the R2D2 integration."""
from __future__ import annotations

import asyncio
import logging
import time
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
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    DOMAIN,
    CONF_ADDRESS,
    CONF_NAME,
    CONF_MODEL,
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
from .droid.data import detect_model

_LOGGER = logging.getLogger(__name__)

_POLL_INTERVAL = timedelta(seconds=30)
_HEARTBEAT_TIMEOUT = 10   # seconds of sensor silence → phantom connection
_WATCHDOG_INTERVAL = 10   # how often the watchdog fires
_CONNECT_GRACE = 10       # seconds to wait for first packet after (re)connect/revive
_SENSOR_PUSH_INTERVAL = 1.0  # min seconds between coordinator pushes from sensor stream
_VERIFY_DELAY = 2.0       # seconds between post-connect sensor-stream re-enable kicks
_VERIFY_ATTEMPTS = 3      # how many times to re-enable sensors if the stream doesn't start
_WAKE_SETTLE = 2.0        # seconds to let a just-woken droid's motor controller come
                          # online before the first command (else stance/move is dropped)
_DRIVE_RESEND = 0.3       # seconds between re-asserting the latest drive vector while active
_DRIVE_TIMEOUT = 1.5      # dead-man: auto-stop if no new drive command arrives within this
_STANCE_SETTLE = 2.0      # seconds for the legs to reach tripod before drive commands land


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
        self.entry = entry
        self.address: str = entry.data[CONF_ADDRESS]
        self.droid_name: str = entry.data.get(CONF_NAME, f"Droid {self.address}")
        # Prefer an explicitly stored model; otherwise auto-detect from the BLE
        # name (D2- → R2-D2, Q5- → R2-Q5).  Older entries have no stored model,
        # so detection from the saved name keeps them working.
        self.model: str = entry.data.get(CONF_MODEL) or detect_model(self.droid_name)
        self.droid = DroidClient(self.address, model=self.model)
        self.droid.reconnect_hook = self._reconnect_for_send
        self.sensor_data: dict[str, Any] = {}
        # Static identity read once from the droid (firmware/MAC/SKU); surfaced
        # by the diagnostic sensors and pushed into the device registry.
        self.device_info_data: dict[str, Any] = {}
        self._cancel_bt_callback = None
        self._reconnecting: bool = False
        self._reconnect_lock = asyncio.Lock()
        self._last_sensor_time: float | None = None   # heartbeat: last sensor packet
        self._connected_at: float | None = None       # heartbeat: when BLE connected
        self._last_push_time: float | None = None      # throttle: last sensor push to HA
        self._phantom_revive_attempted: bool = False   # watchdog: tried reviving stream
        self.dome_angle: float = 0.0   # last commanded angle
        self.dome_speed: int = 10      # 1 = slowest slew, 10 = instant
        self.keep_awake: bool = False  # set by R2D2KeepAwakeSwitch
        self._watchdog_task: asyncio.Task | None = None
        self._verify_task: asyncio.Task | None = None  # post-connect sensor-stream kick
        self._drive_target: tuple[int, int, int] | None = None  # (speed, heading, flags)
        self._drive_last: float = 0.0                  # monotonic time of last drive command
        self._drive_task: asyncio.Task | None = None   # background drive re-assert / dead-man
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
        # Start phantom-breaker watchdog as a background task.  A plain
        # hass.async_create_task is tracked as a startup task and HA blocks
        # "wrapping up the start up phase" until it finishes — but the watchdog
        # is a while-True loop that never finishes, so it would stall startup
        # until HA times out.  Background tasks are exempt from that wait and
        # are auto-cancelled on entry unload.
        self._watchdog_task = self.entry.async_create_background_task(
            self.hass, self._heartbeat_watchdog(), name=f"{DOMAIN}_watchdog_{self.address}"
        )
        # Non-blocking initial connect — failure is normal if droid is off at
        # boot.  Also a background task so its connect/retry sleeps don't hold
        # up the startup phase.
        self.entry.async_create_background_task(
            self.hass, self._initial_connect(), name=f"{DOMAIN}_initial_connect_{self.address}"
        )

    async def _initial_connect(self) -> None:
        """Attempt connect at startup; swallow errors — BLE callback will retry."""
        try:
            await self.async_reconnect()
        except Exception as exc:
            _LOGGER.info(
                "async_setup: initial connect failed (%s) — will retry on advertisement", exc
            )

    async def _heartbeat_watchdog(self) -> None:
        """Fires every _WATCHDOG_INTERVAL; reacts to sensor silence on a live link.

        Sensor silence while the BLE socket is still open means either the
        droid has idle-slept (or powered off, leaving a phantom socket held by
        a BT proxy) or a live droid's stream briefly stalled.  Response depends
        on the keep_awake preference:
          * keep_awake off → let the droid rest: disconnect our end cleanly so
            the next control activation reconnects and wakes it.
          * keep_awake on  → try once to revive the stream by re-enabling
            sensors; if it stays silent through the next grace window, treat it
            as a phantom and disconnect so the next advertisement reconnects.
        Disconnecting always purges the OS socket for a clean reconnect.
        """
        while True:
            await asyncio.sleep(_WATCHDOG_INTERVAL)

            if not self.droid.connected:
                self._phantom_revive_attempted = False
                continue

            if self.is_connected:
                # Connected; clear the revive flag once real packets are flowing.
                if self._last_sensor_time is not None:
                    self._phantom_revive_attempted = False
                continue

            # droid.connected is True but is_connected is False → sensor silence.
            if not self.keep_awake:
                # Keep-awake off: don't fight the droid's idle-sleep.  Sensor
                # silence means it has slept (or powered off) — disconnect our
                # end so the socket is clean and the droid can rest.  A control
                # activation reconnects and wakes it on demand.
                _LOGGER.info(
                    "_heartbeat_watchdog: sensor silence and keep_awake off — "
                    "letting droid sleep, disconnecting"
                )
                try:
                    await self.droid.disconnect()
                except Exception as exc:
                    _LOGGER.debug("_heartbeat_watchdog: disconnect error: %s", exc)
                self._phantom_revive_attempted = False
                self.async_update_listeners()
                continue

            # keep_awake on → try to revive the stream to hold the droid awake.
            if not self._phantom_revive_attempted:
                self._phantom_revive_attempted = True
                try:
                    _LOGGER.debug(
                        "_heartbeat_watchdog: sensor silence — re-enabling sensors to revive stream"
                    )
                    await self.droid.enable_all_sensors()
                    # Fresh grace window: wait for the stream to resume before
                    # deciding this is a phantom.
                    self._connected_at = time.monotonic()
                    self._last_sensor_time = None
                    continue
                except Exception as exc:
                    _LOGGER.debug(
                        "_heartbeat_watchdog: sensor re-enable failed (%s) — link dead", exc
                    )

            # Revive already tried and stream still silent → genuine phantom.
            _LOGGER.info(
                "_heartbeat_watchdog: sensor silence > %ds on connected client — "
                "phantom connection, disconnecting to allow clean reconnect",
                _HEARTBEAT_TIMEOUT,
            )
            try:
                await self.droid.disconnect()
            except Exception as exc:
                _LOGGER.debug("_heartbeat_watchdog: disconnect error: %s", exc)
            self._phantom_revive_attempted = False
            self.async_update_listeners()

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
            if not self.keep_awake:
                # Keep-awake off: a background advertisement must not wake a
                # resting droid (reconnect's init would).  Reconnect happens
                # on control activation instead — see async_ensure_connected.
                _LOGGER.debug(
                    "_on_ble_advertisement: keep_awake off — not auto-reconnecting "
                    "(droid left to sleep; control activation will reconnect)"
                )
                return
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

    def _start_sensor_verify(self) -> None:
        """(Re)start the post-connect sensor-stream supervisor.

        A droid freshly woken from sleep acks the sensor-enable commands but
        often does not actually begin streaming — its streaming engine isn't
        running yet at the moment we configure it during the wake handshake.
        This re-enables sensors a few times shortly after connect until packets
        arrive.  Runs as a background task so the wake command (e.g. an
        animation) still fires instantly.
        """
        if self._verify_task and not self._verify_task.done():
            self._verify_task.cancel()
        self._verify_task = self.hass.async_create_task(self._verify_sensor_stream())

    async def _verify_sensor_stream(self) -> None:
        """Re-enable sensors until the stream starts, or give up after a few tries."""
        for attempt in range(1, _VERIFY_ATTEMPTS + 1):
            await asyncio.sleep(_VERIFY_DELAY)
            if self._last_sensor_time is not None:
                _LOGGER.debug("_verify_sensor_stream: stream live, nothing to do")
                return
            if not self.droid.connected:
                _LOGGER.debug("_verify_sensor_stream: no longer connected, stopping")
                return
            _LOGGER.info(
                "_verify_sensor_stream: no sensor data %.0fs after connect — "
                "re-enabling sensors (attempt %d/%d)",
                attempt * _VERIFY_DELAY, attempt, _VERIFY_ATTEMPTS,
            )
            try:
                await self.droid.enable_all_sensors()
                # Push the grace window out so the watchdog doesn't tear the
                # link down while we are still trying to start the stream.
                self._connected_at = time.monotonic()
            except Exception as exc:
                _LOGGER.debug("_verify_sensor_stream: re-enable failed: %s", exc)
                return

    async def _fetch_device_info(self) -> None:
        """Read firmware/MAC/SKU once and publish to entities + device registry."""
        try:
            info = await self.droid.fetch_device_info()
        except Exception as exc:
            _LOGGER.debug("_fetch_device_info: failed: %s", exc)
            return
        if not info:
            return
        self.device_info_data = info
        _LOGGER.info("_fetch_device_info: %s", info)
        # Mirror the version fields onto the device registry entry so they show
        # on the device page.
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(identifiers={(DOMAIN, self.address)})
        if device is not None:
            registry.async_update_device(
                device.id,
                sw_version=info.get("sw_version"),
                hw_version=str(info["board_revision"]) if "board_revision" in info else None,
            )
        self.async_update_listeners()

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
        # Throttle pushes to HA: the stream can fire many packets/second, but
        # entities only need ~1 Hz.  Latest values are always stored above and
        # flushed by the 30s poll, so the most recent reading is never lost for
        # long.  Heartbeat (_last_sensor_time) is updated on every packet.
        now = self._last_sensor_time
        if self._last_push_time is None or (now - self._last_push_time) >= _SENSOR_PUSH_INTERVAL:
            self._last_push_time = now
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
            # Re-evaluate state *inside* the lock — another caller may have
            # already (re)connected while we waited.  Use is_connected (not raw
            # droid.connected) so a phantom is never mistaken for healthy.
            _LOGGER.debug(
                "async_reconnect: lock acquired, droid.connected=%s is_connected=%s",
                self.droid.connected, self.is_connected,
            )
            if self.is_connected and not force:
                _LOGGER.debug("async_reconnect: already connected and alive, returning")
                return
            if self.droid.connected:
                # Phantom (BLE up, sensor-silent) or force=True → purge the
                # socket before rebuilding so the OS handle is clean.
                _LOGGER.debug("async_reconnect: disconnecting stale/phantom connection before reconnect")
                await self.droid.disconnect()

            def _resolve_device():
                dev = async_ble_device_from_address(self.hass, self.address, connectable=True)
                if dev is None:
                    _LOGGER.debug("async_reconnect: not found connectable, trying connectable=False")
                    dev = async_ble_device_from_address(self.hass, self.address, connectable=False)
                return dev

            # Fail fast if the droid is nowhere in the BT cache.
            if ble_device is None and _resolve_device() is None:
                _LOGGER.warning("async_reconnect: device %s not in BT cache", self.address)
                raise HomeAssistantError(
                    f"Droid {self.address} not in range — try again once it is advertising."
                )

            last_exc: Exception | None = None
            for attempt in range(1, 4):
                # Re-resolve the device each attempt when we weren't handed an
                # explicit one — a connectable entry may only appear in the
                # cache after the droid has advertised for a moment.
                device = ble_device if ble_device is not None else _resolve_device()
                if device is None:
                    _LOGGER.debug("async_reconnect: device not yet resolvable on attempt %d", attempt)
                    last_exc = HomeAssistantError("device not in BT cache")
                    if attempt < 3:
                        await asyncio.sleep(5)
                    continue
                try:
                    _LOGGER.debug("async_reconnect: connect attempt %d/3 (device=%s)", attempt, device)
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
                    self._start_sensor_verify()
                    # Read the static identity fields once, in the background so
                    # the reconnect (and the user's command) isn't held up.
                    if not self.device_info_data:
                        self.hass.async_create_task(self._fetch_device_info())
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
            # Still waiting for first packet after connect — give it the grace window
            if self._connected_at is None:
                return False
            return (time.monotonic() - self._connected_at) < _CONNECT_GRACE
        return (time.monotonic() - self._last_sensor_time) < _HEARTBEAT_TIMEOUT

    async def async_ensure_connected(self) -> None:
        """Connect to the droid if not already connected.

        Called by all entity command methods so controls work whether the
        droid is on or off — if it's off, we connect first then run the command.

        Three states:
          * sensors flowing (is_connected) → nothing to do.
          * BLE link up but sensor stream idle (droid idle-slept — it keeps the
            link, stops streaming, and auto-wakes on the next command) → leave
            the link up; the command's own ensure_awake re-inits then re-arms
            the sensors, in order, on a now-awake droid.  Tearing the link down
            here would re-arm sensors mid-wake, before the droid is ready, so
            the stream never resumes.
          * BLE fully down → full reconnect.

        Phantom handling (BLE up but sensor-silent) is decided inside
        async_reconnect under the lock, so concurrent commands can never race
        to tear down a connection one of them just rebuilt.
        """
        if self.is_connected:
            return
        if self.droid.connected:
            _LOGGER.debug(
                "async_ensure_connected: link up but sensors idle — "
                "waking in place (command's ensure_awake re-arms sensors and settles)"
            )
            return
        _LOGGER.debug("async_ensure_connected: not connected, attempting connect")
        await self.async_reconnect()
        # The reconnect just woke a sleeping droid; its motor controller needs a
        # moment before it acts on the first command (a stance/move sent
        # immediately is acked but dropped).  ensure_awake covers the link-up
        # wake-in-place path; this covers the full-reconnect path.
        _LOGGER.debug("async_ensure_connected: settling %.1fs after wake-reconnect", _WAKE_SETTLE)
        await asyncio.sleep(_WAKE_SETTLE)

    async def async_drive(self, speed: int, heading: int = 0, flags: int = 0) -> None:
        """Set the drive vector. Throttled + dead-man-guarded via a background loop.

        Callers (e.g. a joystick card firing many times/second) just update the
        target here; the loop re-asserts it to the droid at a fixed rate and
        auto-stops if commands stop arriving — so a closed dashboard or dropped
        connection can't leave the droid rolling.
        """
        self._drive_target = (int(speed), int(heading) % 360, int(flags))
        self._drive_last = time.monotonic()
        if self._drive_task is None or self._drive_task.done():
            self._drive_task = self.hass.async_create_task(self._drive_loop())

    async def _drive_loop(self) -> None:
        """Re-assert the latest drive vector at a fixed rate until it goes stale."""
        try:
            await self.async_ensure_connected()  # wake + settle on first drive
            # Driving needs tripod (rolling) stance AND the control system on.
            # A roll command sent while the legs are still transitioning to
            # tripod is acked but dropped, so switch stance and let it settle
            # first.  Skip the settle when already in tripod (consecutive drives
            # stay instant); enabling stabilization is cheap, do it every time.
            try:
                if self.droid.stance != "tripod":
                    await self.droid.tripod()
                    await asyncio.sleep(_STANCE_SETTLE)
                await self.droid.set_stabilization(1)
            except Exception as exc:
                _LOGGER.debug("_drive_loop: drive prep failed: %s", exc)
            # Prep (connect/stance settle) can outlast the dead-man window; start
            # its clock now so prep time isn't mistaken for the controller going
            # away and stopping us before we ever roll.
            self._drive_last = time.monotonic()
            while (
                self._drive_target is not None
                and (time.monotonic() - self._drive_last) <= _DRIVE_TIMEOUT
            ):
                speed, heading, flags = self._drive_target
                await self.droid.drive(speed, heading, flags)
                await asyncio.sleep(_DRIVE_RESEND)
        except Exception as exc:
            _LOGGER.warning("_drive_loop: error: %s", exc)
        finally:
            # Loop ended (dead-man, explicit stop, or error) → halt the droid.
            self._drive_target = None
            if self.droid.connected:
                try:
                    await self.droid.stop()
                except Exception as exc:
                    _LOGGER.debug("_drive_loop: stop on exit failed: %s", exc)

    async def async_stop_drive(self) -> None:
        """Stop driving now. The loop also halts on its next tick (dup stop is harmless)."""
        self._drive_target = None
        if self.droid.connected:
            try:
                await self.droid.stop()
            except Exception as exc:
                _LOGGER.debug("async_stop_drive: %s", exc)

    async def _reconnect_for_send(self) -> None:
        """Reconnect hook handed to the DroidClient for auto-wake on send.

        Guards against re-entering async_reconnect while a reconnect is already
        in progress: the reconnect lock is non-reentrant, so if a handshake/
        init write inside async_reconnect finds the link briefly down and calls
        back through here, awaiting the held lock would deadlock.  When a
        reconnect is already underway we raise instead — the caller's own retry/
        wait loop handles it.
        """
        if self._reconnect_lock.locked():
            raise HomeAssistantError("reconnect already in progress")
        await self.async_reconnect()

    async def _async_update_data(self) -> dict[str, Any]:
        """Poll battery and RSSI every 30s; binary sensor driven by is_connected."""
        _LOGGER.debug("_async_update_data: polling (connected=%s is_connected=%s)",
                      self.droid.connected, self.is_connected)

        current = dict(self.data or {})

        if not self.droid.connected:
            # Not connected — return current data without raising so the
            # coordinator keeps polling; it pushes to listeners on return.
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

        # Cancel post-connect sensor-stream supervisor
        if self._verify_task and not self._verify_task.done():
            self._verify_task.cancel()
        self._verify_task = None

        # Stop any active drive loop
        self._drive_target = None
        if self._drive_task and not self._drive_task.done():
            self._drive_task.cancel()
        self._drive_task = None

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
