"""TCP descriptor implementation with binary serialization."""

import copy
import hashlib
import struct
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    import cbor2
except ImportError:
    raise ImportError("cbor2 required: pip install cbor2")

try:
    import crc32c
except ImportError:
    # Fallback to pure Python implementation
    import zlib
    def crc32c_fallback(data: bytes) -> int:
        # Note: This is NOT true CRC32C, just for development
        # Production should use hardware-accelerated crc32c library
        warnings.warn("Using fallback CRC (not true CRC32C). Install 'crc32c' for production.")
        return zlib.crc32(data) & 0xFFFFFFFF
    crc32c = type('Module', (), {'crc32c': crc32c_fallback})()

from .errors import *
from .types import *


@dataclass
class TCPHeader:
    """TCP binary descriptor header (32 bytes)."""
    magic: bytes = b'TCAP'
    version: int = 0x01
    endian_flags: int = 0x01  # bit0=1 for little-endian
    protocol_type: int = ProtocolType.CLI
    security_level: int = SecurityLevel.SAFE
    tool_id_hint: int = 0
    total_length: int = 32  # Minimum size (header only)
    security_flags: int = 0
    header_crc32c: int = 0
    
    def to_bytes(self, include_crc: bool = True) -> bytes:
        """Serialize header to 32 bytes."""
        # Pack first 28 bytes (everything except CRC)
        header_no_crc = struct.pack(
            '<4sBBBBQQI',
            self.magic,
            self.version,
            self.endian_flags,
            self.protocol_type,
            self.security_level,
            self.tool_id_hint,
            self.total_length,
            self.security_flags
        )
        
        if include_crc:
            # Append CRC32C
            return header_no_crc + struct.pack('<I', self.header_crc32c)
        else:
            # Return without CRC (for CRC computation)
            return header_no_crc
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'TCPHeader':
        """Parse header from 32 bytes."""
        if len(data) < 32:
            raise E_MAGIC("Header too short")
        
        # Unpack all fields
        (magic, version, endian_flags, protocol_type, security_level,
         tool_id_hint, total_length, security_flags, header_crc32c) = struct.unpack(
            '<4sBBBBQQII', data[:32]
        )
        
        return cls(
            magic=magic,
            version=version,
            endian_flags=endian_flags,
            protocol_type=protocol_type,
            security_level=security_level,
            tool_id_hint=tool_id_hint,
            total_length=total_length,
            security_flags=security_flags,
            header_crc32c=header_crc32c
        )


@dataclass
class TLVBlock:
    """TLV extension block."""
    type: int
    flags: int = 0
    payload: bytes = b''
    
    @property
    def length(self) -> int:
        """Get payload length."""
        return len(self.payload)
    
    @property
    def is_required(self) -> bool:
        """Check if TLV is required."""
        return bool(self.flags & 0x01)
    
    def to_bytes(self) -> bytes:
        """Serialize TLV to bytes."""
        header = struct.pack('<HHI', self.type, self.flags, self.length)
        return header + self.payload
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'TLVBlock':
        """Parse TLV from bytes."""
        if len(data) < 8:
            raise E_TLV_TRUNCATED("TLV header too short")
        
        type_val, flags, length = struct.unpack('<HHI', data[:8])
        
        if len(data) < 8 + length:
            raise E_TLV_TRUNCATED("TLV payload truncated")
        
        return cls(
            type=type_val,
            flags=flags,
            payload=data[8:8+length]
        )
    
    def get_decoded_payload(self) -> Any:
        """Decode CBOR payload if applicable."""
        if self.type in CBOR_ENCODED_TLVS:
            return cbor2.loads(self.payload)
        return self.payload
    
    def set_payload(self, data: Any) -> None:
        """Encode payload as CBOR if applicable."""
        if self.type in CBOR_ENCODED_TLVS:
            self.payload = cbor2.dumps(data, canonical=True)
        elif isinstance(data, bytes):
            self.payload = data
        else:
            raise TypeError(f"Payload must be bytes for non-CBOR TLV type {self.type}")


