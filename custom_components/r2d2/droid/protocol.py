import logging
import struct

from bleak.exc import BleakError

from .constants import SOP, EOP, ESC, ESC_ESC, ESC_SOP, ESC_EOP

_LOGGER = logging.getLogger(__name__)


def build_packet(msg, payload=None, seq=0):
    """Build a framed, escaped packet. seq is per-Droid and passed in."""
    if payload is None:
        payload = []
    body = list(msg) + [seq] + list(payload)
    body.append(_calculate_chk(body))

    encoded = []
    for byte in body:
        if byte == ESC:
            encoded += [ESC, ESC_ESC]
        elif byte == SOP:
            encoded += [ESC, ESC_SOP]
        elif byte == EOP:
            encoded += [ESC, ESC_EOP]
        else:
            encoded.append(byte)

    return bytes([SOP] + encoded + [EOP])


def _calculate_chk(buff):
    return (~sum(buff) % 256) & 0xFF


def degrees_to_bytes(degrees, fmt='FLOAT'):
    if fmt == 'FLOAT':
        return list(struct.pack('>f', degrees))
    elif fmt == 'INTEGER':
        return list(struct.pack('>i', degrees))
    raise ValueError(f"Unsupported format: {fmt}")


async def write_gatt(client, char_uuid, packet, label):
    """Write packet to GATT characteristic, trying response=True then False."""
    for response in (True, False):
        try:
            await client.write_gatt_char(char_uuid, packet, response=response)
            _LOGGER.debug("[OK] %s (response=%s)", label, response)
            return True
        except Exception as e:
            _LOGGER.debug("[FAIL] %s (response=%s): %s", label, response, e)
    _LOGGER.error("Failed to send GATT write: %s", label)
    return False


def unescape_packet(packet):
    """Unescape body bytes between SOP and EOP, return list."""
    body, i = [], 1
    while i < len(packet) - 1:
        if packet[i] == ESC and i + 1 < len(packet) - 1:
            nxt = packet[i + 1]
            body.append(ESC if nxt == ESC_ESC else SOP if nxt == ESC_SOP else EOP)
            i += 2
        else:
            body.append(packet[i])
            i += 1
    return body
