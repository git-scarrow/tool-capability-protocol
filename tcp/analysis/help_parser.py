"""Help text parsing and initial analysis."""

import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class RawOption:
    """Raw option extracted from help text."""

    short_flag: Optional[str] = None
    long_flag: Optional[str] = None
    description: str = ""
    takes_value: bool = False
    value_type: Optional[str] = None
    is_required: bool = False
    example_values: List[str] = field(default_factory=list)


@dataclass
class RawCommand:
    """Raw command information extracted from help text."""

    name: str
    description: str = ""
    usage_pattern: str = ""
    options: List[RawOption] = field(default_factory=list)
    positional_args: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)


@dataclass
class HelpTextAnalysis:
    """Complete analysis of help text."""

    tool_name: str
    version: Optional[str] = None
    description: str = ""
    commands: List[RawCommand] = field(default_factory=list)
    global_options: List[RawOption] = field(default_factory=list)
    usage_patterns: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)
    confidence_score: float = 0.0
    parsing_errors: List[str] = field(default_factory=list)


class HelpTextParser:
    """Parse and analyze command help text to extract structured information."""

    def __init__(self):
        """Initialize help text parser."""
        self.option_patterns = [
            # Standard GNU long options with optional short
            r"^[ \t]*(-[a-zA-Z])?,?\s*(--[a-zA-Z0-9][-a-zA-Z0-9]*)\s*(?:=?([A-Z][A-Z0-9_]*))?\s*(.*)$",
            # Short options only
            r"^[ \t]*(-[a-zA-Z])\s*(?:([A-Z][A-Z0-9_]*))?\s*(.*)$",
            # BSD style options
            r"^[ \t]*(-[a-zA-Z0-9]+)\s*(?:\[([A-Z][A-Z0-9_]*)\])?\s*(.*)$",
        ]

        self.usage_patterns = [
            r"^[Uu]sage:\s*(.+)$",
            r"^[Ss]ynopsis:\s*(.+)$",
            r"^[ \t]*\S+\s+(.+)$",  # Basic command pattern
        ]

        self.section_headers = [
            r"^[A-Z][A-Z\s]+:?\s*$",  # ALL CAPS headers
            r"^[A-Za-z\s]+:$",  # Title case headers
        ]

    def extract_help_text(self, command: str, help_flags: List[str] = None) -> str:
        """Extract help text from a command."""
        if help_flags is None:
            help_flags = ["--help", "-h", "-?", "help"]

        for flag in help_flags:
            try:
                cmd_parts = shlex.split(command) + [flag]
                result = subprocess.run(
                    cmd_parts, capture_output=True, text=True, timeout=10
                )

                # Help text might be in stdout or stderr
                help_text = result.stdout or result.stderr
                if help_text.strip():
                    return help_text.strip()

            except (
                subprocess.TimeoutExpired,
                subprocess.SubprocessError,
                FileNotFoundError,
            ):
                continue

        raise ValueError(f"Could not extract help text for command: {command}")

    def detect_version(self, command: str) -> Optional[str]:
        """Detect version of the command."""
        version_flags = ["--version", "-V", "-v", "version"]

        for flag in version_flags:
            try:
                cmd_parts = shlex.split(command) + [flag]
                result = subprocess.run(
                    cmd_parts, capture_output=True, text=True, timeout=5
                )

                version_text = result.stdout or result.stderr
                if version_text.strip():
                    # Extract version number
                    version_match = re.search(r"(\d+\.\d+(?:\.\d+)?)", version_text)
                    if version_match:
                        return version_match.group(1)

            except (
                subprocess.TimeoutExpired,
                subprocess.SubprocessError,
                FileNotFoundError,
            ):
                continue

        return None

    def parse_help_text(self, command: str, help_text: str = None) -> HelpTextAnalysis:
        """Parse help text and extract structured information."""
        tool_name = command.split()[0] if " " in command else command

        if help_text is None:
            help_text = self.extract_help_text(command)

        analysis = HelpTextAnalysis(tool_name=tool_name)
        analysis.version = self.detect_version(command)

        lines = help_text.split("\n")
        current_section = None
        current_command = None

        i = 0
        while i < len(lines):
            line = lines[i].rstrip()

            # Skip empty lines
            if not line.strip():
                i += 1
                continue

            # Check for section headers
            section_match = self._match_section_header(line)
            if section_match:
                current_section = section_match.lower()
                i += 1
                continue

            # Check for usage patterns
            usage_match = self._match_usage_pattern(line)
            if usage_match:
                analysis.usage_patterns.append(usage_match)
                if not analysis.description and i > 0:
                    # Previous lines might be description
                    desc_lines = []
                    j = i - 1
                    while (
                        j >= 0
                        and lines[j].strip()
                        and not self._match_section_header(lines[j])
                    ):
                        desc_lines.insert(0, lines[j].strip())
                        j -= 1
                    analysis.description = " ".join(desc_lines)
                i += 1
                continue

            # Check for options
            option = self._parse_option_line(line, lines, i)
            if option:
                if current_command:
                    current_command.options.append(option)
                else:
                    analysis.global_options.append(option)
                i += 1
                continue

            # Check for examples
            if "example" in line.lower() or line.strip().startswith(tool_name):
                analysis.examples.append(line.strip())

            i += 1

        # If no explicit description found, try to infer from first lines
        if not analysis.description and lines:
            desc_candidates = []
            for line in lines[:5]:
                line = line.strip()
                if line and not any(
                    pattern in line.lower()
                    for pattern in ["usage:", "synopsis:", tool_name]
                ):
                    desc_candidates.append(line)
            if desc_candidates:
                analysis.description = " ".join(desc_candidates)

        # Create default command if no subcommands found
        if not analysis.commands:
            default_cmd = RawCommand(
                name="default",
                description=analysis.description,
                usage_pattern=(
                    analysis.usage_patterns[0] if analysis.usage_patterns else ""
                ),
                options=analysis.global_options.copy(),
            )
            analysis.commands.append(default_cmd)

        # Calculate confidence score
        analysis.confidence_score = self._calculate_confidence(analysis)

        return analysis

    def _match_section_header(self, line: str) -> Optional[str]:
        """Check if line is a section header."""
        for pattern in self.section_headers:
            if re.match(pattern, line.strip()):
                return line.strip().rstrip(":")
        return None

    def _match_usage_pattern(self, line: str) -> Optional[str]:
        """Check if line contains a usage pattern."""
        for pattern in self.usage_patterns:
            match = re.match(pattern, line.strip())
            if match:
                return match.group(1).strip()
        return None

    def _parse_option_line(
        self, line: str, all_lines: List[str], line_idx: int
    ) -> Optional[RawOption]:
        """Parse a single option line."""
        for pattern in self.option_patterns:
            match = re.match(pattern, line)
            if match:
                groups = match.groups()

                if len(groups) == 4:  # Full pattern with short, long, value, desc
                    short_flag = groups[0]
                    long_flag = groups[1]
                    value_type = groups[2]
                    description = groups[3]
                elif len(groups) == 3:  # Short only or partial pattern
                    if groups[1] and groups[1].startswith("--"):
                        short_flag = groups[0]
                        long_flag = groups[1]
                        description = groups[2]
                        value_type = None
                    else:
                        short_flag = groups[0]
                        long_flag = None
                        value_type = groups[1]
                        description = groups[2]
                else:
                    continue

                # Clean up description (might continue on next lines)
                desc_lines = [description] if description else []
                next_line_idx = line_idx + 1
                while (
                    next_line_idx < len(all_lines)
                    and all_lines[next_line_idx].startswith("    ")
                    and not self._parse_option_line(
                        all_lines[next_line_idx], all_lines, next_line_idx
                    )
                ):
                    desc_lines.append(all_lines[next_line_idx].strip())
                    next_line_idx += 1

                full_description = " ".join(desc_lines).strip()

                # Determine if option takes a value
                takes_value = bool(value_type) or any(
                    indicator in full_description.lower()
                    for indicator in [
                        "<",
                        "=",
                        "file",
                        "path",
                        "number",
                        "string",
                        "value",
                    ]
                )

                # Extract value type
                if not value_type and takes_value:
                    value_type = self._infer_value_type(full_description)

                return RawOption(
                    short_flag=short_flag,
                    long_flag=long_flag,
                    description=full_description,
                    takes_value=takes_value,
                    value_type=value_type,
                    is_required="required" in full_description.lower(),
                )

        return None

    def _infer_value_type(self, description: str) -> str:
        """Infer value type from option description."""
        desc_lower = description.lower()

        if any(word in desc_lower for word in ["file", "path", "directory"]):
            return "PATH"
        elif any(word in desc_lower for word in ["number", "count", "size", "port"]):
            return "NUMBER"
        elif any(word in desc_lower for word in ["url", "uri", "address"]):
            return "URL"
        elif any(word in desc_lower for word in ["format", "type", "mode"]):
            return "STRING"
        else:
            return "VALUE"

    def _calculate_confidence(self, analysis: HelpTextAnalysis) -> float:
        """Calculate confidence score for the analysis."""
        score = 0.0

        # Basic structure
        if analysis.description:
            score += 0.2
        if analysis.usage_patterns:
            score += 0.2
        if analysis.global_options:
            score += 0.3

        # Option quality
        for option in analysis.global_options:
            if option.long_flag or option.short_flag:
                score += 0.05
            if option.description:
                score += 0.05

        # Normalize options score
        if analysis.global_options:
            score = min(score, 1.0)

        return score
