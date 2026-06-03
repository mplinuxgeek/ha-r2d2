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
    MSG_GET_MAIN_APP_VERSION, MSG_GET_BOOTLOADER_VERSION,
    MSG_GET_BOARD_REVISION, MSG_GET_MAC_ADDRESS, MSG_GET_SKU,
    MSG_CONFIGURE_COLLISION, COLLISION_DEFAULTS,
    MSG_ENABLE_BATTERY_STATE_NOTIFY,
    MSG_STOP_ANIMATION, MSG_IDLE_ANIMATIONS, MSG_GET_AUDIO_VOLUME,
    NOTIFY_SENSOR_STREAM, NOTIFY_COLLISION, NOTIFY_BATTERY_STATE,
    NOTIFY_WILL_SLEEP, NOTIFY_DID_SLEEP,
    FLAG_IS_RESPONSE, FLAG_HAS_TARGET_ID, FLAG_HAS_SOURCE_ID,
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
        # Pending request/response futures keyed by (did, cid, seq); resolved by
        # _process_packet when the matching response notification arrives.
        self._response_waiters: dict[tuple[int, int, int], asyncio.Future] = {}
        self.sensor_callback = None   # callable(dict) — receives live sensor data
        self.collision_callback = None  # callable(dict) — physical bump reported
        self.battery_state_callback = None  # callable(int) — battery state enum
        self.sleep_callback = None    # callable(str) — "will_sleep" / "did_sleep"
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
        self._cancel_response_waiters("reconnecting")

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
        self._cancel_response_waiters("disconnected")

    def _cancel_response_waiters(self, reason: str) -> None:
        """Fail any in-flight request/response futures (e.g. on disconnect)."""
        if not self._response_waiters:
            return
        for waiter in list(self._response_waiters.values()):
            if not waiter.done():
                waiter.set_exception(RuntimeError(f"response cancelled: {reason}"))
        self._response_waiters.clear()

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

    async def _send(self, msg, payload=None, label="", expect_response=False, timeout=5.0):
        """Send a command. If expect_response, wait for and return its response
        payload bytes (raising on a device error); otherwise return the bool
        write result as before."""
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

        seq = self._seq
        packet = build_packet(msg, payload, seq=seq)
        _LOGGER.debug("_send: seq=%d cmd='%s' packet=%s", seq, cmd, packet.hex())
        self._seq = (self._seq + 1) % _SEQ_MAX

        waiter: asyncio.Future | None = None
        key = (msg[1], msg[2], seq)  # (did, cid, seq) echoed back in the response
        if expect_response:
            waiter = asyncio.get_running_loop().create_future()
            self._response_waiters[key] = waiter

        result = await write_gatt(self._client, self._main_char.uuid, packet, cmd)
        if result:
            self._last_command_time = asyncio.get_running_loop().time()
            _LOGGER.debug("_send: '%s' OK", cmd)
        else:
            _LOGGER.warning("_send: '%s' FAILED (write_gatt returned False)", cmd)

        if not expect_response:
            return result
        if not result:
            self._response_waiters.pop(key, None)
            raise RuntimeError(f"{cmd}: write failed, no response expected")
        try:
            err, data = await asyncio.wait_for(waiter, timeout)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(f"{cmd}: timed out waiting for response") from exc
        finally:
            self._response_waiters.pop(key, None)
        if err != 0x00:
            raise RuntimeError(f"{cmd}: device returned error 0x{err:02x}")
        return data

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

        # Layout: FLAGS [TID] [SID] DID CID SEQ [ERR if response] DATA... CHK
        i = 0
        flags = body[i]; i += 1
        if flags & FLAG_HAS_TARGET_ID:
            i += 1
        if flags & FLAG_HAS_SOURCE_ID:
            i += 1
        if len(body) < i + 4:
            _LOGGER.debug("_process_packet: header truncated, discarding")
            return
        did, cid, seq = body[i], body[i + 1], body[i + 2]
        i += 3
        _LOGGER.debug(
            "_process_packet: flags=%02x did=%02x cid=%02x seq=%d body_len=%d",
            flags, did, cid, seq, len(body),
        )

        if flags & FLAG_IS_RESPONSE:
            # Response to one of our requests: [ERR] DATA... CHK
            err = body[i]; i += 1
            data = bytes(body[i:-1])  # strip CHK
            waiter = self._response_waiters.get((did, cid, seq))
            if waiter is not None and not waiter.done():
                waiter.set_result((err, data))
            else:
                _LOGGER.debug("_process_packet: response with no waiter (did=%02x cid=%02x seq=%d)", did, cid, seq)
            return

        payload = body[i:-1]  # async-notification payload (strip CHK)
        ident = (did, cid)
        if ident == NOTIFY_SENSOR_STREAM:
            _LOGGER.debug("_process_packet: sensor packet, payload_len=%d", len(payload))
            self._handle_sensor_packet(payload)
        elif ident == NOTIFY_COLLISION:
            self._handle_collision(payload)
        elif ident == NOTIFY_BATTERY_STATE:
            if payload and self.battery_state_callback:
                _LOGGER.debug("_process_packet: battery state notify -> %d", payload[0])
                self.battery_state_callback(payload[0])
        elif ident == NOTIFY_WILL_SLEEP:
            _LOGGER.debug("_process_packet: will-sleep notify")
            if self.sleep_callback:
                self.sleep_callback("will_sleep")
        elif ident == NOTIFY_DID_SLEEP:
            _LOGGER.debug("_process_packet: did-sleep notify")
            if self.sleep_callback:
                self.sleep_callback("did_sleep")
        else:
            _LOGGER.debug("_process_packet: unhandled async did=%02x cid=%02x", did, cid)

    def _handle_collision(self, payload) -> None:
        """Decode a collision_detected notification (18-byte payload)."""
        if not self.collision_callback:
            return
        if len(payload) < 18:
            _LOGGER.warning("_handle_collision: short payload (%d bytes): %s",
                            len(payload), bytes(payload).hex())
            return
        try:
            ax, ay, az, axis, px, py, pz, speed, ms = struct.unpack(
                ">3hB3hBL", bytes(payload[:18]))
        except struct.error as exc:
            _LOGGER.warning("_handle_collision: unpack error: %s", exc)
            return
        data = {
            "accel_x": round(ax / 4096, 4),
            "accel_y": round(ay / 4096, 4),
            "accel_z": round(az / 4096, 4),
            "x_axis": bool(axis & 1),
            "y_axis": bool(axis & 2),
            "power_x": px,
            "power_y": py,
            "power_z": pz,
            "speed": speed,
            "time": ms / 1000,
        }
        _LOGGER.debug("_handle_collision: %s", data)
        self.collision_callback(data)

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
    # Device info (System Info, DID 0x11) — request/response getters
    # ------------------------------------------------------------------

    @staticmethod
    def _format_version(data: bytes) -> str | None:
        """Decode a 3x uint16 version payload as 'major.minor.revision'."""
        if len(data) < 6:
            return None
        major, minor, revision = struct.unpack(">3H", data[:6])
        return f"{major}.{minor}.{revision}"

    @staticmethod
    def _decode_ascii(data: bytes) -> str | None:
        """Decode an ASCII payload (MAC / SKU), trimming padding."""
        text = bytes(data).decode("ascii", "ignore").replace("\x00", "").strip()
        return text or None

    async def get_main_app_version(self) -> str | None:
        data = await self._send(MSG_GET_MAIN_APP_VERSION, expect_response=True,
                                label="get_main_app_version")
        return self._format_version(data)

    async def get_bootloader_version(self) -> str | None:
        data = await self._send(MSG_GET_BOOTLOADER_VERSION, expect_response=True,
                                label="get_bootloader_version")
        return self._format_version(data)

    async def get_board_revision(self) -> int | None:
        data = await self._send(MSG_GET_BOARD_REVISION, expect_response=True,
                                label="get_board_revision")
        return data[0] if data else None

    @staticmethod
    def _format_mac(raw: str | None) -> str | None:
        """Normalise the droid's MAC (e.g. 'e4b40a7179b9') to 'E4:B4:..:B9'."""
        if not raw:
            return None
        hex_only = raw.replace(":", "").replace("-", "").strip().upper()
        if len(hex_only) == 12 and all(c in "0123456789ABCDEF" for c in hex_only):
            return ":".join(hex_only[i:i + 2] for i in range(0, 12, 2))
        return raw.upper()

    async def get_mac_address(self) -> str | None:
        data = await self._send(MSG_GET_MAC_ADDRESS, expect_response=True,
                                label="get_mac_address")
        return self._format_mac(self._decode_ascii(data))

    async def get_sku(self) -> str | None:
        data = await self._send(MSG_GET_SKU, expect_response=True, label="get_sku")
        return self._decode_ascii(data)

    async def fetch_device_info(self) -> dict:
        """Read the static identity fields once. Each is best-effort: a single
        failing getter doesn't abort the rest."""
        info: dict = {}
        getters = (
            ("sw_version", self.get_main_app_version),
            ("bootloader_version", self.get_bootloader_version),
            ("board_revision", self.get_board_revision),
            ("mac_address", self.get_mac_address),
            ("sku", self.get_sku),
        )
        for field, getter in getters:
            try:
                value = await getter()
            except Exception as exc:
                _LOGGER.debug("fetch_device_info: %s failed: %s", field, exc)
                continue
            if value is not None:
                info[field] = value
        _LOGGER.debug("fetch_device_info: %s", info)
        return info

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

    async def stop_animation(self):
        """Cut the currently-playing animation short."""
        return await self._send(MSG_STOP_ANIMATION, label="stop_animation")

    async def enable_idle_animations(self, enable=True):
        """Let the droid autonomously play idle fidget animations."""
        return await self._send(MSG_IDLE_ANIMATIONS, [int(bool(enable))],
                                label=f"idle_animations({enable})")

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

    async def get_volume(self) -> int | None:
        """Read the droid's current audio volume (0-255)."""
        data = await self._send(MSG_GET_AUDIO_VOLUME, expect_response=True, label="get_volume")
        return data[0] if data else None

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

    async def configure_collision_detection(self):
        """Arm accelerometer-based collision reporting."""
        result = await self._send(MSG_CONFIGURE_COLLISION, list(COLLISION_DEFAULTS),
                                  label="configure_collision")
        _LOGGER.info("configure_collision_detection: armed=%s params=%s",
                     bool(result), COLLISION_DEFAULTS)
        return result

    async def enable_battery_state_notify(self, enable=True):
        """Ask the droid to push battery state changes (charging/low/...)."""
        return await self._send(MSG_ENABLE_BATTERY_STATE_NOTIFY, [int(bool(enable))],
                                label=f"battery_state_notify({enable})")

    async def enable_all_sensors(self):
        _LOGGER.debug("enable_all_sensors: enabling accelerometer + extended sensors")
        await self.accelerometer()
        await self.enable_extended_sensors()
        # Collision + battery-state pushes ride the same stream; best-effort so a
        # failure here never blocks the core sensor arming (or the wake path).
        # Log at WARNING if arming fails so a silent collision config is visible.
        for label, coro in (("collision", self.configure_collision_detection),
                            ("battery_state_notify", self.enable_battery_state_notify)):
            try:
                await coro()
            except Exception as exc:
                _LOGGER.warning("enable_all_sensors: %s enable failed: %s", label, exc)
        _LOGGER.debug("enable_all_sensors: done")
