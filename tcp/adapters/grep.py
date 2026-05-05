"""Grep adapter for Tool Capability Protocol."""

import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.descriptors import (
    CapabilityDescriptor,
    CommandDescriptor,
    FormatType,
    ParameterDescriptor,
    ParameterType,
    ProcessingMode,
)
from ..core.protocol import ToolCapabilityProtocol


class GrepAdapter:
    """TCP adapter for GNU grep and compatible implementations."""

    def __init__(self, grep_path: str = "grep"):
        """Initialize grep adapter."""
        self.grep_path = grep_path
        self.tcp = ToolCapabilityProtocol()
        self._version = self._detect_version()
        self._capabilities = self._detect_capabilities()

    def _detect_version(self) -> str:
        """Detect grep version."""
        try:
            result = subprocess.run(
                [self.grep_path, "--version"], capture_output=True, text=True, timeout=5
            )
            # Parse version from output like "grep (GNU grep) 3.11"
            match = re.search(r"grep.*?(\d+\.\d+)", result.stdout)
            return match.group(1) if match else "unknown"
        except Exception:
            return "unknown"

    def _detect_capabilities(self) -> Dict[str, bool]:
        """Auto-detect grep capabilities by testing flags."""
        capabilities = {}
        test_flags = {
            "perl_regex": ["-P", "--perl-regexp"],
            "extended_regex": ["-E", "--extended-regexp"],
            "color": ["--color=auto"],
            "binary_files": ["--binary-files=text"],
            "exclude": ["--exclude=test"],
            "include": ["--include=*.txt"],
            "recursive": ["-r", "--recursive"],
            "context": ["-C", "0"],
        }

        for cap, flags in test_flags.items():
            try:
                # Test if flag is supported
                result = subprocess.run(
                    [self.grep_path] + flags + ["test", "/dev/null"],
                    capture_output=True,
                    timeout=1,
                )
                capabilities[cap] = result.returncode != 2  # 2 = invalid option
            except Exception:
                capabilities[cap] = False

        return capabilities

    def create_descriptor(self) -> CapabilityDescriptor:
        """Create TCP descriptor for grep."""
        descriptor = self.tcp.create_descriptor(
            name="grep",
            version=self._version,
            description="Search for patterns in files",
            vendor="GNU",
            homepage="https://www.gnu.org/software/grep/",
        )

        # Add input/output formats
        descriptor.input_formats = [
            FormatType.TEXT,
            FormatType.STDIN,
            FormatType.FILE,
            FormatType.DIRECTORY,
        ]
        descriptor.output_formats = [
            FormatType.TEXT,
            FormatType.JSON,  # With custom wrapper
            FormatType.NULL,  # -q quiet mode
        ]

        # Add processing modes
        descriptor.processing_modes = [
            ProcessingMode.STREAMING,
            ProcessingMode.BATCH,
            ProcessingMode.RECURSIVE,
        ]

        # Add search command
        search_cmd = CommandDescriptor(
            name="search",
            description="Search for pattern in files",
            parameters=self._create_search_parameters(),
        )
        descriptor.commands.append(search_cmd)

        # Add performance metrics
        descriptor.performance_metrics = {
            "avg_throughput_mb_per_sec": 500,
            "memory_overhead_mb": 10,
            "startup_time_ms": 5,
            "regex_engine": (
                "PCRE2" if self._capabilities.get("perl_regex") else "POSIX"
            ),
            "max_pattern_length": 2048,
            "max_line_length": 32768,
        }

        # Add capability flags
        descriptor.capabilities = self._capabilities

        return descriptor

    def _create_search_parameters(self) -> List[ParameterDescriptor]:
        """Create parameter descriptors for search command."""
        params = [
            ParameterDescriptor(
                name="pattern",
                type=ParameterType.STRING,
                description="Search pattern (regex or literal)",
                required=True,
                max_length=2048,
            ),
            ParameterDescriptor(
                name="files",
                type=ParameterType.STRING_ARRAY,
                description="Files or directories to search",
                required=False,
                default=["stdin"],
            ),
            ParameterDescriptor(
                name="case_insensitive",
                type=ParameterType.BOOLEAN,
                description="Ignore case distinctions (-i)",
                required=False,
                default=False,
                flag="-i",
            ),
            ParameterDescriptor(
                name="invert_match",
                type=ParameterType.BOOLEAN,
                description="Select non-matching lines (-v)",
                required=False,
                default=False,
                flag="-v",
            ),
            ParameterDescriptor(
                name="word_regexp",
                type=ParameterType.BOOLEAN,
                description="Match whole words only (-w)",
                required=False,
                default=False,
                flag="-w",
            ),
            ParameterDescriptor(
                name="line_regexp",
                type=ParameterType.BOOLEAN,
                description="Match whole lines only (-x)",
                required=False,
                default=False,
                flag="-x",
            ),
            ParameterDescriptor(
                name="count",
                type=ParameterType.BOOLEAN,
                description="Print only count of matches (-c)",
                required=False,
                default=False,
                flag="-c",
            ),
            ParameterDescriptor(
                name="files_with_matches",
                type=ParameterType.BOOLEAN,
                description="Print only filenames with matches (-l)",
                required=False,
                default=False,
                flag="-l",
            ),
            ParameterDescriptor(
                name="files_without_matches",
                type=ParameterType.BOOLEAN,
                description="Print only filenames without matches (-L)",
                required=False,
                default=False,
                flag="-L",
            ),
            ParameterDescriptor(
                name="line_number",
                type=ParameterType.BOOLEAN,
                description="Prefix each line with line number (-n)",
                required=False,
                default=False,
                flag="-n",
            ),
            ParameterDescriptor(
                name="recursive",
                type=ParameterType.BOOLEAN,
                description="Recursively search directories (-r)",
                required=False,
                default=False,
                flag="-r",
                enabled=self._capabilities.get("recursive", False),
            ),
            ParameterDescriptor(
                name="quiet",
                type=ParameterType.BOOLEAN,
                description="Suppress normal output (-q)",
                required=False,
                default=False,
                flag="-q",
            ),
        ]

        # Add regex type parameters if supported
        if self._capabilities.get("extended_regex"):
            params.append(
                ParameterDescriptor(
                    name="extended_regexp",
                    type=ParameterType.BOOLEAN,
                    description="Use extended regular expressions (-E)",
                    required=False,
                    default=False,
                    flag="-E",
                )
            )

        if self._capabilities.get("perl_regex"):
            params.append(
                ParameterDescriptor(
                    name="perl_regexp",
                    type=ParameterType.BOOLEAN,
                    description="Use Perl regular expressions (-P)",
                    required=False,
                    default=False,
                    flag="-P",
                )
            )

        # Add context parameters
        if self._capabilities.get("context"):
            params.extend(
                [
                    ParameterDescriptor(
                        name="context_before",
                        type=ParameterType.INTEGER,
                        description="Lines of context before match (-B)",
                        required=False,
                        min_value=0,
                        max_value=999,
                        flag="-B",
                    ),
                    ParameterDescriptor(
                        name="context_after",
                        type=ParameterType.INTEGER,
                        description="Lines of context after match (-A)",
                        required=False,
                        min_value=0,
                        max_value=999,
                        flag="-A",
                    ),
                    ParameterDescriptor(
                        name="context",
                        type=ParameterType.INTEGER,
                        description="Lines of context before and after (-C)",
                        required=False,
                        min_value=0,
                        max_value=999,
                        flag="-C",
                    ),
                ]
            )

        # Add include/exclude parameters
        if self._capabilities.get("include"):
            params.extend(
                [
                    ParameterDescriptor(
                        name="include",
                        type=ParameterType.STRING_ARRAY,
                        description="Search only files matching pattern",
                        required=False,
                        flag="--include",
                    ),
                    ParameterDescriptor(
                        name="exclude",
                        type=ParameterType.STRING_ARRAY,
                        description="Skip files matching pattern",
                        required=False,
                        flag="--exclude",
                    ),
                ]
            )

        return params

    def execute(self, command: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Execute grep command with given parameters."""
        if command != "search":
            raise ValueError(f"Unknown command: {command}")

        # Build command line
        cmd = [self.grep_path]

        # Add boolean flags
        for param in self._create_search_parameters():
            if param.type == ParameterType.BOOLEAN and parameters.get(param.name):
                if param.flag:
                    cmd.append(param.flag)

        # Add value parameters
        for param in self._create_search_parameters():
            if param.type in [ParameterType.INTEGER, ParameterType.STRING]:
                value = parameters.get(param.name)
                if value is not None and param.flag:
                    cmd.extend([param.flag, str(value)])

        # Add array parameters
        for param in self._create_search_parameters():
            if param.type == ParameterType.STRING_ARRAY:
                values = parameters.get(param.name, [])
                if values and param.flag:
                    for value in values:
                        cmd.extend([param.flag, value])

        # Add pattern (required)
        pattern = parameters.get("pattern")
        if not pattern:
            raise ValueError("Pattern is required")
        cmd.append(pattern)

        # Add files
        files = parameters.get("files", [])
        if files and files != ["stdin"]:
            cmd.extend(files)

        # Execute
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=parameters.get("timeout", 30),
            )

            return {
                "success": result.returncode == 0,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "command": cmd,
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": "Command timed out",
                "command": cmd,
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "command": cmd,
            }


def register_adapter():
    """Register grep adapter with TCP."""
    from ..core.registry import global_registry

    global_registry.register_adapter("grep", GrepAdapter)
