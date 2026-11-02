"""Raw Govee LAN packets (``ptReal``) that the stock library does not expose.

Every packet is 20 bytes: 19 payload bytes (zero padded) and a XOR checksum.
"""

from __future__ import annotations

from collections.abc import Iterable

PACKET_PAYLOAD = 19


def xor_checksum(data: bytes) -> int:
    """Return the XOR of all bytes."""
    result = 0
    for byte in data:
        result ^= byte
    return result


def finish_packet(payload: bytes) -> bytes:
    """Pad to 19 bytes and append the checksum."""
    if len(payload) > PACKET_PAYLOAD:
        raise ValueError(f"payload too long: {len(payload)} bytes")
    padded = payload.ljust(PACKET_PAYLOAD, b"\x00")
    return padded + bytes([xor_checksum(padded)])


def segment_mask(segments: Iterable[int], size: int = 7) -> bytes:
    """Little-endian bitmask: segment 0 = byte 0 bit 0, segment 8 = byte 1 bit 0."""
    mask = bytearray(size)
    for segment in segments:
        if not 0 <= segment < size * 8:
            raise ValueError(f"segment {segment} out of range")
        mask[segment // 8] |= 1 << (segment % 8)
    return bytes(mask)


def segment_color_packet(rgb: tuple[int, int, int], segments: Iterable[int]) -> bytes:
    """``33 05 15 01 R G B 00x5 <mask>``: set a colour on several segments at once."""
    red, green, blue = (max(0, min(255, int(c))) for c in rgb)
    payload = (
        bytes([0x33, 0x05, 0x15, 0x01, red, green, blue])
        + bytes(5)
        + segment_mask(segments)
    )
    return finish_packet(payload)
