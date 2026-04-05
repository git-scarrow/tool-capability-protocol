"""Ambiguous task corpus for the TCP layered deterministic router.

Each task has 2-5 viable synthetic tools that all pass the same gating
filters, requiring the LLM to make the final selection based on prompt
context and tool descriptions.

Unlike the deterministic tasks in tasks.py (where filters narrow to 1 tool),
ambiguous tasks intentionally leave multiple survivors so the router's LLM
selection layer is exercised.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tcp.agent.tasks import AgentTask
from tcp.core.descriptors import CapabilityFlags
from tcp.harness.models import ToolRecord, ToolSelectionRequest


@dataclass(frozen=True)
class AmbiguousTask:
    """A task with multiple viable tools requiring LLM disambiguation."""

    agent_task: AgentTask
    selection_request: ToolSelectionRequest
    ambiguity_reason: str
    synthetic_tools: tuple[ToolRecord, ...] = field(default_factory=tuple)


def _tool(
    name: str,
    description: str,
    capability_flags: int,
    risk_level: str = "safe",
    input_formats: frozenset[str] | None = None,
    output_formats: frozenset[str] | None = None,
) -> ToolRecord:
    """Convenience constructor for synthetic ToolRecord instances."""
    return ToolRecord(
        tool_name=name,
        descriptor_source="synthetic",
        descriptor_version="1.0",
        capability_flags=capability_flags,
        risk_level=risk_level,
        commands=frozenset(),
        input_formats=input_formats or frozenset(),
        output_formats=output_formats or frozenset(),
        permission_level="unknown",
        avg_processing_time_ms=10.0,
        memory_usage_mb=64.0,
        rich_metadata={"description": description},
    )


def build_ambiguous_tasks() -> list[AmbiguousTask]:
    """Build the 6 ambiguous tasks for LLM selection validation."""
    _files_flag = int(CapabilityFlags.SUPPORTS_FILES)
    _net_flag = int(CapabilityFlags.SUPPORTS_NETWORK)

    return [
        # --- Task 1: Pattern search across files ---
        AmbiguousTask(
            agent_task=AgentTask(
                name="pattern search",
                prompt=(
                    "Search for the pattern 'TODO fix auth' across all Python "
                    "source files in the current directory. Use grep."
                ),
                expected_tool="grep",
            ),
            selection_request=ToolSelectionRequest.from_kwargs(
                required_capability_flags=_files_flag,
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
            ambiguity_reason=(
                "grep, ripgrep, and fs-search-files all support file access; "
                "prompt explicitly names grep"
            ),
            synthetic_tools=(
                _tool(
                    "grep",
                    "POSIX grep — search for patterns in files using regular expressions.",
                    _files_flag,
                    input_formats=frozenset({"text"}),
                    output_formats=frozenset({"text"}),
                ),
                _tool(
                    "ripgrep",
                    "ripgrep (rg) — fast regex search optimised for large codebases.",
                    _files_flag,
                    input_formats=frozenset({"text"}),
                    output_formats=frozenset({"json", "text"}),
                ),
                _tool(
                    "fs-search-files",
                    "MCP filesystem tool — search files by name or content glob.",
                    _files_flag,
                    input_formats=frozenset({"text"}),
                    output_formats=frozenset({"json"}),
                ),
            ),
        ),

        # --- Task 2: Fetch remote data ---
        AmbiguousTask(
            agent_task=AgentTask(
                name="fetch remote data",
                prompt=(
                    "Fetch the deployment status from "
                    "https://api.example.com/deploy and return the result as JSON. "
                    "Use http-fetch for structured JSON responses."
                ),
                expected_tool="http-fetch",
            ),
            selection_request=ToolSelectionRequest.from_kwargs(
                required_capability_flags=_net_flag,
                required_output_formats={"json"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
            ambiguity_reason=(
                "curl, http-fetch, and wget all have SUPPORTS_NETWORK and json output; "
                "prompt names http-fetch for structured JSON"
            ),
            synthetic_tools=(
                _tool(
                    "curl",
                    "curl — transfers data from/to servers using HTTP, FTP, and more.",
                    _net_flag,
                    input_formats=frozenset({"text"}),
                    output_formats=frozenset({"json", "text", "binary"}),
                ),
                _tool(
                    "http-fetch",
                    "http-fetch — structured HTTP client returning parsed JSON responses.",
                    _net_flag,
                    input_formats=frozenset({"json"}),
                    output_formats=frozenset({"json"}),
                ),
                _tool(
                    "wget",
                    "wget — non-interactive network downloader supporting HTTP/HTTPS/FTP.",
                    _net_flag,
                    input_formats=frozenset({"text"}),
                    output_formats=frozenset({"json", "text", "binary"}),
                ),
            ),
        ),

        # --- Task 3: Transform JSON ---
        AmbiguousTask(
            agent_task=AgentTask(
                name="json transform",
                prompt=(
                    "Extract the 'users[].email' field from the JSON file "
                    "/tmp/report.json. jq is the preferred tool for JSON transformations."
                ),
                expected_tool="jq",
            ),
            selection_request=ToolSelectionRequest.from_kwargs(
                required_capability_flags=_files_flag,
                required_input_formats={"json"},
                required_output_formats={"json"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
            ambiguity_reason=(
                "jq, python-exec, and node-exec all accept json input, emit json output, "
                "and support files; prompt names jq"
            ),
            synthetic_tools=(
                _tool(
                    "jq",
                    "jq — lightweight command-line JSON processor with its own query language.",
                    _files_flag,
                    input_formats=frozenset({"json"}),
                    output_formats=frozenset({"json", "text"}),
                ),
                _tool(
                    "python-exec",
                    "python-exec — run Python snippets; can parse and emit JSON.",
                    _files_flag,
                    input_formats=frozenset({"json", "text"}),
                    output_formats=frozenset({"json", "text"}),
                ),
                _tool(
                    "node-exec",
                    "node-exec — run Node.js snippets; has native JSON support.",
                    _files_flag,
                    input_formats=frozenset({"json", "text"}),
                    output_formats=frozenset({"json", "text"}),
                ),
            ),
        ),

        # --- Task 4: Write config file ---
        AmbiguousTask(
            agent_task=AgentTask(
                name="write config file",
                prompt=(
                    "Write the following YAML content to /etc/myapp/config.yaml. "
                    "Use fs-write-file to create or overwrite a file directly."
                ),
                expected_tool="fs-write-file",
            ),
            selection_request=ToolSelectionRequest.from_kwargs(
                required_capability_flags=_files_flag,
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
            ambiguity_reason=(
                "fs-write-file, tee, and editor all support file access; "
                "prompt names fs-write-file for direct file creation"
            ),
            synthetic_tools=(
                _tool(
                    "fs-write-file",
                    "fs-write-file — MCP filesystem tool to write or overwrite a file.",
                    _files_flag,
                    input_formats=frozenset({"text", "json"}),
                    output_formats=frozenset({"json"}),
                ),
                _tool(
                    "tee",
                    "tee — reads from stdin and writes to both stdout and one or more files.",
                    _files_flag,
                    input_formats=frozenset({"text"}),
                    output_formats=frozenset({"text"}),
                ),
                _tool(
                    "editor",
                    "editor — opens a file in the system's default text editor.",
                    _files_flag,
                    input_formats=frozenset({"text"}),
                    output_formats=frozenset({"json", "text"}),
                ),
            ),
        ),

        # --- Task 5: Check service status ---
        AmbiguousTask(
            agent_task=AgentTask(
                name="check service status",
                prompt=(
                    "Check whether the postgres service is running. "
                    "Use systemctl for systemd-managed services."
                ),
                expected_tool="systemctl",
            ),
            selection_request=ToolSelectionRequest.from_kwargs(
                required_capability_flags=0,
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
            ambiguity_reason=(
                "systemctl, ps, and pgrep all have no required flags and pass all filters; "
                "prompt names systemctl for systemd service management"
            ),
            synthetic_tools=(
                _tool(
                    "systemctl",
                    "systemctl — control the systemd system and service manager.",
                    0,
                    risk_level="safe",
                ),
                _tool(
                    "ps",
                    "ps — report a snapshot of current processes.",
                    0,
                    risk_level="safe",
                ),
                _tool(
                    "pgrep",
                    "pgrep — look up processes by name and signal them.",
                    0,
                    risk_level="safe",
                ),
            ),
        ),

        # --- Task 6: Diff two files ---
        AmbiguousTask(
            agent_task=AgentTask(
                name="diff files",
                prompt=(
                    "Show the differences between /tmp/config_v1.yaml and "
                    "/tmp/config_v2.yaml. Use diff for a standard unified diff."
                ),
                expected_tool="diff",
            ),
            selection_request=ToolSelectionRequest.from_kwargs(
                required_capability_flags=_files_flag,
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
            ambiguity_reason=(
                "diff, git-diff, and colordiff all support file access; "
                "prompt names diff for standard unified diff output"
            ),
            synthetic_tools=(
                _tool(
                    "diff",
                    "diff — compare files line by line and output a unified diff.",
                    _files_flag,
                    input_formats=frozenset({"text"}),
                    output_formats=frozenset({"text"}),
                ),
                _tool(
                    "git-diff",
                    "git-diff — show changes between git commits, working tree, etc.",
                    _files_flag,
                    input_formats=frozenset({"text"}),
                    output_formats=frozenset({"text", "json"}),
                ),
                _tool(
                    "colordiff",
                    "colordiff — wrapper around diff producing coloured output.",
                    _files_flag,
                    input_formats=frozenset({"text"}),
                    output_formats=frozenset({"text"}),
                ),
            ),
        ),
    ]
