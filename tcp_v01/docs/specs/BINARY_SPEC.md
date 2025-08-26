# TCP Binary Format Specification v0.1

## Overview
The Tool Capability Protocol (TCP) defines a compact, lossless binary format for describing tool capabilities, security properties, and metadata. This specification defines version 0.1 of the binary format.

## Design Principles
- **Lossless**: Complete capability reconstruction from binary representation
- **Compact**: Fixed 32-byte header with optional TLV extensions
- **Forward-compatible**: Unknown extensions are preserved
- **Self-describing**: Version and type information embedded
- **Secure**: CRC32C integrity checking and cryptographic signatures

## Header Format (32 bytes)

All multi-byte integers are stored in **little-endian** byte order.

| Offset | Size | Field           | Type    | Description                              |
|--------|------|-----------------|---------|------------------------------------------|
| 0x00   | 4    | magic           | char[4] | "TCAP" (0x54434150)                     |
| 0x04   | 1    | version         | uint8   | 0x01 for v0.1                           |
| 0x05   | 1    | endian_flags    | uint8   | bit0=1 (little-endian), bits1-7=reserved (must be 0) |
| 0x06   | 1    | protocol_type   | uint8   | 1=CLI, 2=MCP, 3=OpenAPI                 |
| 0x07   | 1    | security_level  | uint8   | 0=SAFE, 1=LOW, 2=MEDIUM, 3=HIGH, 4=CRITICAL |
| 0x08   | 8    | tool_id_hint    | uint64  | SHA-256[:8] of tool name for fast lookup |
| 0x10   | 8    | total_length    | uint64  | Total descriptor size including header and all TLVs |
| 0x18   | 4    | security_flags  | uint32  | Security capability bitmask (see below)  |
| 0x1C   | 4    | header_crc32c   | uint32  | CRC32C of bytes[0x00:0x1C]              |

### Reserved Bits
- `endian_flags` bits 1-7 MUST be set to 0 by emitters
- Parsers MUST reject descriptors with non-zero reserved bits

### CRC32C Parameters
- **Polynomial**: 0x1EDC6F41 (Castagnoli)
- **Initial value**: 0xFFFFFFFF
- **Final XOR**: 0xFFFFFFFF
- **Reflected**: Input and output bits are reflected

## TLV Extension Format

TLV blocks immediately follow the 32-byte header. Each TLV has an 8-byte header followed by a variable-length payload.

| Offset | Size | Field   | Type   | Description                            |
|--------|------|---------|--------|----------------------------------------|
| 0x00   | 2    | type    | uint16 | Extension type (see TLV Types)        |
| 0x02   | 2    | flags   | uint16 | bit0=required, bits1-15=reserved      |
| 0x04   | 4    | length  | uint32 | Payload length in bytes               |
| 0x08   | var  | payload | bytes  | Type-specific data (CBOR encoded)     |

### TLV Types

| Type    | Name          | Encoding | Required | Description                           |
|---------|---------------|----------|----------|---------------------------------------|
| 0x0001  | IDENTITY      | CBOR     | Yes      | Tool name, version, adapter info     |
| 0x0002  | CAPABILITIES  | CBOR     | Yes*     | Verbs, parameters, schemas           |
| 0x0003  | EFFECTS       | CBOR     | No       | Side-effects, guards, examples       |
| 0x0004  | PROTOCOL_EXT  | CBOR     | Yes*     | Native protocol fields                |
| 0x0005  | SECURITY_EXT  | CBOR     | No**     | Resource domain, rationale           |
| 0x0006  | TELEMETRY     | CBOR     | No       | Performance observations              |
| 0x0007  | SIGNATURES    | CBOR     | No       | Cryptographic signatures array       |
| 0x0008  | OVERRIDES     | CBOR     | No       | Manual corrections/annotations       |

*Either CAPABILITIES or PROTOCOL_EXT must be present
**Required when security_flags includes FS_* or NET_* flags

### TLV Type Ranges
- `0x0001-0x00FF`: Core types (this specification)
- `0x0100-0x3FFF`: Reserved for future standard extensions
- `0x4000-0x7FFF`: Experimental use
- `0x8000-0xFFFF`: Vendor/private use

### Canonical TLV Ordering
For signature computation and comparison:
1. Sort by type (ascending)
2. Then by flags (ascending)
3. Duplicates allowed only for: CAPABILITIES, TELEMETRY

### Unknown TLV Handling
- If `flags & 0x01` (required) and type unknown: **reject** with error
- If `flags & 0x01 == 0` (optional) and type unknown: **preserve** byte-exact

## Security Flags (32-bit bitmask)

