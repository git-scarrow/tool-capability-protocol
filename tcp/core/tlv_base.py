"""Base classes for TLV encoders/decoders."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class TLVBase(ABC):
    """Simple base class for TLV helpers.

    Concrete implementations should provide :meth:`encode` and
    :meth:`decode` methods that operate on Python dictionaries and return
    raw bytes and vice‑versa.
    """

    @staticmethod
    @abstractmethod
    def encode(payload: Dict[str, Any]) -> bytes:
        """Encode payload into bytes."""
        raise NotImplementedError

    @staticmethod
    @abstractmethod
    def decode(data: bytes) -> Dict[str, Any]:
        """Decode payload from bytes."""
        raise NotImplementedError
