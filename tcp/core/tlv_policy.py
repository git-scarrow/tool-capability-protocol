"""Implementation of POLICY (0x000C) TLV."""
from __future__ import annotations

from typing import Any, Dict

import cbor2

from .tlv_base import TLVBase


class PolicyTLV(TLVBase):
    """Simple canonical CBOR encoder/decoder for POLICY TLV."""

    @staticmethod
    def encode(payload: Dict[str, Any]) -> bytes:
        return cbor2.dumps(payload, canonical=True)

    @staticmethod
    def decode(data: bytes) -> Dict[str, Any]:
        return cbor2.loads(data)
