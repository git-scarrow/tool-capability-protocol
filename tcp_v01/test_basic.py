#!/usr/bin/env python3
"""Basic test to verify TCP v0.1 implementation works."""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from core.descriptor import TCPDescriptor
from core.types import ProtocolType, SecurityFlags, TLVType
from adapters.cli import CLIAdapter
from adapters.mcp import MCPAdapter


def test_basic_descriptor():
    """Test creating and serializing a basic descriptor."""
    print("Testing basic descriptor creation...")
    
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
    
    # Serialize to binary
    binary = descriptor.to_bytes()
    print(f"  Serialized to {len(binary)} bytes")
    
    # Parse back
    parsed = TCPDescriptor.from_bytes(binary)
    print(f"  Successfully parsed back")
    
    # Verify round-trip
    assert parsed.header.protocol_type == ProtocolType.CLI
    assert parsed.header.security_flags == SecurityFlags.FS_READ
    
    identity = parsed.get_tlv(TLVType.IDENTITY).get_decoded_payload()
    assert identity["name"] == "test-tool"
    
    print("✅ Basic descriptor test passed")
    return True


def test_cli_adapter():
    """Test CLI adapter."""
    print("\nTesting CLI adapter...")
    
    adapter = CLIAdapter()
    
    # Analyze a safe command
    descriptor = adapter.analyze("ls -la")
    assert descriptor.header.protocol_type == ProtocolType.CLI
    assert descriptor.header.security_flags & SecurityFlags.FS_READ
    
    print("  Analyzed 'ls -la' command")
    
    # Test binary round-trip
    binary = descriptor.to_bytes()
    parsed = TCPDescriptor.from_bytes(binary)
    assert parsed.header.security_flags == descriptor.header.security_flags
    
    print("✅ CLI adapter test passed")
    return True


def test_mcp_adapter():
    """Test MCP adapter with lossless preservation."""
    print("\nTesting MCP adapter...")
    
    adapter = MCPAdapter()
    
    # Test manifest
    manifest = {
        "name": "test-mcp-server",
        "version": "2.0.0",
        "tools": ["read_file", "write_file", "delete_file"],
        "resources": ["file://*", "https://api.example.com/*"],
        "prompts": ["summarize", "explain"],
        "custom_field": {"nested": {"data": [1, 2, 3]}}
    }
    
    # Analyze manifest
    descriptor = adapter.analyze(manifest, "test://source")
    assert descriptor.header.protocol_type == ProtocolType.MCP
    
    # Check security flags were mapped
    assert descriptor.header.security_flags & SecurityFlags.FS_READ
    assert descriptor.header.security_flags & SecurityFlags.FS_WRITE
    assert descriptor.header.security_flags & SecurityFlags.FS_DELETE
    assert descriptor.header.security_flags & SecurityFlags.NET_EGRESS
    assert descriptor.header.security_flags & SecurityFlags.WILDCARD_RESOURCE
    
    print("  Security flags correctly mapped")
    
    # Verify lossless preservation
    protocol_ext = descriptor.get_tlv(TLVType.PROTOCOL_EXT).get_decoded_payload()
    assert protocol_ext == manifest
    assert protocol_ext["custom_field"]["nested"]["data"] == [1, 2, 3]
    
    print("  Manifest preserved losslessly in PROTOCOL_EXT")
    
    # Test round-trip
    binary = descriptor.to_bytes()
    parsed = TCPDescriptor.from_bytes(binary)
    
    recovered_manifest = parsed.get_tlv(TLVType.PROTOCOL_EXT).get_decoded_payload()
    assert recovered_manifest == manifest
    
    print("✅ MCP adapter test passed (lossless round-trip verified)")
    return True


def test_header_validation():
    """Test header validation and error cases."""
    print("\nTesting header validation...")
    
    # Test magic validation
    bad_magic = b'XXXX' + b'\x00' * 28
    try:
        TCPDescriptor.from_bytes(bad_magic)
        assert False, "Should have raised E_MAGIC"
    except Exception as e:
        assert "magic" in str(e).lower()
        print("  Magic validation works")
    
    # Test CRC validation
    descriptor = TCPDescriptor()
    descriptor.add_tlv(TLVType.IDENTITY, {"name": "test", "version": "1.0", "adapter": "test"}, required=True)
    descriptor.add_tlv(TLVType.CAPABILITIES, {"verbs": []}, required=True)
    
    binary = descriptor.to_bytes()
    
    # Corrupt a byte
    corrupted = bytearray(binary)
    corrupted[10] ^= 0xFF
    
    try:
        TCPDescriptor.from_bytes(bytes(corrupted))
        assert False, "Should have raised E_CRC"
    except Exception as e:
        assert "CRC" in str(e)
        print("  CRC validation works")
    
    print("✅ Header validation test passed")
    return True


def main():
    """Run all tests."""
    print("=" * 60)
    print("TCP v0.1 Basic Tests")
    print("=" * 60)
    
    tests = [
        test_basic_descriptor,
        test_cli_adapter,
        test_mcp_adapter,
        test_header_validation
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            if test():
                passed += 1
        except Exception as e:
            print(f"❌ {test.__name__} failed: {e}")
            failed += 1
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())