"""Helpers for computing signing surfaces.

The canonical bytes for signatures are the descriptor bytes with any
EVIDENCE (0x000B) TLVs removed.  The header's length and CRC32C are
recomputed to maintain canonical form.
"""
from __future__ import annotations

import copy
import struct
from typing import Tuple

import zlib

EVIDENCE_TYPE = 0x000B


def _parse_header(data: bytes) -> Tuple[bytearray, int]:
    if len(data) < 32:
        raise ValueError("descriptor too short")
    header = bytearray(data[:32])
    total_length = struct.unpack_from('<Q', header, 16)[0]
    return header, total_length


def canonical_bytes_without_evidence(descriptor_bytes: bytes) -> bytes:
    """Return descriptor bytes with all EVIDENCE TLVs removed."""
    header, total_length = _parse_header(descriptor_bytes)
    tlv_data = descriptor_bytes[32:total_length]
    out = bytearray()
    offset = 0
    while offset < len(tlv_data):
        t, f, l = struct.unpack_from('<HHI', tlv_data, offset)
        payload = tlv_data[offset : offset + 8 + l]
        if t != EVIDENCE_TYPE:
            out.extend(payload)
        offset += 8 + l
    new_total = 32 + len(out)
    struct.pack_into('<Q', header, 16, new_total)
    # zero CRC32C then compute
    struct.pack_into('<I', header, 28, 0)
    crc = zlib.crc32(header[:28]) & 0xFFFFFFFF
    struct.pack_into('<I', header, 28, crc)
    return bytes(header) + bytes(out)
