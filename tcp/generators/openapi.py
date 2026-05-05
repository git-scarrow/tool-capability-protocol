"""OpenAPI specification generator for TCP capabilities."""

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


class OpenAPIGenerator:
    """Generates OpenAPI 3.0 specifications from TCP capabilities."""

    def __init__(self, api_version: str = "3.0.3"):
        """Initialize OpenAPI generator."""
        self.api_version = api_version

    def generate_specification(
        self, descriptor: CapabilityDescriptor
    ) -> Dict[str, Any]:
        """Generate complete OpenAPI specification."""

        spec = {
            "openapi": self.api_version,
            "info": self._generate_info(descriptor),
            "servers": self._generate_servers(),
            "paths": self._generate_paths(descriptor),
            "components": self._generate_components(descriptor),
            "tags": self._generate_tags(descriptor),
        }

        return spec

    def generate_paths(self, descriptor: CapabilityDescriptor) -> Dict[str, Any]:
        """Generate OpenAPI paths for all commands."""

        paths = {}

        for command in descriptor.commands:
            # Generate REST endpoint path
            command_path = f"/{command.name.replace('_', '-')}"

            paths[command_path] = {
                "post": self._generate_operation(command, descriptor)
            }

            # Add GET endpoint for commands that don't require input
            if not command.parameters or all(
                not p.required for p in command.parameters
            ):
                paths[command_path]["get"] = self._generate_get_operation(
                    command, descriptor
                )

        # Add capability discovery endpoints
        paths["/capabilities"] = {
            "get": {
                "summary": "Get tool capabilities",
                "description": "Retrieve the complete capability descriptor for this tool",
                "tags": ["capabilities"],
                "responses": {
                    "200": {
                        "description": "Tool capabilities",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/CapabilityDescriptor"
                                }
                            }
                        },
                    }
                },
            }
        }

        paths["/health"] = {
            "get": {
                "summary": "Health check",
                "description": "Check if the tool is available and responsive",
                "tags": ["system"],
                "responses": {
                    "200": {
                        "description": "Tool is healthy",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "status": {"type": "string", "example": "ok"},
                                        "timestamp": {
                                            "type": "string",
                                            "format": "date-time",
                                        },
                                        "version": {"type": "string"},
                                    },
                                }
                            }
                        },
                    }
                },
            }
        }

        return paths

    def _generate_info(self, descriptor: CapabilityDescriptor) -> Dict[str, Any]:
        """Generate OpenAPI info section."""

        info = {
            "title": f"{descriptor.name} API",
            "description": descriptor.description or f"API for {descriptor.name} tool",
            "version": descriptor.version,
            "termsOfService": "https://example.com/terms",
            "contact": {},
            "license": {},
        }

        if descriptor.author:
            info["contact"]["name"] = descriptor.author
        if descriptor.homepage:
            info["contact"]["url"] = descriptor.homepage
        if descriptor.license:
            info["license"]["name"] = descriptor.license

        return info

    def _generate_servers(self) -> List[Dict[str, Any]]:
        """Generate server configurations."""

        return [
            {"url": "http://localhost:8000", "description": "Local development server"},
            {"url": "https://api.example.com", "description": "Production server"},
        ]

    def _generate_paths(self, descriptor: CapabilityDescriptor) -> Dict[str, Any]:
        """Generate all API paths."""
        return self.generate_paths(descriptor)

    def _generate_operation(
        self, command: CommandDescriptor, descriptor: CapabilityDescriptor
    ) -> Dict[str, Any]:
        """Generate OpenAPI operation for a command."""

        operation = {
            "summary": command.description or f"Execute {command.name}",
            "description": self._generate_operation_description(command),
            "tags": [self._get_command_tag(command)],
            "operationId": f"execute_{command.name}",
            "requestBody": self._generate_request_body(command),
            "responses": self._generate_responses(command),
            "parameters": self._generate_query_parameters(command),
        }

        # Add security if needed
        if any(
            "auth" in flag.lower() for flag in getattr(command, "security_flags", [])
        ):
            operation["security"] = [{"ApiKeyAuth": []}]

        # Add rate limiting info
        if command.rate_limit:
            operation["x-rate-limit"] = command.rate_limit

        # Add timeout info
        if command.timeout_seconds:
            operation["x-timeout"] = command.timeout_seconds

        return operation

    def _generate_get_operation(
        self, command: CommandDescriptor, descriptor: CapabilityDescriptor
    ) -> Dict[str, Any]:
        """Generate GET operation for commands without required parameters."""

        return {
            "summary": f"Get {command.name} (simple)",
            "description": f"Execute {command.name} with default parameters",
            "tags": [self._get_command_tag(command)],
            "operationId": f"get_{command.name}",
            "parameters": [
                {
                    "name": param.name,
                    "in": "query",
                    "required": param.required,
                    "schema": self._parameter_to_schema(param),
                    "description": param.description,
                }
                for param in command.parameters
                if not param.required
            ],
            "responses": self._generate_responses(command),
        }

    def _generate_request_body(self, command: CommandDescriptor) -> Dict[str, Any]:
        """Generate request body schema."""

        if not command.parameters:
            return {}

        properties = {}
        required = []

        for param in command.parameters:
            properties[param.name] = self._parameter_to_schema(param)
            if param.required:
                required.append(param.name)

        schema = {"type": "object", "properties": properties}

        if required:
            schema["required"] = required

        return {
            "required": True,
            "content": {
                "application/json": {
                    "schema": schema,
                    "examples": self._generate_examples(command),
                },
                "multipart/form-data": {"schema": schema},
            },
        }

    def _generate_responses(self, command: CommandDescriptor) -> Dict[str, Any]:
        """Generate response schemas."""

        responses = {
            "200": {
                "description": "Successful operation",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "status": {"type": "string", "example": "success"},
                                "result": {"type": "object"},
                                "metadata": {
                                    "type": "object",
                                    "properties": {
                                        "processing_time_ms": {"type": "integer"},
                                        "output_format": {"type": "string"},
                                        "file_size_bytes": {"type": "integer"},
                                    },
                                },
                            },
                        }
                    }
                },
            },
            "400": {
                "description": "Bad request",
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/Error"}
                    }
                },
            },
            "422": {
                "description": "Validation error",
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/ValidationError"}
                    }
                },
            },
            "500": {
                "description": "Internal server error",
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/Error"}
                    }
                },
            },
        }

        # Add specific output format responses
        for output_format in command.output_formats:
            mime_type = self._format_to_mime_type(output_format)
            if mime_type != "application/json":
                responses["200"]["content"][mime_type] = {
                    "schema": {"type": "string", "format": "binary"}
                }

        # Add custom error responses
        if command.error_codes:
            for code, description in command.error_codes.items():
                if code not in responses:
                    responses[code] = {
                        "description": description,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Error"}
                            }
                        },
                    }

        return responses

    def _generate_query_parameters(
        self, command: CommandDescriptor
    ) -> List[Dict[str, Any]]:
        """Generate query parameters for file uploads and optional params."""

        params = []

        # Add format parameter
        if command.output_formats:
            params.append(
                {
                    "name": "output_format",
                    "in": "query",
                    "required": False,
                    "schema": {
                        "type": "string",
                        "enum": [fmt.name for fmt in command.output_formats],
                        "default": (
                            command.output_formats[0].name
                            if command.output_formats
                            else None
                        ),
                    },
                    "description": "Desired output format",
                }
            )

        # Add processing mode parameter
        if command.processing_modes:
            params.append(
                {
                    "name": "processing_mode",
                    "in": "query",
                    "required": False,
                    "schema": {
                        "type": "string",
                        "enum": [
                            mode.name.lower() for mode in command.processing_modes
                        ],
                    },
                    "description": "Processing mode to use",
                }
            )

        return params

    def _generate_components(self, descriptor: CapabilityDescriptor) -> Dict[str, Any]:
        """Generate OpenAPI components section."""

        components = {
            "schemas": {
                "Error": {
                    "type": "object",
                    "properties": {
                        "error": {"type": "string"},
                        "message": {"type": "string"},
                        "code": {"type": "string"},
                        "timestamp": {"type": "string", "format": "date-time"},
                    },
                    "required": ["error", "message"],
                },
                "ValidationError": {
                    "type": "object",
                    "properties": {
                        "error": {"type": "string", "example": "validation_failed"},
                        "message": {"type": "string"},
                        "details": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "field": {"type": "string"},
                                    "message": {"type": "string"},
                                    "code": {"type": "string"},
                                },
                            },
                        },
                    },
                    "required": ["error", "message"],
                },
                "CapabilityDescriptor": self._generate_capability_schema(descriptor),
            },
            "securitySchemes": {
                "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
                "BearerAuth": {"type": "http", "scheme": "bearer"},
            },
        }

        return components

    def _generate_tags(self, descriptor: CapabilityDescriptor) -> List[Dict[str, Any]]:
        """Generate API tags."""

        tags = [
            {"name": "capabilities", "description": "Tool capability information"},
            {"name": "system", "description": "System health and status"},
        ]

        # Add tags for command categories
        command_categories = set()
        for command in descriptor.commands:
            category = self._get_command_tag(command)
            command_categories.add(category)

        for category in sorted(command_categories):
            tags.append(
                {"name": category, "description": f"{category.title()} operations"}
            )

        return tags

    def _parameter_to_schema(self, param: ParameterDescriptor) -> Dict[str, Any]:
        """Convert parameter to OpenAPI schema."""

        schema = {}

        # Basic type mapping
        if param.type == ParameterType.STRING:
            schema["type"] = "string"
        elif param.type == ParameterType.INTEGER:
            schema["type"] = "integer"
        elif param.type == ParameterType.FLOAT:
            schema["type"] = "number"
        elif param.type == ParameterType.BOOLEAN:
            schema["type"] = "boolean"
        elif param.type == ParameterType.ARRAY:
            schema["type"] = "array"
            schema["items"] = {"type": "string"}  # Default to string items
        elif param.type == ParameterType.OBJECT:
            schema["type"] = "object"
        elif param.type == ParameterType.FILE:
            schema["type"] = "string"
            schema["format"] = "binary"

        # Add constraints
        if param.min_value is not None:
            schema["minimum"] = param.min_value
        if param.max_value is not None:
            schema["maximum"] = param.max_value
        if param.pattern:
            schema["pattern"] = param.pattern
        if param.enum_values:
            schema["enum"] = param.enum_values
        if param.default is not None:
            schema["default"] = param.default

        # Add description
        if param.description:
            schema["description"] = param.description

        # Add examples
        if param.examples:
            schema["examples"] = param.examples

        return schema

    def _format_to_mime_type(self, format_desc: FormatDescriptor) -> str:
        """Convert format descriptor to MIME type."""

        # Common MIME type mappings
        mime_mappings = {
            "pdf": "application/pdf",
            "json": "application/json",
            "xml": "application/xml",
            "html": "text/html",
            "csv": "text/csv",
            "txt": "text/plain",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "gif": "image/gif",
            "svg": "image/svg+xml",
        }

        # Check explicit MIME types
        if format_desc.mime_types:
            return format_desc.mime_types[0]

        # Check name mapping
        format_name = format_desc.name.lower()
        if format_name in mime_mappings:
            return mime_mappings[format_name]

        # Check extension mapping
        for ext in format_desc.extensions:
            clean_ext = ext.lstrip(".")
            if clean_ext in mime_mappings:
                return mime_mappings[clean_ext]

        # Default fallback
        if format_desc.type == FormatType.TEXT:
            return "text/plain"
        elif format_desc.type == FormatType.BINARY:
            return "application/octet-stream"
        elif format_desc.type == FormatType.IMAGE:
            return "image/*"
        elif format_desc.type == FormatType.DOCUMENT:
            return "application/pdf"
        else:
            return "application/json"

    def _generate_operation_description(self, command: CommandDescriptor) -> str:
        """Generate detailed operation description."""

        desc = command.description or f"Execute the {command.name} command"

        if command.input_formats:
            formats = ", ".join(f.name for f in command.input_formats)
            desc += f"\n\nSupported input formats: {formats}"

        if command.output_formats:
            formats = ", ".join(f.name for f in command.output_formats)
            desc += f"\nSupported output formats: {formats}"

        if command.processing_modes:
            modes = ", ".join(m.name.lower() for m in command.processing_modes)
            desc += f"\nProcessing modes: {modes}"

        if command.timeout_seconds:
            desc += f"\nMaximum processing time: {command.timeout_seconds} seconds"

        return desc

    def _get_command_tag(self, command: CommandDescriptor) -> str:
        """Get tag category for a command."""

        name = command.name.lower()

        if any(word in name for word in ["extract", "parse", "read"]):
            return "extraction"
        elif any(word in name for word in ["convert", "transform", "process"]):
            return "processing"
        elif any(word in name for word in ["analyze", "detect", "classify"]):
            return "analysis"
        elif any(word in name for word in ["generate", "create", "build"]):
            return "generation"
        else:
            return "operations"

    def _generate_examples(self, command: CommandDescriptor) -> Dict[str, Any]:
        """Generate example requests for a command."""

        examples = {}

        if command.examples:
            for i, example in enumerate(command.examples):
                examples[f"example_{i+1}"] = {
                    "summary": f"Example {i+1}",
                    "value": example,
                }
        else:
            # Generate basic example
            example_data = {}
            for param in command.parameters:
                if param.required:
                    if param.examples:
                        example_data[param.name] = param.examples[0]
                    elif param.default is not None:
                        example_data[param.name] = param.default
                    elif param.enum_values:
                        example_data[param.name] = param.enum_values[0]
                    else:
                        example_data[param.name] = self._generate_example_value(param)

            if example_data:
                examples["basic"] = {
                    "summary": "Basic usage example",
                    "value": example_data,
                }

        return examples

    def _generate_example_value(self, param: ParameterDescriptor) -> Any:
        """Generate example value for a parameter."""

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

    def _generate_capability_schema(
        self, descriptor: CapabilityDescriptor
    ) -> Dict[str, Any]:
        """Generate schema for capability descriptor."""

        return {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "version": {"type": "string"},
                "description": {"type": "string"},
                "author": {"type": "string"},
                "license": {"type": "string"},
                "homepage": {"type": "string", "format": "uri"},
                "commands": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "parameters": {"type": "array"},
                            "input_formats": {"type": "array"},
                            "output_formats": {"type": "array"},
                        },
                    },
                },
                "input_formats": {"type": "array"},
                "output_formats": {"type": "array"},
                "processing_modes": {"type": "array"},
                "performance": {
                    "type": "object",
                    "properties": {
                        "avg_processing_time_ms": {"type": "integer"},
                        "max_file_size_mb": {"type": "integer"},
                        "memory_usage_mb": {"type": "integer"},
                        "concurrent_requests": {"type": "integer"},
                    },
                },
            },
            "required": ["name", "version"],
        }

    def to_yaml(self, spec: Dict[str, Any]) -> str:
        """Convert OpenAPI spec to YAML format."""
        try:
            import yaml

            return yaml.dump(spec, default_flow_style=False, sort_keys=False)
        except ImportError:
            raise ImportError("PyYAML is required for YAML output")

    def to_json(self, spec: Dict[str, Any], compact: bool = False) -> str:
        """Convert OpenAPI spec to JSON format."""
        import json

        if compact:
            return json.dumps(spec, separators=(",", ":"))
        else:
            return json.dumps(spec, indent=2)
