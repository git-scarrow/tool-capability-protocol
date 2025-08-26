#!/usr/bin/env python3
"""ASCII-only test for Windows compatibility."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.descriptor import TCPDescriptor
from core.types import ProtocolType, SecurityFlags, TLVType
from core.errors import E_CRC, E_SCHEMA


def test_basic():
    """Test basic descriptor functionality."""
    print("Testing TCP v0.1 implementation...")
    print("-" * 40)
    
    # Create descriptor
    descriptor = TCPDescriptor()
    descriptor.header.protocol_type = ProtocolType.CLI
    descriptor.header.security_flags = SecurityFlags.FS_READ
    
    # Add required TLVs
    descriptor.add_tlv(TLVType.IDENTITY, {
        "name": "test-tool",
        "version": "1.0.0",
        "adapter": "test/1.0.0"
    }, required=True)
    
    descriptor.add_tlv(TLVType.CAPABILITIES, {
        "verbs": ["read", "list"],
        "resources": ["filesystem"]
    }, required=True)
    
    # Validate
    descriptor.validate()
    print(f"[OK] Created descriptor with {len(descriptor.tlvs)} TLVs")
    
    # Serialize to binary
    binary = descriptor.to_bytes()
    print(f"[OK] Serialized to {len(binary)} bytes")
    print(f"     Header: 32 bytes, TLVs: {len(binary) - 32} bytes")
    
    # Parse back
    parsed = TCPDescriptor.from_bytes(binary)
    print(f"[OK] Successfully parsed descriptor from binary")
    
    # Verify round-trip
    assert parsed.header.protocol_type == ProtocolType.CLI
    assert parsed.header.security_flags == SecurityFlags.FS_READ
    assert parsed.header.total_length == len(binary)
    
    identity = parsed.get_tlv(TLVType.IDENTITY).get_decoded_payload()
    assert identity["name"] == "test-tool"
    
    print("[OK] Round-trip verification passed")
    
    # Test CRC validation
    print("\nTesting CRC validation...")
    corrupted = bytearray(binary)
    corrupted[10] ^= 0xFF
    
    try:
        TCPDescriptor.from_bytes(bytes(corrupted))
        print("[FAIL] CRC validation failed - should have raised error")
        return False
    except E_CRC:
        print("[OK] CRC validation correctly detected corruption")
    
    # Test required TLV validation
    print("\nTesting required TLV validation...")
    bad_desc = TCPDescriptor()
    bad_desc.add_tlv(TLVType.CAPABILITIES, {"verbs": []})
    
    try:
        bad_binary = bad_desc.to_bytes()
        TCPDescriptor.from_bytes(bad_binary)
        print("[FAIL] Required TLV validation failed")
        return False
    except E_SCHEMA as e:
        print(f"[OK] Required TLV validation: {e}")
    
    print("\n" + "=" * 40)
    print("ALL TESTS PASSED!")
    return True


if __name__ == "__main__":
    try:
        success = test_basic()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n[ERROR] Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)