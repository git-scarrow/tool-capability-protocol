"""JSON schema generator for TCP capabilities."""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..core.descriptors import (
    CapabilityDescriptor,
    CommandDescriptor,
    FormatDescriptor,
    FormatType,
    ParameterDescriptor,
    ParameterType,
    ProcessingMode,
)


class JSONGenerator:
    """Generates JSON schemas and representations of TCP capabilities."""

    def __init__(self, schema_version: str = "1.0.0"):
        """Initialize JSON generator."""
        self.schema_version = schema_version

    def generate_capability_schema(
        self, descriptor: CapabilityDescriptor
    ) -> Dict[str, Any]:
        """Generate JSON schema for a capability descriptor."""

        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"https://tcp.dev/schemas/{descriptor.name}/v{descriptor.version}",
            "title": f"{descriptor.name} Tool Capabilities",
            "description": descriptor.description,
            "type": "object",
            "properties": {
                "tool": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "const": descriptor.name},
                        "version": {"type": "string", "const": descriptor.version},
                        "description": {"type": "string"},
                        "author": {"type": "string"},
                        "license": {"type": "string"},
                        "homepage": {"type": "string", "format": "uri"},
                    },
                    "required": ["name", "version"],
                },
                "capabilities": {
                    "type": "object",
                    "properties": {
                        "input_formats": {
                            "type": "array",
                            "items": self._format_schema(),
                        },
                        "output_formats": {
                            "type": "array",
                            "items": self._format_schema(),
                        },
                        "commands": {"type": "array", "items": self._command_schema()},
                        "processing_modes": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": [mode.name.lower() for mode in ProcessingMode],
                            },
                        },
                        "performance": self._performance_schema(),
                    },
                },
            },
            "required": ["tool", "capabilities"],
            "additionalProperties": False,
        }

        return schema

    def generate_capability_document(
        self, descriptor: CapabilityDescriptor
    ) -> Dict[str, Any]:
        """Generate JSON document representing capabilities."""

        return {
            "schema_version": self.schema_version,
            "generated_at": datetime.utcnow().isoformat(),
            "tool": {
                "name": descriptor.name,
                "version": descriptor.version,
                "description": descriptor.description,
                "author": descriptor.author,
                "license": descriptor.license,
                "homepage": descriptor.homepage,
                "fingerprint": descriptor.get_fingerprint(),
            },
            "capabilities": {
                "input_formats": [
                    self._format_to_dict(f) for f in descriptor.input_formats
                ],
                "output_formats": [
                    self._format_to_dict(f) for f in descriptor.output_formats
                ],
                "commands": [self._command_to_dict(c) for c in descriptor.commands],
                "processing_modes": [
                    mode.name.lower() for mode in descriptor.processing_modes
                ],
                "capability_flags": descriptor.get_capability_flags(),
                "performance": self._performance_to_dict(descriptor.performance),
            },
            "technical": {
                "python_version": descriptor.python_version,
                "dependencies": descriptor.dependencies,
                "optional_dependencies": descriptor.optional_dependencies,
            },
            "metadata": {
                "created_at": (
                    descriptor.created_at.isoformat() if descriptor.created_at else None
                ),
                "updated_at": (
                    descriptor.updated_at.isoformat() if descriptor.updated_at else None
                ),
                "schema_version": descriptor.schema_version,
            },
        }

    def generate_openapi_info(self, descriptor: CapabilityDescriptor) -> Dict[str, Any]:
        """Generate OpenAPI info section."""

        return {
            "openapi": "3.0.3",
            "info": {
                "title": f"{descriptor.name} API",
                "description": descriptor.description,
                "version": descriptor.version,
                "contact": (
                    {"name": descriptor.author, "url": descriptor.homepage}
                    if descriptor.author or descriptor.homepage
                    else {}
                ),
                "license": {"name": descriptor.license} if descriptor.license else {},
            },
            "servers": [
                {
                    "url": "http://localhost:8000",
                    "description": "Local development server",
                }
            ],
        }

    def generate_command_examples(
        self, command: CommandDescriptor
    ) -> List[Dict[str, Any]]:
        """Generate example requests for a command."""

        examples = []

        # Use provided examples if available
        if command.examples:
            for i, example in enumerate(command.examples):
                examples.append(
                    {
                        "name": f"example_{i+1}",
                        "summary": f"Example {i+1}",
                        "value": example,
                    }
                )
        else:
            # Generate basic example
            example_params = {}
            for param in command.parameters:
                if param.required:
                    example_params[param.name] = self._generate_example_value(param)

            if example_params:
                examples.append(
                    {
                        "name": "basic_example",
                        "summary": "Basic usage example",
                        "value": example_params,
                    }
                )

        return examples

    def _format_schema(self) -> Dict[str, Any]:
        """Generate schema for format descriptor."""

        return {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "type": {
                    "type": "string",
                    "enum": [t.name.lower() for t in FormatType],
                },
                "extensions": {"type": "array", "items": {"type": "string"}},
                "mime_types": {"type": "array", "items": {"type": "string"}},
                "description": {"type": "string"},
                "max_size_mb": {"type": "integer", "minimum": 0},
                "encoding": {"type": "string"},
                "compression": {"type": "string"},
            },
            "required": ["name", "type"],
        }

    def _command_schema(self) -> Dict[str, Any]:
        """Generate schema for command descriptor."""

        return {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "parameters": {"type": "array", "items": self._parameter_schema()},
                "input_formats": {"type": "array", "items": self._format_schema()},
                "output_formats": {"type": "array", "items": self._format_schema()},
                "processing_modes": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [mode.name.lower() for mode in ProcessingMode],
                    },
                },
                "examples": {"type": "array", "items": {"type": "object"}},
                "error_codes": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "timeout_seconds": {"type": "integer", "minimum": 0},
                "rate_limit": {
                    "type": "object",
                    "properties": {
                        "requests_per_minute": {"type": "integer", "minimum": 0},
                        "requests_per_hour": {"type": "integer", "minimum": 0},
                    },
                },
            },
            "required": ["name"],
        }

    def _parameter_schema(self) -> Dict[str, Any]:
        """Generate schema for parameter descriptor."""

        return {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "type": {
                    "type": "string",
                    "enum": [t.name.lower() for t in ParameterType],
                },
                "required": {"type": "boolean"},
                "default": {},  # Any type
                "description": {"type": "string"},
                "enum_values": {"type": "array", "items": {"type": "string"}},
                "min_value": {"type": "number"},
                "max_value": {"type": "number"},
                "pattern": {"type": "string"},
                "examples": {"type": "array", "items": {}},  # Any type
            },
            "required": ["name", "type"],
        }

    def _performance_schema(self) -> Dict[str, Any]:
        """Generate schema for performance metrics."""

        return {
            "type": "object",
            "properties": {
                "avg_processing_time_ms": {"type": "integer", "minimum": 0},
                "max_file_size_mb": {"type": "integer", "minimum": 0},
                "memory_usage_mb": {"type": "integer", "minimum": 0},
                "cpu_cores": {"type": "integer", "minimum": 1},
                "gpu_required": {"type": "boolean"},
                "network_required": {"type": "boolean"},
                "disk_space_mb": {"type": "integer", "minimum": 0},
                "concurrent_requests": {"type": "integer", "minimum": 1},
                "requests_per_minute": {"type": "integer", "minimum": 0},
            },
        }

    def _format_to_dict(self, format_desc: FormatDescriptor) -> Dict[str, Any]:
        """Convert format descriptor to dictionary."""

        return {
            "name": format_desc.name,
            "type": format_desc.type.name.lower(),
            "extensions": format_desc.extensions,
            "mime_types": format_desc.mime_types,
            "description": format_desc.description,
            "max_size_mb": format_desc.max_size_mb,
            "encoding": format_desc.encoding,
            "compression": format_desc.compression,
        }

    def _command_to_dict(self, command: CommandDescriptor) -> Dict[str, Any]:
        """Convert command descriptor to dictionary."""

        return {
            "name": command.name,
            "description": command.description,
            "parameters": [self._parameter_to_dict(p) for p in command.parameters],
            "input_formats": [self._format_to_dict(f) for f in command.input_formats],
            "output_formats": [self._format_to_dict(f) for f in command.output_formats],
            "processing_modes": [
                mode.name.lower() for mode in command.processing_modes
            ],
            "examples": command.examples,
            "error_codes": command.error_codes,
            "timeout_seconds": command.timeout_seconds,
            "rate_limit": command.rate_limit,
        }

    def _parameter_to_dict(self, param: ParameterDescriptor) -> Dict[str, Any]:
        """Convert parameter descriptor to dictionary."""

        return {
            "name": param.name,
            "type": param.type.name.lower(),
            "required": param.required,
            "default": param.default,
            "description": param.description,
            "enum_values": param.enum_values,
            "min_value": param.min_value,
            "max_value": param.max_value,
            "pattern": param.pattern,
            "examples": param.examples,
        }

    def _performance_to_dict(self, performance) -> Dict[str, Any]:
        """Convert performance metrics to dictionary."""

        return {
            "avg_processing_time_ms": performance.avg_processing_time_ms,
            "max_file_size_mb": performance.max_file_size_mb,
            "memory_usage_mb": performance.memory_usage_mb,
            "cpu_cores": performance.cpu_cores,
            "gpu_required": performance.gpu_required,
            "network_required": performance.network_required,
            "disk_space_mb": performance.disk_space_mb,
            "concurrent_requests": performance.concurrent_requests,
            "requests_per_minute": performance.requests_per_minute,
        }

    def _generate_example_value(self, param: ParameterDescriptor) -> Any:
        """Generate example value for a parameter."""

        if param.examples:
            return param.examples[0]

        if param.default is not None:
            return param.default

        if param.enum_values:
            return param.enum_values[0]

        # Generate based on type
        if param.type == ParameterType.STRING:
            return "example_string"
        elif param.type == ParameterType.INTEGER:
            return param.min_value if param.min_value is not None else 42
        elif param.type == ParameterType.FLOAT:
            return param.min_value if param.min_value is not None else 3.14
        elif param.type == ParameterType.BOOLEAN:
            return True
        elif param.type == ParameterType.ARRAY:
            return ["item1", "item2"]
        elif param.type == ParameterType.OBJECT:
            return {"key": "value"}
        elif param.type == ParameterType.FILE:
            return "path/to/file.txt"
        else:
            return None

    def to_json(self, data: Dict[str, Any], compact: bool = False) -> str:
        """Convert dictionary to JSON string."""

        if compact:
            return json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        else:
            return json.dumps(data, indent=2, ensure_ascii=False)

    def from_json(self, json_str: str) -> Dict[str, Any]:
        """Parse JSON string to dictionary."""

        return json.loads(json_str)
