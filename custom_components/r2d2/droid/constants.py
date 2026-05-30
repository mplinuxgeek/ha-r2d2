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

# Packet framing
SOP     = 0x8D
EOP     = 0xD8
ESC     = 0xAB
ESC_ESC = 0x23
ESC_SOP = 0x05
ESC_EOP = 0x50

# Droid BLE name pattern: e.g. D2-79B9 (last 4 hex = last 2 MAC octets)
DROID_PATTERN = re.compile(r'^[A-Z]\d-([0-9A-Fa-f]{4})$')
