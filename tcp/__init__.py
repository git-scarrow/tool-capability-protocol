"""Tool Capability Protocol (TCP) - Universal tool capability description."""

from .core.descriptors import (
    BinaryCapabilityDescriptor,
    CapabilityDescriptor,
    CommandDescriptor,
    FormatDescriptor,
    ParameterDescriptor,
)
from .core.discovery import DiscoveryService
from .core.protocol import ToolCapabilityProtocol
from .core.registry import CapabilityRegistry
from .generators import (
    BinaryGenerator,
    GraphQLGenerator,
    JSONGenerator,
    OpenAPIGenerator,
    ProtobufGenerator,
)
from .harness import (
    GateResult,
    RuntimeEnvironment,
    ToolRecord,
    ToolSelectionRequest,
    gate_tools,
    project_tool,
    project_tools,
    route_tool,
)

__version__ = "0.1.0"
__author__ = "TCP Team"
__email__ = "team@tcp.dev"

__all__ = [
    # Core classes
    "ToolCapabilityProtocol",
    "CapabilityDescriptor",
    "BinaryCapabilityDescriptor",
    "CommandDescriptor",
    "ParameterDescriptor",
    "FormatDescriptor",
    "CapabilityRegistry",
    "DiscoveryService",
    "ToolRecord",
    "ToolSelectionRequest",
    "RuntimeEnvironment",
    "GateResult",
    # Generators
    "JSONGenerator",
    "OpenAPIGenerator",
    "GraphQLGenerator",
    "ProtobufGenerator",
    "BinaryGenerator",
    # Harness
    "gate_tools",
    "project_tool",
    "project_tools",
    "route_tool",
]
