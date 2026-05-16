"""Complete TCP generation pipeline orchestrating all components."""

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..core.descriptors import CapabilityDescriptor
from .help_parser import HelpTextAnalysis, HelpTextParser
from .llm_extractor import (
    LangChainExtractor,
    LLMExtractor,
    LocalLLMExtractor,
    ToolCapabilities,
)
from .tcp_generator import TCPDescriptorGenerator


class TCPGenerationPipeline:
    """Complete pipeline for generating TCP descriptors from command help text."""

    def __init__(
        self,
        llm_provider: str = "auto",
        model: str = None,
        fallback_to_basic: bool = True,
        **llm_kwargs,
    ):
        """
        Initialize TCP generation pipeline.

        Args:
            llm_provider: "langchain_openai", "langchain_anthropic", "langchain_ollama",
                         "local_llm", "basic", or "auto"
            model: Model name (provider-specific)
            fallback_to_basic: Whether to fallback to basic parsing if LLM fails
            **llm_kwargs: Additional arguments for LLM initialization
        """
        self.logger = logging.getLogger(__name__)
        self.parser = HelpTextParser()
        self.tcp_generator = TCPDescriptorGenerator()
        self.fallback_to_basic = fallback_to_basic

        # Initialize LLM extractor
        self.llm_extractor = self._create_llm_extractor(
            llm_provider, model, **llm_kwargs
        )

    def _create_llm_extractor(
        self, provider: str, model: str, **kwargs
    ) -> Optional[LLMExtractor]:
        """Create appropriate LLM extractor based on provider."""
        if provider == "auto":
            # Try providers in order of preference
            for auto_provider in ["langchain_openai", "local_llm", "basic"]:
                extractor = self._create_llm_extractor(auto_provider, model, **kwargs)
                if extractor and extractor.is_available():
                    self.logger.info(f"Auto-selected LLM provider: {auto_provider}")
                    return extractor
            return None

        elif provider == "basic":
            return None  # Use basic parsing only

        elif provider.startswith("langchain_"):
            try:
                lang_provider = provider.replace("langchain_", "")
                return LangChainExtractor(provider=lang_provider, model=model, **kwargs)
            except ImportError as e:
                self.logger.warning(f"LangChain not available: {e}")
                return None

        elif provider == "local_llm":
            try:
                return LocalLLMExtractor(model=model or "llama3.1", **kwargs)
            except ImportError as e:
                self.logger.warning(f"Local LLM not available: {e}")
                return None

        else:
            raise ValueError(f"Unknown LLM provider: {provider}")

    def process_command(
        self, command: str, help_text: str = None, output_formats: List[str] = None
    ) -> Dict[str, Any]:
        """
        Process a command and generate TCP descriptors.

        Args:
            command: Command to analyze (e.g., "grep", "find", "ls -la")
            help_text: Pre-extracted help text (optional)
            output_formats: Desired output formats ("tcp", "json", "binary", "openapi")

        Returns:
            Dictionary with generated descriptors and metadata
        """
        if output_formats is None:
            output_formats = ["tcp", "json"]

        self.logger.info(f"Processing command: {command}")

        results = {
            "command": command,
            "success": False,
            "error": None,
            "analysis": None,
            "capabilities": None,
            "tcp_descriptor": None,
            "outputs": {},
        }

        try:
            # Step 1: Parse help text
            self.logger.info("Step 1: Parsing help text")
            analysis = self.parser.parse_help_text(command, help_text)
            results["analysis"] = asdict(analysis)

            if analysis.confidence_score < 0.3:
                self.logger.warning(
                    f"Low confidence in help text parsing: {analysis.confidence_score}"
                )

            # Step 2: Extract capabilities with LLM
            capabilities = None
            if self.llm_extractor and self.llm_extractor.is_available():
                try:
                    self.logger.info("Step 2: Extracting capabilities with LLM")
                    capabilities = self.llm_extractor.extract_capabilities(analysis)
                    results["capabilities"] = asdict(capabilities)

                    if capabilities.confidence_score < 0.5:
                        self.logger.warning(
                            f"Low confidence in LLM extraction: {capabilities.confidence_score}"
                        )

                except Exception as e:
                    self.logger.error(f"LLM extraction failed: {e}")
                    if not self.fallback_to_basic:
                        raise

            # Step 3: Fallback to basic extraction if needed
            if not capabilities and self.fallback_to_basic:
                self.logger.info("Step 2 (fallback): Using basic capability extraction")
                capabilities = self._basic_capability_extraction(analysis)
                results["capabilities"] = asdict(capabilities)

            if not capabilities:
                raise ValueError("Could not extract capabilities")

            # Step 3: Generate TCP descriptor
            self.logger.info("Step 3: Generating TCP descriptor")
            tcp_descriptor = self.tcp_generator.generate_tcp_descriptor(capabilities)
            results["tcp_descriptor"] = asdict(tcp_descriptor)

            # Step 4: Generate requested output formats
            self.logger.info("Step 4: Generating output formats")
            for output_format in output_formats:
                if output_format == "tcp":
                    results["outputs"]["tcp"] = tcp_descriptor
                elif output_format == "json":
                    results["outputs"][
                        "json"
                    ] = self.tcp_generator.generate_json_schema(capabilities)
                elif output_format == "binary":
                    binary_data = self.tcp_generator.generate_binary_descriptor(
                        capabilities
                    )
                    results["outputs"]["binary"] = {
                        "data": binary_data.hex(),
                        "size_bytes": len(binary_data),
                    }
                elif output_format == "openapi":
                    results["outputs"]["openapi"] = self._generate_openapi_spec(
                        capabilities
                    )
                else:
                    self.logger.warning(f"Unknown output format: {output_format}")

            results["success"] = True
            self.logger.info(f"Successfully processed command: {command}")

        except Exception as e:
            results["error"] = str(e)
            self.logger.error(f"Pipeline failed for command {command}: {e}")

        return results

    def batch_process_commands(
        self, commands: List[str], output_dir: str = None
    ) -> Dict[str, Any]:
        """
        Process multiple commands in batch.

        Args:
            commands: List of commands to process
            output_dir: Directory to save individual results (optional)

        Returns:
            Batch processing results
        """
        output_path = Path(output_dir) if output_dir else None
        if output_path:
            output_path.mkdir(parents=True, exist_ok=True)

        batch_results = {
            "total_commands": len(commands),
            "successful": 0,
            "failed": 0,
            "results": {},
            "summary": {},
        }

        for command in commands:
            self.logger.info(f"Batch processing: {command}")

            try:
                result = self.process_command(command)
                batch_results["results"][command] = result

                if result["success"]:
                    batch_results["successful"] += 1

                    # Save individual result if output directory specified
                    if output_path:
                        cmd_filename = (
                            command.replace(" ", "_").replace("/", "_") + ".json"
                        )
                        cmd_file = output_path / cmd_filename
                        with open(cmd_file, "w") as f:
                            json.dump(result, f, indent=2, default=str)

                else:
                    batch_results["failed"] += 1

            except Exception as e:
                batch_results["failed"] += 1
                batch_results["results"][command] = {
                    "command": command,
                    "success": False,
                    "error": str(e),
                }
                self.logger.error(f"Batch processing failed for {command}: {e}")

        # Generate summary
        batch_results["summary"] = {
            "success_rate": batch_results["successful"] / len(commands),
            "most_common_errors": self._analyze_errors(batch_results["results"]),
            "avg_confidence": self._calculate_avg_confidence(batch_results["results"]),
        }

        return batch_results

    def _basic_capability_extraction(
        self, analysis: HelpTextAnalysis
    ) -> ToolCapabilities:
        """Basic capability extraction without LLM."""
        capabilities = ToolCapabilities(
            tool_name=analysis.tool_name,
            version=analysis.version,
            description=analysis.description,
            extraction_method="basic_parser",
            confidence_score=min(analysis.confidence_score + 0.2, 0.6),
        )

        # Infer capabilities from options
        option_flags = [
            opt.long_flag or opt.short_flag or "" for opt in analysis.global_options
        ]
        option_text = " ".join(
            option_flags + [opt.description for opt in analysis.global_options]
        )

        # Basic capability inference
        capabilities.supports_stdin = any(
            keyword in option_text.lower() for keyword in ["stdin", "input", "-"]
        )

        capabilities.supports_files = any(
            keyword in option_text.lower()
            for keyword in ["file", "path", "-f", "--file"]
        )

        capabilities.supports_directories = any(
            keyword in option_text.lower()
            for keyword in ["directory", "dir", "folder", "-d", "--dir"]
        )

        capabilities.supports_recursion = any(
            keyword in option_text.lower()
            for keyword in ["recursive", "recurse", "-r", "-R", "--recursive"]
        )

        capabilities.supports_parallel = any(
            keyword in option_text.lower()
            for keyword in ["parallel", "thread", "job", "-j", "--parallel"]
        )

        # Infer formats from tool name and description
        text_tools = ["grep", "sed", "awk", "cat", "head", "tail", "sort", "uniq"]
        if any(tool in analysis.tool_name.lower() for tool in text_tools):
            capabilities.input_formats = ["text", "file"]
            capabilities.output_formats = ["text"]

        # Basic resource estimation
        large_tools = ["find", "grep", "docker", "git"]
        if any(tool in analysis.tool_name.lower() for tool in large_tools):
            capabilities.memory_usage = "medium"
            capabilities.cpu_usage = "medium"
        else:
            capabilities.memory_usage = "low"
            capabilities.cpu_usage = "low"

        return capabilities

    def _generate_openapi_spec(self, capabilities: ToolCapabilities) -> Dict[str, Any]:
        """Generate OpenAPI specification for capabilities."""
        spec = {
            "openapi": "3.0.0",
            "info": {
                "title": f"{capabilities.tool_name} Service API",
                "version": capabilities.version or "1.0.0",
                "description": f"RESTful API for {capabilities.tool_name} functionality",
            },
            "paths": {
                "/execute": {
                    "post": {
                        "summary": f"Execute {capabilities.tool_name} command",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ExecuteRequest"
                                    }
                                }
                            }
                        },
                        "responses": {
                            "200": {
                                "description": "Command executed successfully",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "$ref": "#/components/schemas/ExecuteResponse"
                                        }
                                    }
                                },
                            },
                            "400": {"description": "Invalid request"},
                            "500": {"description": "Execution error"},
                        },
                    }
                },
                "/capabilities": {
                    "get": {
                        "summary": "Get tool capabilities",
                        "responses": {
                            "200": {
                                "description": "Tool capabilities",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "$ref": "#/components/schemas/Capabilities"
                                        }
                                    }
                                },
                            }
                        },
                    }
                },
            },
            "components": {
                "schemas": {
                    "ExecuteRequest": {
                        "type": "object",
                        "required": ["command"],
                        "properties": {
                            "command": {"type": "string"},
                            "parameters": {"type": "object"},
                            "input_data": {"type": "string"},
                            "timeout": {"type": "integer", "default": 30},
                        },
                    },
                    "ExecuteResponse": {
                        "type": "object",
                        "properties": {
                            "success": {"type": "boolean"},
                            "exit_code": {"type": "integer"},
                            "stdout": {"type": "string"},
                            "stderr": {"type": "string"},
                            "execution_time_ms": {"type": "number"},
                        },
                    },
                    "Capabilities": {
                        "type": "object",
                        "properties": {
                            "tool_name": {"type": "string"},
                            "version": {"type": "string"},
                            "description": {"type": "string"},
                            "supports_stdin": {"type": "boolean"},
                            "supports_files": {"type": "boolean"},
                            "input_formats": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "output_formats": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    },
                }
            },
        }

        return spec

    def _analyze_errors(self, results: Dict[str, Any]) -> List[str]:
        """Analyze common errors from batch results."""
        errors = []
        for result in results.values():
            if not result.get("success") and result.get("error"):
                errors.append(result["error"])

        # Count error types (simplified)
        error_counts = {}
        for error in errors:
            error_type = error.split(":")[0] if ":" in error else error
            error_counts[error_type] = error_counts.get(error_type, 0) + 1

        return sorted(error_counts.items(), key=lambda x: x[1], reverse=True)

    def _calculate_avg_confidence(self, results: Dict[str, Any]) -> float:
        """Calculate average confidence score from results."""
        confidence_scores = []
        for result in results.values():
            if result.get("success") and result.get("capabilities"):
                confidence_scores.append(
                    result["capabilities"].get("confidence_score", 0)
                )

        return (
            sum(confidence_scores) / len(confidence_scores)
            if confidence_scores
            else 0.0
        )
