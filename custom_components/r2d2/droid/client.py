import asyncio
import logging
import struct

from bleak import BleakClient
from bleak_retry_connector import establish_connection

from .constants import (
    CONNECT_CHAR, HANDLE_CHAR, MAIN_CHAR, BATTERY_CHAR,
    MSG_CONNECTION, MSG_INIT, MSG_OFF, MSG_ROTATE, MSG_ANIMATION,
    MSG_CARRIAGE, MSG_MOVE, MSG_RESET_YAW, MSG_STABILIZATION,
    MSG_ACCELEROMETER, MSG_EXTENDED_SENSORS,
    MSG_AUDIO, MSG_AUDIO_VOLUME, MSG_AUDIO_STOP, MSG_LED,
    SOP, EOP,
)
from .data import AUDIO, AudioMode, DEFAULT_MODEL, DriveFlags, LegAction, animations_for
from .protocol import build_packet, degrees_to_bytes, write_gatt, unescape_packet

_LOGGER = logging.getLogger(__name__)

_SEQ_MAX = 140


class DroidClient:
    """
    HA-adapted async client for controlling an R2D2 / Q5 droid over BLE.

    Usage:
        client = DroidClient("E4:B4:0A:71:79:B9")
        await client.connect(ble_device)   # ble_device from HA bluetooth stack
        await client.init()
        await client.rotate(90)
        await client.disconnect()
    """

    IDLE_SLEEP_TIMEOUT = 290  # seconds idle before droid sleeps (sensor stream
                              # stops ~5 min) → next command must re-init + re-arm sensors
    WAKE_SETTLE = 2.0         # seconds for a just-woken droid's motor controller to come
                              # online before the first command (else stance/move is dropped)

    def __init__(self, address: str, model: str | None = None) -> None:
        self.address = address
        self.model = model or DEFAULT_MODEL
        # Animation IDs are model-specific (emote IDs 7-24 differ between
        # R2-D2 and R2-Q5); pick the right name→ID table up front.
        self.animations = animations_for(self.model)
        self._client: BleakClient | None = None
        self._main_char = None
        self._intentional_disconnect = False
        self._last_command_time: float | None = None
        self._waking = False          # guard: re-entrancy in ensure_awake
        self._stance: str | None = None  # last commanded leg stance (tripod/bipod/waddle)
        self._seq = 0
        self._packet_buffer: list[int] = []
        self.sensor_callback = None   # callable(dict) — receives live sensor data
        self.reconnect_hook = None    # async callable() — set by coordinator

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def connect(self, ble_device) -> None:
        """Connect to the droid using a BLEDevice obtained from HA's bluetooth stack."""
        _LOGGER.debug("connect: resetting session state for %s", self.address)
        self._main_char = None
        self._last_command_time = None  # prevents ensure_awake recursion
        self._intentional_disconnect = False
        self._stance = None             # stance unknown after a fresh connect
        self._seq = 0
        self._packet_buffer.clear()

        _LOGGER.debug("connect: calling establish_connection for %s (device=%s)", self.address, ble_device)
        self._client = await establish_connection(
            BleakClient,
            ble_device,
            self.address,
            disconnected_callback=self._on_disconnect,
        )
        _LOGGER.info("connect: established BLE connection to %s", self.address)
        # Brief pause so the droid firmware is ready to accept the handshake.
        await asyncio.sleep(0.5)

        services = self._client.services
        _LOGGER.debug("connect: discovered %d services on %s", len(list(services)), self.address)

        handle_char = services.get_characteristic(HANDLE_CHAR)
        if handle_char and "notify" in handle_char.properties:
            try:
                await self._client.start_notify(HANDLE_CHAR, self._on_notification)
                _LOGGER.debug("connect: subscribed to HANDLE_CHAR notifications (%s)", HANDLE_CHAR)
            except Exception as exc:
                _LOGGER.warning("connect: failed to subscribe to HANDLE_CHAR %s: %s", HANDLE_CHAR, exc)
        else:
            _LOGGER.debug("connect: HANDLE_CHAR not found or not notifiable")

        _LOGGER.debug("connect: sending handshake to CONNECT_CHAR")
        if not await write_gatt(self._client, CONNECT_CHAR, MSG_CONNECTION, "handshake"):
            await self._client.disconnect()
            raise RuntimeError("Handshake failed")
        _LOGGER.debug("connect: handshake OK")

        self._main_char = services.get_characteristic(MAIN_CHAR)
        if not self._main_char:
            await self._client.disconnect()
            raise RuntimeError(f"MAIN_CHAR {MAIN_CHAR} not found")
        _LOGGER.debug("connect: found MAIN_CHAR (%s), properties=%s", MAIN_CHAR, self._main_char.properties)

        if "notify" in self._main_char.properties:
            try:
                await self._client.start_notify(MAIN_CHAR, self._on_notification)
                _LOGGER.debug("connect: subscribed to MAIN_CHAR notifications")
            except Exception as exc:
                _LOGGER.warning("connect: MAIN_CHAR notify failed: %s", exc)
        else:
            _LOGGER.warning("connect: MAIN_CHAR does not support notify — sensor data will not work")

    async def disconnect(self) -> None:
        self._intentional_disconnect = True
        if self._client and self._client.is_connected:
            _LOGGER.info("disconnect: disconnecting from %s", self.address)
            await self._client.disconnect()
            _LOGGER.info("disconnect: done")

    def _on_disconnect(self, client) -> None:
        if not self._intentional_disconnect:
            _LOGGER.info("_on_disconnect: %s dropped connection (sleep/power-off)", self.address)
        else:
            _LOGGER.debug("_on_disconnect: intentional disconnect from %s", self.address)
        self._intentional_disconnect = False

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    @property
    def stance(self) -> str | None:
        """Last commanded leg stance: 'tripod', 'bipod', 'waddle', or None if unknown."""
        return self._stance

    async def ensure_awake(self) -> None:
        # _waking guards re-entrancy: init() and enable_all_sensors() below each
        # go through _send → ensure_awake, which would otherwise recurse forever
        # (the idle window is still stale until the first write completes).
        if self._waking or self._last_command_time is None:
            return
        loop = asyncio.get_running_loop()
        idle = loop.time() - self._last_command_time
        _LOGGER.debug("ensure_awake: idle=%.1fs (threshold=%ds)", idle, self.IDLE_SLEEP_TIMEOUT)
        if idle <= self.IDLE_SLEEP_TIMEOUT:
            return
        # Droid has likely slept: it stops the sensor stream on idle-sleep but
        # keeps BLE up and auto-wakes on the next command.  Re-init AND re-arm
        # the sensors so live data resumes — the command itself wakes the motors
        # but never restarts streaming.
        _LOGGER.info("ensure_awake: idle %.0fs > threshold, re-init + re-arm sensors", idle)
        self._waking = True
        try:
            await self.init()
            await self.enable_all_sensors()
            # Let the motor controller come online before the caller's command
            # lands — a stance/move sent immediately after wake is acked but
            # dropped (the dome/animation system boots first, motors lag).
            await asyncio.sleep(self.WAKE_SETTLE)
        finally:
            self._waking = False

    async def _send(self, msg, payload=None, label=""):
        cmd = label or msg.hex()
        if not self.connected:
            if self.reconnect_hook:
                _LOGGER.info("_send: not connected, auto-waking before '%s'", cmd)
                try:
                    await self.reconnect_hook()
                    _LOGGER.debug("_send: reconnect_hook succeeded, proceeding with '%s'", cmd)
                except Exception as exc:
                    _LOGGER.info(
                        "_send: reconnect_hook failed (%s), waiting up to 45s for BLE advertisement...", exc
                    )
                    for tick in range(9):      # 9 × 5 s = 45 s
                        await asyncio.sleep(5)
                        if self.connected:
                            _LOGGER.info("_send: droid came online after %ds, proceeding with '%s'", (tick + 1) * 5, cmd)
                            break
                    else:
                        raise RuntimeError("Timed out waiting for droid to come online")
            else:
                raise RuntimeError("Not connected — main characteristic unavailable")

        await self.ensure_awake()

        if self._main_char is None:
            raise RuntimeError("Not connected — main characteristic unavailable")

        packet = build_packet(msg, payload, seq=self._seq)
        _LOGGER.debug("_send: seq=%d cmd='%s' packet=%s", self._seq, cmd, packet.hex())
        self._seq = (self._seq + 1) % _SEQ_MAX
        result = await write_gatt(self._client, self._main_char.uuid, packet, cmd)
        if result:
            self._last_command_time = asyncio.get_running_loop().time()
            _LOGGER.debug("_send: '%s' OK", cmd)
        else:
            _LOGGER.warning("_send: '%s' FAILED (write_gatt returned False)", cmd)
        return result

    # ------------------------------------------------------------------
    # Notification handling — packets arrive fragmented (1 byte at a time)
    # ------------------------------------------------------------------

    def _on_notification(self, sender, data) -> None:
        _LOGGER.debug("_on_notification: %d bytes from %s: %s", len(data), sender, data.hex())
        # Raw 0xD8 only appears as EOP (payload 0xD8 is always escaped as [0xAB, 0x50])
        self._packet_buffer.extend(data)

        while True:
            try:
                sop = self._packet_buffer.index(SOP)
            except ValueError:
                self._packet_buffer.clear()
                break
            if sop > 0:
                del self._packet_buffer[:sop]

            try:
                eop = self._packet_buffer.index(EOP)
            except ValueError:
                break  # Incomplete packet — wait for more bytes

            packet = bytes(self._packet_buffer[:eop + 1])
            del self._packet_buffer[:eop + 1]
            self._process_packet(packet)

    def _process_packet(self, packet) -> None:
        if len(packet) < 6:
            _LOGGER.debug("_process_packet: too short (%d bytes), discarding", len(packet))
            return

        body = unescape_packet(packet)
        if len(body) < 4:
            _LOGGER.debug("_process_packet: unescaped body too short (%d bytes), discarding", len(body))
            return

        flags, did, cid, seq = body[0], body[1], body[2], body[3]
        _LOGGER.debug(
            "_process_packet: flags=%02x did=%02x cid=%02x seq=%d body_len=%d",
            flags, did, cid, seq, len(body),
        )

        if did == 0x18 and cid == 0x02:
            _LOGGER.debug("_process_packet: sensor packet, payload_len=%d", len(body) - 5)
            self._handle_sensor_packet(body[4:-1])  # strip CHK
        else:
            _LOGGER.debug("_process_packet: unhandled did=%02x cid=%02x (not a sensor packet)", did, cid)

    def _handle_sensor_packet(self, payload) -> None:
        n = len(payload) // 4
        if n == 0:
            _LOGGER.debug("_handle_sensor_packet: empty payload")
            return
        try:
            floats = [round(struct.unpack('>f', bytes(payload[j*4:j*4+4]))[0], 4) for j in range(n)]
        except struct.error as exc:
            _LOGGER.debug("_handle_sensor_packet: unpack error: %s", exc)
            return
        _LOGGER.debug("_handle_sensor_packet: %d floats: %s", n, floats)
        if not self.sensor_callback:
            _LOGGER.debug("_handle_sensor_packet: no sensor_callback set, discarding")
            return
        out: dict = {'type': 'sensor'}
        if n >= 6:
            out['imu']   = {'pitch': floats[0], 'roll': floats[1], 'yaw': floats[2]}
            out['accel'] = {'x': floats[3], 'y': floats[4], 'z': floats[5]}
        if n >= 14:
            out['head_angle'] = floats[10]
            out['gyro'] = {'x': floats[11], 'y': floats[12], 'z': floats[13]}
        self.sensor_callback(out)

    # ------------------------------------------------------------------
    # System
    # ------------------------------------------------------------------

    async def init(self):
        """Send initialisation command."""
        return await self._send(MSG_INIT, label="init")

    async def off(self):
        """Power off the droid."""
        return await self._send(MSG_OFF, label="off")

    async def battery(self) -> int | None:
        """Read battery percentage (0-100) via standard BLE Battery Service."""
        try:
            data = await self._client.read_gatt_char(BATTERY_CHAR)
            level = data[0]
            _LOGGER.debug("battery: %d%%", level)
            return level
        except Exception as exc:
            _LOGGER.warning("battery: read failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Movement
    # ------------------------------------------------------------------

    async def rotate(self, degrees):
        """Rotate the dome. degrees: float, -160 to 180."""
        return await self._send(MSG_ROTATE, degrees_to_bytes(degrees), label=f"rotate({degrees})")

    async def tripod(self):
        result = await self._send(MSG_CARRIAGE, [LegAction.TRIPOD], label="tripod")
        self._stance = "tripod"
        return result

    async def bipod(self):
        result = await self._send(MSG_CARRIAGE, [LegAction.BIPOD], label="bipod")
        self._stance = "bipod"
        return result

    async def waddle(self, enable=True):
        action = LegAction.WADDLE if enable else LegAction.STOP
        result = await self._send(MSG_CARRIAGE, [action], label=f"waddle({enable})")
        # Waddle leaves the legs in the waddle config; stopping it is ambiguous.
        self._stance = "waddle" if enable else None
        return result

    async def drive(self, speed, heading=0, flags=DriveFlags.FORWARD):
        heading = int(heading) % 360
        payload = [speed, (heading >> 8) & 0xFF, heading & 0xFF, flags]
        return await self._send(MSG_MOVE, payload, label=f"drive(speed={speed}, heading={heading})")

    async def stop(self):
        return await self.drive(0, 0)

    async def reset_yaw(self):
        return await self._send(MSG_RESET_YAW, label="reset_yaw")

    async def set_stabilization(self, index=1):
        return await self._send(MSG_STABILIZATION, [index], label=f"stabilization({index})")

    # ------------------------------------------------------------------
    # Animations
    # ------------------------------------------------------------------

    async def animate(self, animation):
        if isinstance(animation, str):
            if animation not in self.animations:
                raise ValueError(
                    f"Unknown animation '{animation}' for {self.model}. "
                    f"Known: {list(self.animations)}"
                )
            anim_id = self.animations[animation]
        else:
            anim_id = int(animation)
        payload = list(struct.pack('>H', anim_id))
        return await self._send(MSG_ANIMATION, payload, label=f"animate({animation})")

    # ------------------------------------------------------------------
    # Audio
    # ------------------------------------------------------------------

    async def play_audio(self, sound, mode=AudioMode.PLAY_IMMEDIATELY):
        if isinstance(sound, str):
            if sound not in AUDIO:
                raise ValueError(f"Unknown sound '{sound}'. Known: {list(AUDIO)}")
            sound_id = AUDIO[sound]
        else:
            sound_id = int(sound)
        payload = list(struct.pack('>H', sound_id)) + [mode]
        return await self._send(MSG_AUDIO, payload, label=f"audio({sound})")

    async def set_volume(self, volume):
        return await self._send(MSG_AUDIO_VOLUME, [max(0, min(255, int(volume)))], label=f"volume({volume})")

    async def stop_audio(self):
        return await self._send(MSG_AUDIO_STOP, label="stop_audio")

    # ------------------------------------------------------------------
    # LEDs
    # ------------------------------------------------------------------

    async def set_holo_projector(self, brightness):
        return await self._send(MSG_LED, [0x00, 0x80, max(0, min(255, int(brightness)))], label=f"holo({brightness})")

    async def set_front_led(self, r, g, b):
        return await self._send(MSG_LED, [0x00, 0x07, r, g, b], label=f"front_led({r},{g},{b})")

    async def set_back_led(self, r, g, b):
        return await self._send(MSG_LED, [0x00, 0x70, r, g, b], label=f"back_led({r},{g},{b})")

    async def set_logic_display(self, brightness):
        return await self._send(MSG_LED, [0x00, 0x08, max(0, min(255, int(brightness)))], label=f"logic({brightness})")

    async def set_all_leds(self, r, g, b):
        return await self._send(MSG_LED, [0x00, 0x77, r, g, b, r, g, b], label=f"all_leds({r},{g},{b})")

    # ------------------------------------------------------------------
    # Sensors
    # ------------------------------------------------------------------

    async def accelerometer(self):
        return await self._send(MSG_ACCELEROMETER,
            [0x00, 0x96, 0x00, 0x00, 0x07, 0xe0, 0x78], label="accelerometer")

    async def enable_extended_sensors(self):
        mask = 0x4000000 | 0x2000000 | 0x1000000 | 0x800000
        payload = list(struct.pack('>I', mask))
        return await self._send(MSG_EXTENDED_SENSORS, payload, label="extended_sensors")

    async def enable_all_sensors(self):
        _LOGGER.debug("enable_all_sensors: enabling accelerometer + extended sensors")
        await self.accelerometer()
        await self.enable_extended_sensors()
        _LOGGER.debug("enable_all_sensors: done")
