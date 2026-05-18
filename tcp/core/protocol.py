"""Main TCP protocol implementation."""

import base64
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, Union

from .descriptors import (
    BinaryCapabilityDescriptor,
    CapabilityDescriptor,
    CommandDescriptor,
    FormatDescriptor,
    FormatType,
    ParameterDescriptor,
    ParameterType,
    PerformanceMetrics,
    ProcessingMode,
)
from .discovery import DiscoveryService
from .registry import CapabilityRegistry


class ToolCapabilityProtocol:
    """Main TCP protocol class for managing tool capabilities."""

    def __init__(self, registry: Optional[CapabilityRegistry] = None):
        """Initialize TCP protocol."""
        self.registry = registry or CapabilityRegistry()
        self.discovery = DiscoveryService(self.registry)
        self._generators: Dict[str, Type] = {}
        self._adapters: Dict[str, Type] = {}

    def create_descriptor(
        self, name: str, version: str, description: str = "", **kwargs
    ) -> CapabilityDescriptor:
        """Create a new capability descriptor."""
        return CapabilityDescriptor(
            name=name, version=version, description=description, **kwargs
        )

    def add_command(
        self,
        descriptor: CapabilityDescriptor,
        command_name: str,
        description: str = "",
        **kwargs,
    ) -> CommandDescriptor:
        """Add a command to a capability descriptor."""
        command = CommandDescriptor(
            name=command_name, description=description, **kwargs
        )
        descriptor.add_command(command)
        return command

    def add_parameter(
        self,
        command: CommandDescriptor,
        param_name: str,
        param_type: Union[ParameterType, str],
        required: bool = False,
        **kwargs,
    ) -> ParameterDescriptor:
        """Add a parameter to a command."""
        if isinstance(param_type, str):
            param_type = ParameterType[param_type.upper()]

        parameter = ParameterDescriptor(
            name=param_name, type=param_type, required=required, **kwargs
        )
        command.parameters.append(parameter)
        return parameter

    def add_format(
        self,
        descriptor: CapabilityDescriptor,
        format_name: str,
        format_type: Union[FormatType, str],
        is_input: bool = True,
        **kwargs,
    ) -> FormatDescriptor:
        """Add an input/output format to a descriptor."""
        if isinstance(format_type, str):
            format_type = FormatType[format_type.upper()]

        format_desc = FormatDescriptor(name=format_name, type=format_type, **kwargs)

        if is_input:
            descriptor.input_formats.append(format_desc)
        else:
            descriptor.output_formats.append(format_desc)

        return format_desc

    def register_tool(self, descriptor: CapabilityDescriptor) -> None:
        """Register a tool capability descriptor."""
        self.registry.register(descriptor)

    def unregister_tool(self, name: str, version: Optional[str] = None) -> bool:
        """Unregister a tool capability descriptor."""
        return self.registry.unregister(name, version)

    def get_tool(
        self, name: str, version: Optional[str] = None
    ) -> Optional[CapabilityDescriptor]:
        """Get a registered tool descriptor."""
        return self.registry.get(name, version)

    def list_tools(self) -> List[CapabilityDescriptor]:
        """List all registered tools."""
        return self.registry.list_all()

    def discover_tools(self, **filters) -> List[CapabilityDescriptor]:
        """Discover tools matching criteria."""
        return self.discovery.discover(**filters)

    def query_capabilities(
        self, tool_name: str, capability: str, version: Optional[str] = None
    ) -> bool:
        """Query if a tool has a specific capability."""
        descriptor = self.get_tool(tool_name, version)
        if not descriptor:
            return False

        # Check various capability types
        if capability.startswith("format:"):
            format_name = capability[7:]
            return descriptor.supports_format(format_name)
        elif capability.startswith("command:"):
            command_name = capability[8:]
            return descriptor.get_command(command_name) is not None
        elif capability.startswith("flag:"):
            flag_name = capability[5:]
            try:
                from .descriptors import CapabilityFlags

                flag = getattr(CapabilityFlags, flag_name.upper())
                return bool(descriptor.get_capability_flags() & flag)
            except AttributeError:
                return False
        else:
            # Generic capability check
            return hasattr(descriptor, capability)

    def select_optimal_tool(
        self, tools: List[CapabilityDescriptor], criteria: str = "speed"
    ) -> Optional[CapabilityDescriptor]:
        """Select the optimal tool from a list based on criteria."""
        if not tools:
            return None

        if criteria == "speed":
            return min(tools, key=lambda t: t.performance.avg_processing_time_ms)
        elif criteria == "memory":
            return min(tools, key=lambda t: t.performance.memory_usage_mb)
        elif criteria == "size":
            return max(tools, key=lambda t: t.performance.max_file_size_mb)
        elif criteria == "concurrent":
            return max(tools, key=lambda t: t.performance.concurrent_requests)
        else:
            return tools[0]  # Return first by default

    def generate_binary(self, descriptor: CapabilityDescriptor) -> bytes:
        """Generate binary capability descriptor."""
        binary_desc = BinaryCapabilityDescriptor.from_capability_descriptor(descriptor)
        return binary_desc.to_bytes()

    def parse_binary(self, data: bytes) -> BinaryCapabilityDescriptor:
        """Parse binary capability descriptor."""
        return BinaryCapabilityDescriptor.from_bytes(data)

    def generate_json(
        self, descriptor: CapabilityDescriptor, compact: bool = False
    ) -> str:
        """Generate JSON representation."""
        data = descriptor.to_dict()
        if compact:
            return json.dumps(data, separators=(",", ":"))
        else:
            return json.dumps(data, indent=2)

    def parse_json(self, json_str: str) -> CapabilityDescriptor:
        """Parse JSON capability descriptor."""
        data = json.loads(json_str)
        return self._dict_to_descriptor(data)

    def save_descriptor(
        self,
        descriptor: CapabilityDescriptor,
        file_path: Union[str, Path],
        format: str = "json",
    ) -> None:
        """Save capability descriptor to file."""
        path = Path(file_path)

        if format == "json":
            content = self.generate_json(descriptor)
            path.write_text(content, encoding="utf-8")
        elif format == "binary":
            content = self.generate_binary(descriptor)
            path.write_bytes(content)
        elif format == "base64":
            binary_content = self.generate_binary(descriptor)
            b64_content = base64.b64encode(binary_content).decode("ascii")
            path.write_text(b64_content, encoding="utf-8")
        else:
            raise ValueError(f"Unsupported format: {format}")

    def load_descriptor(
        self, file_path: Union[str, Path], format: Optional[str] = None
    ) -> CapabilityDescriptor:
        """Load capability descriptor from file."""
        path = Path(file_path)

        if format is None:
            # Auto-detect format from extension
            if path.suffix == ".json":
                format = "json"
            elif path.suffix in [".bin", ".tcp"]:
                format = "binary"
            elif path.suffix == ".b64":
                format = "base64"
            else:
                # Try to detect from content
                try:
                    content = path.read_text(encoding="utf-8")
                    if content.strip().startswith("{"):
                        format = "json"
                    else:
                        format = "base64"
                except UnicodeDecodeError:
                    format = "binary"

        if format == "json":
            content = path.read_text(encoding="utf-8")
            return self.parse_json(content)
        elif format == "binary":
            content = path.read_bytes()
            binary_desc = self.parse_binary(content)
            # Note: Binary format loses detailed information
            return self._binary_to_descriptor(binary_desc)
        elif format == "base64":
            content = path.read_text(encoding="utf-8").strip()
            binary_content = base64.b64decode(content)
            binary_desc = self.parse_binary(binary_content)
            return self._binary_to_descriptor(binary_desc)
        else:
            raise ValueError(f"Unsupported format: {format}")

    def validate_descriptor(self, descriptor: CapabilityDescriptor) -> List[str]:
        """Validate a capability descriptor and return any errors."""
        errors = []

        # Basic validation
        if not descriptor.name:
            errors.append("Tool name is required")
        if not descriptor.version:
            errors.append("Tool version is required")

        # Command validation
        command_names = set()
        for command in descriptor.commands:
            if not command.name:
                errors.append("Command name is required")
            elif command.name in command_names:
                errors.append(f"Duplicate command name: {command.name}")
            else:
                command_names.add(command.name)

            # Parameter validation
            param_names = set()
            for param in command.parameters:
                if not param.name:
                    errors.append(
                        f"Parameter name is required in command {command.name}"
                    )
                elif param.name in param_names:
                    errors.append(
                        f"Duplicate parameter name: {param.name} in command {command.name}"
                    )
                else:
                    param_names.add(param.name)

        return errors

    def register_generator(self, name: str, generator_class: Type) -> None:
        """Register a capability generator."""
        self._generators[name] = generator_class

    def register_adapter(self, name: str, adapter_class: Type) -> None:
        """Register a tool adapter."""
        self._adapters[name] = adapter_class

    def get_generator(self, name: str) -> Optional[Type]:
        """Get a registered generator."""
        return self._generators.get(name)

    def get_adapter(self, name: str) -> Optional[Type]:
        """Get a registered adapter."""
        return self._adapters.get(name)

    def _dict_to_descriptor(self, data: Dict[str, Any]) -> CapabilityDescriptor:
        """Convert dictionary to CapabilityDescriptor."""
        # This is a simplified implementation
        # In a full implementation, this would handle all nested objects
        return CapabilityDescriptor(
            name=data.get("name", ""),
            version=data.get("version", ""),
            description=data.get("description", ""),
            # Add more field mappings as needed
        )

    def _binary_to_descriptor(
        self, binary_desc: BinaryCapabilityDescriptor
    ) -> CapabilityDescriptor:
        """Convert binary descriptor to full CapabilityDescriptor."""
        # Create minimal descriptor from binary data
        descriptor = CapabilityDescriptor(
            name=f"binary_tool_{binary_desc.checksum:08x}",
            version="1.0.0",
            description="Tool loaded from binary descriptor",
        )

        # Set performance metrics
        descriptor.performance = PerformanceMetrics(
            max_file_size_mb=binary_desc.max_file_size_mb,
            avg_processing_time_ms=binary_desc.avg_processing_time_ms,
        )

        # Set capability flags
        descriptor.capability_flags = binary_desc.capability_flags

        return descriptor


# Backward-compatible alias used by older validation and researcher modules.
TCPProtocol = ToolCapabilityProtocol
