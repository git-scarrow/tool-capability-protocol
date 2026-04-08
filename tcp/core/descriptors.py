"""Core descriptor classes for tool capabilities."""

import hashlib
import json
import struct
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Dict, List, Optional, Union


UTC = timezone.utc


class ParameterType(IntEnum):
    """Parameter type enumeration."""

    STRING = 1
    INTEGER = 2
    FLOAT = 3
    BOOLEAN = 4
    ENUM = 5
    ARRAY = 6
    OBJECT = 7
    FILE = 8
    BINARY = 9


class FormatType(IntEnum):
    """Format type enumeration."""

    TEXT = 1
    JSON = 2
    XML = 3
    BINARY = 4
    IMAGE = 5
    AUDIO = 6
    VIDEO = 7
    DOCUMENT = 8
    ARCHIVE = 9
    BASE64 = 10


class ProcessingMode(IntEnum):
    """Processing mode enumeration."""

    SYNC = 1
    ASYNC = 2
    BATCH = 3
    STREAM = 4
    REALTIME = 5


class CapabilityFlags(IntEnum):
    """Capability flags for binary representation."""

    # Input capabilities (bits 0-7)
    SUPPORTS_FILES = 1 << 0
    SUPPORTS_STDIN = 1 << 1
    SUPPORTS_NETWORK = 1 << 2
    SUPPORTS_BASE64 = 1 << 3
    AUTO_DETECTION = 1 << 4
    BATCH_PROCESSING = 1 << 5
    PARALLEL_PROCESSING = 1 << 6
    GPU_ACCELERATION = 1 << 7

    # Processing capabilities (bits 8-15)
    REALTIME_PROCESSING = 1 << 8
    ASYNC_PROCESSING = 1 << 9
    STREAM_PROCESSING = 1 << 10
    CACHING_SUPPORT = 1 << 11
    RATE_LIMITING = 1 << 12
    AUTH_REQUIRED = 1 << 13
    STATELESS = 1 << 14
    IDEMPOTENT = 1 << 15

    # Output capabilities (bits 16-23)
    JSON_OUTPUT = 1 << 16
    XML_OUTPUT = 1 << 17
    BINARY_OUTPUT = 1 << 18
    STREAMING_OUTPUT = 1 << 19
    FILE_OUTPUT = 1 << 20
    NETWORK_OUTPUT = 1 << 21
    COMPRESSED_OUTPUT = 1 << 22
    ENCRYPTED_OUTPUT = 1 << 23


@dataclass
class ParameterDescriptor:
    """Describes a command parameter."""

    name: str
    type: ParameterType
    required: bool = False
    default: Optional[Any] = None
    description: str = ""
    enum_values: Optional[List[str]] = None
    min_value: Optional[Union[int, float]] = None
    max_value: Optional[Union[int, float]] = None
    pattern: Optional[str] = None
    examples: List[Any] = field(default_factory=list)


@dataclass
class FormatDescriptor:
    """Describes an input/output format."""

    name: str
    type: FormatType
    extensions: List[str] = field(default_factory=list)
    mime_types: List[str] = field(default_factory=list)
    description: str = ""
    max_size_mb: Optional[int] = None
    encoding: Optional[str] = None
    compression: Optional[str] = None


@dataclass
class CommandDescriptor:
    """Describes a tool command."""

    name: str
    description: str = ""
    parameters: List[ParameterDescriptor] = field(default_factory=list)
    input_formats: List[FormatDescriptor] = field(default_factory=list)
    output_formats: List[FormatDescriptor] = field(default_factory=list)
    processing_modes: List[ProcessingMode] = field(default_factory=list)
    examples: List[Dict[str, Any]] = field(default_factory=list)
    error_codes: Dict[int, str] = field(default_factory=dict)
    timeout_seconds: Optional[int] = None
    rate_limit: Optional[Dict[str, int]] = None

    def get_parameter(self, name: str) -> Optional[ParameterDescriptor]:
        """Get parameter by name."""
        return next((p for p in self.parameters if p.name == name), None)

    def get_required_parameters(self) -> List[ParameterDescriptor]:
        """Get list of required parameters."""
        return [p for p in self.parameters if p.required]

    def supports_format(self, format_name: str) -> bool:
        """Check if command supports a specific format."""
        return any(
            format_name.lower() in [f.name.lower() for f in self.input_formats]
            or format_name.lower()
            in [ext.lower() for f in self.input_formats for ext in f.extensions]
        )


