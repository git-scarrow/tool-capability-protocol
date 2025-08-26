"""TCP type definitions and enumerations."""

from enum import IntEnum, IntFlag


class ProtocolType(IntEnum):
    """Protocol types for tools."""
    CLI = 1
    MCP = 2
    OPENAPI = 3


class SecurityLevel(IntEnum):
    """Security risk levels."""
    SAFE = 0
    LOW_RISK = 1
    MEDIUM_RISK = 2
    HIGH_RISK = 3
    CRITICAL = 4


class SecurityFlags(IntFlag):
    """Security capability flags (32-bit bitmask)."""
    # File system operations
    FS_READ = 1 << 0            # Read file contents
    FS_WRITE = 1 << 1           # Modify/create files
    FS_DELETE = 1 << 2          # Remove files/directories
    
    # Network operations
    NET_EGRESS = 1 << 3         # Outbound connections
    NET_INGRESS = 1 << 4        # Listen for connections
    
    # Execution and privileges
    CODE_EXEC = 1 << 5          # Execute arbitrary code
    PRIV_ESC = 1 << 6           # Privilege escalation
    
    # Data and credentials
    CRED_ACCESS = 1 << 7        # Access credentials/secrets
    DATA_EXFIL = 1 << 8         # Data exfiltration risk
    
    # System modifications
    PERSISTENCE = 1 << 9        # Persistent system changes
    WILDCARD_RESOURCE = 1 << 10 # Unbounded resource access
    
    # AI-specific
    PROMPT_SURFACE = 1 << 11    # Prompt injection surface
    MODEL_ACCESS = 1 << 12      # Direct model access


class TLVType(IntEnum):
    """TLV extension types."""
    # Core types (0x0001-0x00FF)
    IDENTITY = 0x0001
    CAPABILITIES = 0x0002
    EFFECTS = 0x0003
    PROTOCOL_EXT = 0x0004
    SECURITY_EXT = 0x0005
    TELEMETRY = 0x0006
    SIGNATURES = 0x0007
    OVERRIDES = 0x0008
    
    # Reserved ranges:
    # 0x0100-0x3FFF: Future standard extensions
    # 0x4000-0x7FFF: Experimental
    # 0x8000-0xFFFF: Vendor/private
    
    @classmethod
    def is_experimental(cls, type_val: int) -> bool:
        """Check if type is in experimental range."""
        return 0x4000 <= type_val <= 0x7FFF
    
    @classmethod
    def is_vendor(cls, type_val: int) -> bool:
        """Check if type is in vendor/private range."""
        return 0x8000 <= type_val <= 0xFFFF


# TLV types that use CBOR encoding
CBOR_ENCODED_TLVS = {
    TLVType.IDENTITY,
    TLVType.CAPABILITIES,
    TLVType.EFFECTS,
    TLVType.PROTOCOL_EXT,
    TLVType.SECURITY_EXT,
    TLVType.TELEMETRY,
    TLVType.SIGNATURES,
    TLVType.OVERRIDES
}


# Known TLV types (for validation)
KNOWN_TLVS = set(TLVType)


def compute_risk_level(flags: int) -> int:
    """
    Compute security risk level from flags with combination awareness.
    
    Args:
        flags: Security flags bitmask
        
    Returns:
        Risk level (0=SAFE to 4=CRITICAL)
    """
    # Critical: Code execution or privilege escalation
    if flags & (SecurityFlags.PRIV_ESC | SecurityFlags.CODE_EXEC):
        return SecurityLevel.CRITICAL
    
    # High risk: Dangerous combinations or destructive operations
    dangerous_combo = (
        (flags & SecurityFlags.NET_EGRESS) and 
        (flags & SecurityFlags.DATA_EXFIL) and
        (flags & SecurityFlags.WILDCARD_RESOURCE)
    )
    if dangerous_combo or (flags & (SecurityFlags.FS_DELETE | SecurityFlags.CRED_ACCESS)):
        return SecurityLevel.HIGH_RISK
    
    # Medium risk: Write operations or persistence
    if flags & (SecurityFlags.FS_WRITE | SecurityFlags.PERSISTENCE):
        return SecurityLevel.MEDIUM_RISK
    
    # Low risk: Read operations or network access
    if flags & (SecurityFlags.NET_EGRESS | SecurityFlags.FS_READ):
        return SecurityLevel.LOW_RISK
    
    # Safe: No risky flags
    return SecurityLevel.SAFE