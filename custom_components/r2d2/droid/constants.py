import re

# BLE characteristics
CONNECT_CHAR = "00020005574f4f2053706865726f2121"
HANDLE_CHAR  = "00020002574f4f2053706865726f2121"
MAIN_CHAR    = "00010002574f4f2053706865726f2121"
BATTERY_CHAR = "00002a19-0000-1000-8000-00805f9b34fb"

# Handshake payload — "usetheforce...band"
MSG_CONNECTION = bytes([
    0x75, 0x73, 0x65, 0x74, 0x68,
    0x65, 0x66, 0x6F, 0x72, 0x63,
    0x65, 0x2E, 0x2E, 0x2E, 0x62,
    0x61, 0x6E, 0x64
])

# Command messages — [flags=0x0A, DID, CID]
MSG_INIT              = bytes([0x0A, 0x13, 0x0D])
MSG_OFF               = bytes([0x0A, 0x13, 0x01])
MSG_ROTATE            = bytes([0x0A, 0x17, 0x0F])
MSG_ANIMATION         = bytes([0x0A, 0x17, 0x05])
MSG_CARRIAGE          = bytes([0x0A, 0x17, 0x0D])
MSG_MOVE              = bytes([0x0A, 0x16, 0x07])
MSG_RESET_YAW         = bytes([0x0A, 0x16, 0x06])
MSG_STABILIZATION     = bytes([0x0A, 0x16, 0x0C])
MSG_ACCELEROMETER     = bytes([0x0A, 0x18, 0x00])  # set_sensor_streaming_mask
MSG_EXTENDED_SENSORS  = bytes([0x0A, 0x18, 0x0C])  # set_extended_sensor_streaming_mask
MSG_AUDIO             = bytes([0x0A, 0x1A, 0x07])
MSG_AUDIO_VOLUME      = bytes([0x0A, 0x1A, 0x08])
MSG_AUDIO_STOP        = bytes([0x0A, 0x1A, 0x0A])
MSG_LED               = bytes([0x0A, 0x1A, 0x0E])  # set_all_leds_with_16_bit_mask

# Collision detection (DID 0x18). configure_collision_detection payload is
# [method, x_thr, y_thr, x_speed, y_speed, dead_time]; method 1 = accelerometer.
MSG_CONFIGURE_COLLISION = bytes([0x0A, 0x18, 0x11])
COLLISION_DEFAULTS = (0x01, 90, 130, 90, 130, 1)  # spherov2's tested defaults

# Battery-state change notifications (DID 0x13).
MSG_ENABLE_BATTERY_STATE_NOTIFY = bytes([0x0A, 0x13, 0x05])

# Animatronic extras (DID 0x17).
MSG_STOP_ANIMATION    = bytes([0x0A, 0x17, 0x2B])
MSG_IDLE_ANIMATIONS   = bytes([0x0A, 0x17, 0x2C])  # payload [enable]

# Audio (DID 0x1A).
MSG_GET_AUDIO_VOLUME  = bytes([0x0A, 0x1A, 0x09])  # response: 1 byte (0-255)

# Async-notification identifiers (DID, CID) the droid pushes unprompted.
NOTIFY_SENSOR_STREAM = (0x18, 0x02)
NOTIFY_COLLISION     = (0x18, 0x12)
NOTIFY_BATTERY_STATE = (0x13, 0x06)
NOTIFY_WILL_SLEEP    = (0x13, 0x19)
NOTIFY_DID_SLEEP     = (0x13, 0x1A)

# Battery state enum (battery_state_changed_notify payload byte).
BATTERY_STATE_CHARGED      = 0
BATTERY_STATE_CHARGING     = 1
BATTERY_STATE_NOT_CHARGING = 2
BATTERY_STATE_OK           = 3
BATTERY_STATE_LOW          = 4
BATTERY_STATE_CRITICAL     = 5

# System-info getters (DID 0x11). These return a response packet whose payload
# carries the requested value — see DroidClient._send(expect_response=True).
MSG_GET_MAIN_APP_VERSION   = bytes([0x0A, 0x11, 0x00])  # 3x uint16: maj.min.rev
MSG_GET_BOOTLOADER_VERSION = bytes([0x0A, 0x11, 0x01])  # 3x uint16
MSG_GET_BOARD_REVISION     = bytes([0x0A, 0x11, 0x03])  # 1 byte
MSG_GET_MAC_ADDRESS        = bytes([0x0A, 0x11, 0x06])  # ASCII
MSG_GET_SKU                = bytes([0x0A, 0x11, 0x28])  # get_three_character_sku, ASCII

# Packet flag bit (in the FLAGS byte) marking a packet as a response to a
# request rather than an async notification.
FLAG_IS_RESPONSE      = 0x01
FLAG_HAS_TARGET_ID    = 0x10
FLAG_HAS_SOURCE_ID    = 0x20

# Packet framing
SOP     = 0x8D
EOP     = 0xD8
ESC     = 0xAB
ESC_ESC = 0x23
ESC_SOP = 0x05
ESC_EOP = 0x50

# Droid BLE name pattern: e.g. D2-79B9 (last 4 hex = last 2 MAC octets)
DROID_PATTERN = re.compile(r'^[A-Z]\d-([0-9A-Fa-f]{4})$')
