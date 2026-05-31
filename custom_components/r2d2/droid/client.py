import asyncio
import logging
import struct

from bleak import BleakClient

from .constants import (
    CONNECT_CHAR, HANDLE_CHAR, MAIN_CHAR, BATTERY_CHAR,
    MSG_CONNECTION, MSG_INIT, MSG_OFF, MSG_ROTATE, MSG_ANIMATION,
    MSG_CARRIAGE, MSG_MOVE, MSG_RESET_YAW, MSG_STABILIZATION,
    MSG_ACCELEROMETER, MSG_EXTENDED_SENSORS,
    MSG_AUDIO, MSG_AUDIO_VOLUME, MSG_AUDIO_STOP, MSG_LED,
    SOP, EOP, ESC,
)
from .data import ANIMATIONS, AUDIO, AudioMode, DriveFlags, LegAction
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

    IDLE_SLEEP_TIMEOUT = 600  # seconds before assuming droid needs re-init

    def __init__(self, address: str) -> None:
        self.address = address
        self._client: BleakClient | None = None
        self._main_char = None
        self._asleep = False
        self._intentional_disconnect = False
        self._last_command_time: float | None = None
        self._seq = 0
        self._packet_buffer: list[int] = []
        self.sensor_callback = None  # callable(dict) — receives live sensor data

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def connect(self, ble_device) -> None:
        """Connect to the droid using a BLEDevice obtained from HA's bluetooth stack."""
        # Reset all per-session state so a reconnect on the same instance
        # behaves identically to a fresh DroidClient (which is what a reload gets).
        self._main_char = None
        self._last_command_time = None  # prevents ensure_awake recursion
        self._asleep = False
        self._intentional_disconnect = False
        self._seq = 0
        self._packet_buffer.clear()
        self._client = BleakClient(ble_device, disconnected_callback=self._on_disconnect)
        await self._client.connect()
        if not self._client.is_connected:
            raise RuntimeError(f"Failed to connect to {self.address}")
        _LOGGER.info("Connected to %s", self.address)

        services = self._client.services

        handle_char = services.get_characteristic(HANDLE_CHAR)
        if handle_char and "notify" in handle_char.properties:
            try:
                await self._client.start_notify(HANDLE_CHAR, self._on_notification)
                _LOGGER.debug("Subscribed to notifications on %s", HANDLE_CHAR)
            except Exception as exc:
                _LOGGER.warning("Failed to subscribe to %s: %s", HANDLE_CHAR, exc)

        if not await write_gatt(self._client, CONNECT_CHAR, MSG_CONNECTION, "handshake"):
            await self._client.disconnect()
            raise RuntimeError("Handshake failed")

        self._main_char = services.get_characteristic(MAIN_CHAR)
        if not self._main_char:
            await self._client.disconnect()
            raise RuntimeError(f"MAIN_CHAR {MAIN_CHAR} not found")

        if "notify" in self._main_char.properties:
            try:
                await self._client.start_notify(MAIN_CHAR, self._on_notification)
                _LOGGER.debug("Subscribed to notifications on %s", MAIN_CHAR)
            except Exception as exc:
                _LOGGER.warning("MAIN_CHAR notify failed: %s", exc)

    async def disconnect(self) -> None:
        self._intentional_disconnect = True
        if self._client and self._client.is_connected:
            _LOGGER.info("Disconnecting from %s", self.address)
            await self._client.disconnect()
            _LOGGER.info("Disconnected from %s", self.address)

    def _on_disconnect(self, client) -> None:
        if not self._intentional_disconnect:
            self._asleep = True
            _LOGGER.info("Droid %s dropped connection (went to sleep?).", self.address)
        self._intentional_disconnect = False

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def ensure_awake(self) -> None:
        if self._last_command_time is not None:
            loop = asyncio.get_running_loop()
            idle = loop.time() - self._last_command_time
            if idle > self.IDLE_SLEEP_TIMEOUT:
                _LOGGER.info("Droid idle %.0fs, sending init...", idle)
                await self.init()

    async def _send(self, msg, payload=None, label=""):
        await self.ensure_awake()
        if self._main_char is None:
            raise RuntimeError("Not connected — main characteristic unavailable")
        packet = build_packet(msg, payload, seq=self._seq)
        self._seq = (self._seq + 1) % _SEQ_MAX
        result = await write_gatt(self._client, self._main_char.uuid, packet, label or msg.hex())
        if result:
            self._last_command_time = asyncio.get_running_loop().time()
        return result

    # ------------------------------------------------------------------
    # Notification handling — packets arrive fragmented (1 byte at a time)
    # ------------------------------------------------------------------

    def _on_notification(self, sender, data) -> None:
        _LOGGER.debug("BLE notification from %s: %d bytes: %s", sender, len(data), data.hex())
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
            _LOGGER.debug("Packet too short (%d bytes), discarding", len(packet))
            return

        body = unescape_packet(packet)
        if len(body) < 4:
            _LOGGER.debug("Unescaped body too short (%d bytes), discarding", len(body))
            return

        flags, did, cid, seq = body[0], body[1], body[2], body[3]
        _LOGGER.debug("Packet flags=%02x did=%02x cid=%02x seq=%s body_len=%d", flags, did, cid, seq, len(body))

        if did == 0x18 and cid == 0x02:
            self._handle_sensor_packet(body[4:-1])  # strip CHK

    def _handle_sensor_packet(self, payload) -> None:
        n = len(payload) // 4
        if n == 0:
            return
        try:
            floats = [round(struct.unpack('>f', bytes(payload[j*4:j*4+4]))[0], 4) for j in range(n)]
        except struct.error:
            return
        _LOGGER.debug("Sensor floats (%d): %s", n, floats)
        if not self.sensor_callback:
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
            return data[0]
        except Exception as exc:
            _LOGGER.warning("Battery read failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Movement
    # ------------------------------------------------------------------

    async def rotate(self, degrees):
        """Rotate the dome. degrees: float, -160 to 180."""
        return await self._send(MSG_ROTATE, degrees_to_bytes(degrees), label=f"rotate({degrees})")

    async def tripod(self):
        """Switch to tripod (3-leg) stance."""
        return await self._send(MSG_CARRIAGE, [LegAction.TRIPOD], label="tripod")

    async def bipod(self):
        """Switch to bipod (2-leg) stance."""
        return await self._send(MSG_CARRIAGE, [LegAction.BIPOD], label="bipod")

    async def waddle(self, enable=True):
        """Enable/disable waddle (bipod walking gait)."""
        action = LegAction.WADDLE if enable else LegAction.STOP
        return await self._send(MSG_CARRIAGE, [action], label=f"waddle({enable})")

    async def drive(self, speed, heading=0, flags=DriveFlags.FORWARD):
        """Drive. speed: 0-255, heading: 0-359 degrees, flags: DriveFlags."""
        heading = int(heading) % 360
        payload = [speed, (heading >> 8) & 0xFF, heading & 0xFF, flags]
        return await self._send(MSG_MOVE, payload, label=f"drive(speed={speed}, heading={heading})")

    async def stop(self):
        """Stop driving."""
        return await self.drive(0, 0)

    async def reset_yaw(self):
        """Reset heading to current orientation."""
        return await self._send(MSG_RESET_YAW, label="reset_yaw")

    async def set_stabilization(self, index=1):
        """Set stabilization mode. 0=none, 1=full (default), 2=pitch, 3=roll, 4=yaw, 5=speed+yaw."""
        return await self._send(MSG_STABILIZATION, [index], label=f"stabilization({index})")

    # ------------------------------------------------------------------
    # Animations
    # ------------------------------------------------------------------

    async def animate(self, animation):
        """Play animation by name (str) or integer ID. See ANIMATIONS dict."""
        if isinstance(animation, str):
            if animation not in ANIMATIONS:
                raise ValueError(f"Unknown animation '{animation}'. Known: {list(ANIMATIONS)}")
            anim_id = ANIMATIONS[animation]
        else:
            anim_id = int(animation)
        payload = list(struct.pack('>H', anim_id))
        return await self._send(MSG_ANIMATION, payload, label=f"animate({animation})")

    # ------------------------------------------------------------------
    # Audio
    # ------------------------------------------------------------------

    async def play_audio(self, sound, mode=AudioMode.PLAY_IMMEDIATELY):
        """Play audio by name (str) or integer ID. mode: AudioMode constant."""
        if isinstance(sound, str):
            if sound not in AUDIO:
                raise ValueError(f"Unknown sound '{sound}'. Known: {list(AUDIO)}")
            sound_id = AUDIO[sound]
        else:
            sound_id = int(sound)
        payload = list(struct.pack('>H', sound_id)) + [mode]
        return await self._send(MSG_AUDIO, payload, label=f"audio({sound})")

    async def set_volume(self, volume):
        """Set audio volume (0-255)."""
        return await self._send(MSG_AUDIO_VOLUME, [max(0, min(255, int(volume)))], label=f"volume({volume})")

    async def stop_audio(self):
        """Stop all audio playback."""
        return await self._send(MSG_AUDIO_STOP, label="stop_audio")

    # ------------------------------------------------------------------
    # LEDs
    # ------------------------------------------------------------------

    async def set_holo_projector(self, brightness):
        """Set dome holo projector brightness (0-255)."""
        return await self._send(MSG_LED, [0x00, 0x80, max(0, min(255, int(brightness)))], label=f"holo({brightness})")

    async def set_front_led(self, r, g, b):
        """Set front LED colour (0-255 each)."""
        return await self._send(MSG_LED, [0x00, 0x07, r, g, b], label=f"front_led({r},{g},{b})")

    async def set_back_led(self, r, g, b):
        """Set back LED colour (0-255 each)."""
        return await self._send(MSG_LED, [0x00, 0x70, r, g, b], label=f"back_led({r},{g},{b})")

    async def set_logic_display(self, brightness):
        """Set logic display brightness (0-255)."""
        return await self._send(MSG_LED, [0x00, 0x08, max(0, min(255, int(brightness)))], label=f"logic({brightness})")

    async def set_all_leds(self, r, g, b):
        """Set all main LEDs (front + back) to same colour."""
        return await self._send(MSG_LED, [0x00, 0x77, r, g, b, r, g, b], label=f"all_leds({r},{g},{b})")

    # ------------------------------------------------------------------
    # Sensors
    # ------------------------------------------------------------------

    async def accelerometer(self):
        """Enable standard sensor stream: IMU (pitch/roll/yaw) + accelerometer (x/y/z)."""
        return await self._send(MSG_ACCELEROMETER,
            [0x00, 0x96, 0x00, 0x00, 0x07, 0xe0, 0x78], label="accelerometer")

    async def enable_extended_sensors(self):
        """Enable extended sensor stream: gyroscope (x/y/z) + dome head angle."""
        mask = 0x4000000 | 0x2000000 | 0x1000000 | 0x800000
        payload = list(struct.pack('>I', mask))
        return await self._send(MSG_EXTENDED_SENSORS, payload, label="extended_sensors")

    async def enable_all_sensors(self):
        """Enable all sensor streams."""
        await self.accelerometer()
        await self.enable_extended_sensors()