class TCPDescriptor:
    """TCP capability descriptor with header and TLV extensions."""
    
    def __init__(self):
        """Initialize empty descriptor."""
        self.header = TCPHeader()
        self.tlvs: List[TLVBlock] = []
    
    def add_tlv(self, tlv_type: int, payload: Any, required: bool = False) -> None:
        """Add TLV extension to descriptor."""
        tlv = TLVBlock(type=tlv_type, flags=0x01 if required else 0x00)
        tlv.set_payload(payload)
        self.tlvs.append(tlv)
    
    def get_tlv(self, tlv_type: int) -> Optional[TLVBlock]:
        """Get first TLV of specified type."""
        for tlv in self.tlvs:
            if tlv.type == tlv_type:
                return tlv
        return None
    
    def get_all_tlvs(self, tlv_type: int) -> List[TLVBlock]:
        """Get all TLVs of specified type."""
        return [tlv for tlv in self.tlvs if tlv.type == tlv_type]
    
    def set_tlv(self, tlv_type: int, payload: Any, required: bool = False) -> None:
        """Set or update TLV (replaces existing)."""
        # Remove existing TLV of this type
        self.tlvs = [tlv for tlv in self.tlvs if tlv.type != tlv_type]
        # Add new TLV
        self.add_tlv(tlv_type, payload, required)
    
    def to_bytes(self) -> bytes:
        """Serialize to binary with proper two-pass header construction."""
        # First pass: serialize TLVs with canonical ordering
        sorted_tlvs = sorted(self.tlvs, key=lambda t: (t.type, t.flags))
        tlv_bytes = b''.join(t.to_bytes() for t in sorted_tlvs)
        
        # Update header fields
        self.header.total_length = 32 + len(tlv_bytes)
        
        # Serialize header without CRC
        hdr_no_crc = self.header.to_bytes(include_crc=False)
        
        # Compute and set CRC32C
        self.header.header_crc32c = crc32c.crc32c(hdr_no_crc) & 0xFFFFFFFF
        
        # Final header serialization with CRC
        header_bytes = self.header.to_bytes(include_crc=True)
        
        return header_bytes + tlv_bytes
    
    def to_bytes_without_tlv(self, exclude_type: int) -> bytes:
        """Serialize without specific TLV type (for signatures)."""
        # Create temporary descriptor without the excluded TLV
        temp = TCPDescriptor()
        temp.header = copy.deepcopy(self.header)
        temp.tlvs = [tlv for tlv in self.tlvs if tlv.type != exclude_type]
        
        # Serialize with proper header updates
        return temp.to_bytes()
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'TCPDescriptor':
        """Parse binary descriptor with strict validation."""
        if len(data) < 32:
            raise E_MAGIC("Descriptor too short")
        
        # Parse header
        header = TCPHeader.from_bytes(data[:32])
        
        # Validate magic
        if header.magic != b'TCAP':
            raise E_MAGIC(f"Invalid magic: {header.magic}")
        
        # Check version
        if header.version != 0x01:
            raise E_VERSION_UNSUPPORTED(f"Unsupported version: {header.version:#04x}")
        
        # Check endianness
        if not (header.endian_flags & 0x01):
            raise E_ENDIAN_UNSUPPORTED("Big-endian not supported")
        
        # Check reserved bits
        if header.endian_flags & ~0x01:
            raise E_RESERVED_BIT_SET(f"Reserved bits set: {header.endian_flags:#04x}")
        
        # Strict length check
        if len(data) != header.total_length:
            raise E_LENGTH_MISMATCH(
                f"Data length {len(data)} != declared {header.total_length}"
            )
        
        # Verify CRC
        header_for_crc = data[:28]
        expected_crc = crc32c.crc32c(header_for_crc) & 0xFFFFFFFF
        if expected_crc != header.header_crc32c:
            raise E_CRC(f"Header CRC mismatch: expected {expected_crc:#010x}, got {header.header_crc32c:#010x}")
        
        # Create descriptor
        descriptor = cls()
        descriptor.header = header
        
        # Parse TLVs with exact boundary checking
        offset = 32
        while offset < header.total_length:
            # Check TLV header fits
            if offset + 8 > header.total_length:
                raise E_TLV_TRUNCATED("TLV header extends beyond descriptor")
            
            # Parse TLV header
            tlv_type, tlv_flags, tlv_length = struct.unpack('<HHI', data[offset:offset+8])
            
            # Check TLV payload fits
            if offset + 8 + tlv_length > header.total_length:
                raise E_TLV_OVERFLOW(f"TLV payload extends beyond descriptor")
            
            # Create TLV
            tlv = TLVBlock(
                type=tlv_type,
                flags=tlv_flags,
                payload=data[offset+8:offset+8+tlv_length]
            )
            
            # Check for unknown required TLV
            if tlv.type not in KNOWN_TLVS and tlv.is_required:
                raise E_REQ_UNKNOWN_TLV(f"Unknown required TLV: {tlv.type:#06x}")
            
            descriptor.tlvs.append(tlv)
            offset += 8 + tlv_length
        
        # Verify we consumed exactly the declared length
        if offset != header.total_length:
            raise E_LENGTH_MISMATCH(
                f"TLV parsing ended at {offset}, expected {header.total_length}"
            )
        
        # Validate required TLVs and security
        descriptor._validate_required_tlvs()
        descriptor.validate()
        
        return descriptor
    
    def _validate_required_tlvs(self) -> None:
        """Ensure minimum viable descriptor."""
        tlv_types = {tlv.type for tlv in self.tlvs}
        
        # Must have IDENTITY
        if TLVType.IDENTITY not in tlv_types:
            raise E_SCHEMA("Missing required IDENTITY TLV")
        
        # Must have CAPABILITIES or PROTOCOL_EXT
        if not (TLVType.CAPABILITIES in tlv_types or 
                TLVType.PROTOCOL_EXT in tlv_types):
            raise E_SCHEMA("Missing CAPABILITIES or PROTOCOL_EXT")
    
    def _validate_security_ext(self) -> None:
        """Ensure resource_domain present when needed."""
        if self.header.security_flags & (
            SecurityFlags.FS_READ | SecurityFlags.FS_WRITE | SecurityFlags.FS_DELETE |
            SecurityFlags.NET_EGRESS | SecurityFlags.NET_INGRESS
        ):
            security_ext = self.get_tlv(TLVType.SECURITY_EXT)
            if not security_ext:
                raise E_SCHEMA("SECURITY_EXT required when FS_* or NET_* flags set")
            
            payload = security_ext.get_decoded_payload()
            if "resource_domain" not in payload:
                raise E_SCHEMA("resource_domain required in SECURITY_EXT")
            
            if payload["resource_domain"] not in ["LOCAL_HOST", "REMOTE_SERVICE", "SANDBOX"]:
                raise E_SCHEMA(f"Invalid resource_domain: {payload['resource_domain']}")
    
    def validate(self) -> None:
        """Complete validation after parse or before serialization."""
        # Check required TLVs (already done in from_bytes, but good for manual creation)
        self._validate_required_tlvs()
        
        # Validate security extension if flags present
        if self.header.security_flags != 0:
            try:
                self._validate_security_ext()
            except E_SCHEMA:
                # Allow missing SECURITY_EXT for now (backwards compat)
                pass
        
        # Recompute risk level from flags
        computed_risk = compute_risk_level(self.header.security_flags)
        
        # Normalization: computed risk overrides header if different
        if computed_risk != self.header.security_level:
            warnings.warn(
                f"Risk level mismatch: header={self.header.security_level}, "
                f"computed={computed_risk}. Using computed value."
            )
            self.header.security_level = computed_risk
    
    @staticmethod
    def compute_tool_id_hint(tool_name: str) -> int:
        """Compute 64-bit tool ID hint from name."""
        hash_bytes = hashlib.sha256(tool_name.encode()).digest()
        return struct.unpack('<Q', hash_bytes[:8])[0]