@dataclass
class PerformanceMetrics:
    """Performance characteristics of a tool."""

    avg_processing_time_ms: int = 1000
    max_file_size_mb: int = 100
    memory_usage_mb: int = 512
    cpu_cores: int = 1
    gpu_required: bool = False
    network_required: bool = False
    disk_space_mb: int = 0
    concurrent_requests: int = 1
    requests_per_minute: int = 60


@dataclass
class CapabilityDescriptor:
    """Comprehensive tool capability descriptor."""

    # Basic metadata
    name: str
    version: str
    description: str = ""
    author: str = ""
    license: str = ""
    homepage: str = ""

    # Capabilities
    commands: Union[List[CommandDescriptor], Dict[str, CommandDescriptor]] = field(
        default_factory=list
    )
    input_formats: List[FormatDescriptor] = field(default_factory=list)
    output_formats: List[FormatDescriptor] = field(default_factory=list)
    processing_modes: List[ProcessingMode] = field(default_factory=list)

    # Technical specifications
    python_version: str = ">=3.8"
    dependencies: List[str] = field(default_factory=list)
    optional_dependencies: Dict[str, List[str]] = field(default_factory=dict)

    # Performance and constraints
    performance: PerformanceMetrics = field(default_factory=PerformanceMetrics)
    capability_flags: int = 0

    # Metadata
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    schema_version: str = "1.0.0"

    @staticmethod
    def _utc_now() -> datetime:
        """Return a timezone-aware UTC timestamp."""
        return datetime.now(UTC)

    def __post_init__(self):
        """Post-initialization processing."""
        # Normalize commands from Dict to List if needed
        if isinstance(self.commands, dict):
            warnings.warn(
                "Dict format for commands is deprecated and will be removed in v2.0.0. "
                "Use List[CommandDescriptor] instead. "
                "Migration guide: https://tcp.dev/docs/migration/commands-list",
                DeprecationWarning,
                stacklevel=3,
            )
            # Convert dict values to list, preserving the command descriptors
            self.commands = list(self.commands.values())

        if self.created_at is None:
            self.created_at = self._utc_now()
        self.updated_at = self._utc_now()

    def add_command(self, command: CommandDescriptor) -> None:
        """Add a command to the descriptor."""
        # Ensure commands is a list after normalization
        if isinstance(self.commands, list):
            self.commands.append(command)
        else:
            # This shouldn't happen after __post_init__, but handle it defensively
            raise TypeError(
                "Commands must be normalized to a List before adding new commands"
            )
        self.updated_at = self._utc_now()

    def get_command(self, name: str) -> Optional[CommandDescriptor]:
        """Get command by name."""
        # Ensure commands is a list (should be after __post_init__)
        if isinstance(self.commands, list):
            return next((c for c in self.commands if c.name == name), None)
        else:
            # This shouldn't happen after __post_init__, but handle it defensively
            return self.commands.get(name) if isinstance(self.commands, dict) else None

    def supports_format(self, format_name: str) -> bool:
        """Check if tool supports a specific format."""
        # Check global formats
        global_support = any(
            format_name.lower() in [f.name.lower() for f in self.input_formats]
            or format_name.lower()
            in [ext.lower() for f in self.input_formats for ext in f.extensions]
        )

        # Check command-specific formats
        command_support = any(cmd.supports_format(format_name) for cmd in self.commands)

        return global_support or command_support

    def get_capability_flags(self) -> int:
        """Calculate capability flags from descriptors."""
        flags = 0

        # Input capabilities
        if any(f.type == FormatType.BINARY for f in self.input_formats):
            flags |= CapabilityFlags.SUPPORTS_FILES
        if ProcessingMode.BATCH in self.processing_modes:
            flags |= CapabilityFlags.BATCH_PROCESSING
        if self.performance.concurrent_requests > 1:
            flags |= CapabilityFlags.PARALLEL_PROCESSING
        if self.performance.gpu_required:
            flags |= CapabilityFlags.GPU_ACCELERATION

        # Processing capabilities
        if ProcessingMode.ASYNC in self.processing_modes:
            flags |= CapabilityFlags.ASYNC_PROCESSING
        if ProcessingMode.STREAM in self.processing_modes:
            flags |= CapabilityFlags.STREAM_PROCESSING
        if ProcessingMode.REALTIME in self.processing_modes:
            flags |= CapabilityFlags.REALTIME_PROCESSING

        # Output capabilities
        if any(f.type == FormatType.JSON for f in self.output_formats):
            flags |= CapabilityFlags.JSON_OUTPUT
        if any(f.type == FormatType.XML for f in self.output_formats):
            flags |= CapabilityFlags.XML_OUTPUT
        if any(f.type == FormatType.BINARY for f in self.output_formats):
            flags |= CapabilityFlags.BINARY_OUTPUT

        return flags

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""

        def serialize_obj(obj):
            if isinstance(obj, IntEnum):
                return obj.value
            if hasattr(obj, "__dict__"):
                return {k: serialize_obj(v) for k, v in obj.__dict__.items()}
            elif isinstance(obj, list):
                return [serialize_obj(item) for item in obj]
            elif isinstance(obj, dict):
                return {k: serialize_obj(v) for k, v in obj.items()}
            elif isinstance(obj, datetime):
                return obj.isoformat()
            else:
                return obj

        return serialize_obj(self)

    def get_fingerprint(self) -> str:
        """Generate unique fingerprint for this capability descriptor."""
        # Create deterministic representation
        fingerprint_data = {
            "name": self.name,
            "version": self.version,
            "commands": [cmd.name for cmd in self.commands],
            "input_formats": [f.name for f in self.input_formats],
            "capability_flags": self.get_capability_flags(),
        }

        # Generate hash
        data_str = json.dumps(fingerprint_data, sort_keys=True)
        return hashlib.sha256(data_str.encode()).hexdigest()[:16]