| Bit | Flag               | Description                                 |
|-----|--------------------|---------------------------------------------|
| 0   | FS_READ           | Read file contents                          |
| 1   | FS_WRITE          | Modify/create files                         |
| 2   | FS_DELETE         | Remove files/directories                    |
| 3   | NET_EGRESS        | Make outbound network connections           |
| 4   | NET_INGRESS       | Listen for incoming connections             |
| 5   | CODE_EXEC         | Execute arbitrary code/commands             |
| 6   | PRIV_ESC          | Escalate privileges (sudo/admin)            |
| 7   | CRED_ACCESS       | Access stored credentials/secrets           |
| 8   | DATA_EXFIL        | Potential for data exfiltration             |
| 9   | PERSISTENCE       | Make persistent system changes              |
| 10  | WILDCARD_RESOURCE | Unbounded resource access (*/all)           |
| 11  | PROMPT_SURFACE    | Accepts untrusted prompt input (MCP)        |
| 12  | MODEL_ACCESS      | Direct LLM model access (MCP)               |
| 13-31 | Reserved         | Must be 0                                   |

## TLV Payload Schemas

All structured TLV payloads use [CBOR](https://cbor.io/) encoding with canonical serialization (RFC 8949 Section 4.2).

### IDENTITY (0x0001)
```cbor
{
  "name": "tool-name",           // Required: Tool identifier
  "version": "1.0.0",            // Required: Version string
  "adapter": "cli/1.0.0",        // Required: Adapter name/version
  "source": "help:git",          // Optional: Source of information
  "timestamp": "2024-01-15T..."  // Optional: ISO 8601 timestamp
}
```

### CAPABILITIES (0x0002)
```cbor
{
  "verbs": ["read", "write"],           // Tool actions
  "parameters": [...],                  // Parameter schemas
  "resources": ["file", "network"],     // Resource types
  "schemas": {...}                      // Input/output schemas
}
```

### SECURITY_EXT (0x0005)
```cbor
{
  "resource_domain": "LOCAL_HOST",      // Required with FS_*/NET_* flags
  "rationale": "Modifies system files", // Optional: Risk explanation
  "mitigations": [...]                  // Optional: Safety measures
}
```

Resource domains:
- `LOCAL_HOST`: Local file system and processes
- `REMOTE_SERVICE`: Network APIs and cloud services  
- `SANDBOX`: Isolated/containerized environment

### SIGNATURES (0x0007)
```cbor
[
  {
    "scope": "binary",                     // Signature scope
    "alg": "Ed25519",                     // Algorithm
    "kid": "base64url...",                // Key identifier
    "sig": "base64url...",                // Signature bytes
    "hash": "hex...",                     // SHA256 of signed content
    "timestamp": "2024-01-15T..."         // ISO 8601 timestamp
  }
]
```

Key ID formats:
1. Raw public key: `base64url(ed25519_pubkey_32_bytes)`
2. JWK Thumbprint: `base64url(sha256(canonical_jwk))`
3. Key URI: `https://keys.example.com/ed25519/abc123`

## Performance Targets

### Native Implementation (Rust/C/C++)
- Encode: < 1 microsecond per descriptor
- Decode: < 500 nanoseconds per descriptor
- Query: p50 < 1ms, p99 < 3ms @ 100K tools

### Python Reference Implementation
- Encode: 10-50 microseconds per descriptor (measured: ~25μs)
- Decode: 5-25 microseconds per descriptor (measured: ~12μs)
- Query: p50 < 5ms, p99 < 15ms @ 100K tools

Note: Python implementation prioritizes correctness and clarity over performance. Production systems requiring sub-microsecond performance should use native implementations.

## Error Codes

| Code                   | Description                                        |
|------------------------|---------------------------------------------------|
| E_MAGIC               | Invalid magic bytes (not "TCAP")                  |
| E_VERSION_UNSUPPORTED | Unsupported protocol version                      |
| E_ENDIAN_UNSUPPORTED  | Unsupported endianness                           |
| E_RESERVED_BIT_SET    | Reserved bits are non-zero                       |
| E_CRC                 | Header CRC32C mismatch                           |
| E_LENGTH_MISMATCH     | Actual length != declared total_length           |
| E_TLV_TRUNCATED       | TLV extends beyond descriptor boundary           |
| E_TLV_OVERFLOW        | TLV payload extends beyond descriptor            |
| E_REQ_UNKNOWN_TLV     | Unknown TLV with required flag set               |
| E_SCHEMA              | Required fields missing or invalid               |
| E_SIG_BAD             | Signature verification failed                     |

## Version History

- v0.1 (2024-01): Initial specification