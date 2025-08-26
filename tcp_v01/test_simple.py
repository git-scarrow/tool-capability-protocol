#!/usr/bin/env python3
"""Simple test without package imports."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Direct imports
from core.descriptor import TCPDescriptor, TCPHeader, TLVBlock
from core.types import ProtocolType, SecurityFlags, TLVType
from core.errors import *


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
    print(f"✅ Created descriptor with {len(descriptor.tlvs)} TLVs")
    
    # Serialize to binary
    binary = descriptor.to_bytes()
    print(f"✅ Serialized to {len(binary)} bytes")
    print(f"   Header: 32 bytes")
    print(f"   TLVs: {len(binary) - 32} bytes")
    
    # Show hex dump of header
    header_hex = ' '.join(f'{b:02x}' for b in binary[:32])
    print(f"   Header hex: {header_hex[:47]}...")
    
    # Parse back
    parsed = TCPDescriptor.from_bytes(binary)
    print(f"✅ Successfully parsed descriptor from binary")
    
    # Verify round-trip
    assert parsed.header.protocol_type == ProtocolType.CLI
    assert parsed.header.security_flags == SecurityFlags.FS_READ
    assert parsed.header.total_length == len(binary)
    
    identity = parsed.get_tlv(TLVType.IDENTITY).get_decoded_payload()
    assert identity["name"] == "test-tool"
    assert identity["version"] == "1.0.0"
    
    capabilities = parsed.get_tlv(TLVType.CAPABILITIES).get_decoded_payload()
    assert "read" in capabilities["verbs"]
    assert "filesystem" in capabilities["resources"]
    
    print("✅ Round-trip verification passed")
    
    # Test CRC validation
    print("\n" + "-" * 40)
    print("Testing CRC validation...")
    
    # Corrupt a byte
    corrupted = bytearray(binary)
    corrupted[10] ^= 0xFF
    
    try:
        TCPDescriptor.from_bytes(bytes(corrupted))
        print("❌ CRC validation failed - should have raised error")
        return False
    except E_CRC:
        print("✅ CRC validation correctly detected corruption")
    
    # Test required TLV validation
    print("\n" + "-" * 40)
    print("Testing required TLV validation...")
    
    bad_desc = TCPDescriptor()
    bad_desc.add_tlv(TLVType.CAPABILITIES, {"verbs": []})
    # Missing IDENTITY
    
    try:
        bad_binary = bad_desc.to_bytes()
        TCPDescriptor.from_bytes(bad_binary)
        print("❌ Required TLV validation failed")
        return False
    except E_SCHEMA as e:
        print(f"✅ Required TLV validation: {e}")
    
    print("\n" + "=" * 40)
    print("All tests passed!")
    return True


if __name__ == "__main__":
    try:
        success = test_basic()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)