@dataclass
class BinaryCapabilityDescriptor:
    """Ultra-compact binary representation of tool capabilities."""

    # Header (8 bytes)
    magic: bytes = b"TCP\x01"  # 4 bytes magic + version
    checksum: int = 0  # 4 bytes CRC32 checksum

    # Capabilities (8 bytes)
    capability_flags: int = 0  # 4 bytes capability flags
    command_count: int = 0  # 1 byte command count
    format_count: int = 0  # 1 byte format count
    reserved: bytes = b"\x00\x00"  # 2 bytes reserved

    # Performance (4 bytes)
    max_file_size_mb: int = 100  # 2 bytes max file size
    avg_processing_time_ms: int = 1000  # 2 bytes avg processing time

    @classmethod
    def from_capability_descriptor(
        cls, descriptor: CapabilityDescriptor
    ) -> "BinaryCapabilityDescriptor":
        """Create binary descriptor from full capability descriptor."""
        binary = cls(
            capability_flags=descriptor.get_capability_flags(),
            command_count=min(len(descriptor.commands), 255),
            format_count=min(
                len(descriptor.input_formats) + len(descriptor.output_formats), 255
            ),
            max_file_size_mb=min(descriptor.performance.max_file_size_mb, 65535),
            avg_processing_time_ms=min(
                descriptor.performance.avg_processing_time_ms, 65535
            ),
        )

        # Calculate checksum
        data = binary.to_bytes()
        data_without_checksum = data[:4] + data[8:]  # Skip checksum field (bytes 4-8)
        binary.checksum = _crc32(data_without_checksum)

        return binary

    def to_bytes(self) -> bytes:
        """Serialize to 20-byte binary format."""
        return struct.pack(
            ">4sIIBBHHH",
            self.magic,
            self.checksum,
            self.capability_flags,
            self.command_count,
            self.format_count,
            self.max_file_size_mb,
            self.avg_processing_time_ms,
            0,  # Reserved
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "BinaryCapabilityDescriptor":
        """Deserialize from 20-byte binary format."""
        if len(data) != 20:
            raise ValueError(f"Expected 20 bytes, got {len(data)}")

        (
            magic,
            checksum,
            capability_flags,
            command_count,
            format_count,
            max_file_size_mb,
            avg_processing_time_ms,
            reserved,
        ) = struct.unpack(">4sIIBBHHH", data)

        # Verify magic number
        if magic != b"TCP\x01":
            raise ValueError(f"Invalid magic number: {magic}")

        # Verify checksum
        data_without_checksum = data[:4] + data[8:]  # Skip checksum field
        if checksum != _crc32(data_without_checksum):
            raise ValueError("Checksum mismatch")

        return cls(
            magic=magic,
            checksum=checksum,
            capability_flags=capability_flags,
            command_count=command_count,
            format_count=format_count,
            max_file_size_mb=max_file_size_mb,
            avg_processing_time_ms=avg_processing_time_ms,
        )

    def has_capability(self, flag: CapabilityFlags) -> bool:
        """Check if specific capability flag is set."""
        return bool(self.capability_flags & flag)

    def get_capabilities(self) -> List[str]:
        """Get list of capability names."""
        capabilities = []
        for flag in CapabilityFlags:
            if self.has_capability(flag):
                capabilities.append(flag.name.lower())
        return capabilities


def _crc32(data: bytes) -> int:
    """Calculate CRC32 checksum."""
    import zlib

    return zlib.crc32(data) & 0xFFFFFFFF
