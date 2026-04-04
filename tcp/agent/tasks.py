"""EXP-2 agent task definitions with natural language prompts.

Each task maps to one of the 12 MT-3 benchmark tasks but adds a natural
language prompt for the agent loop and an expected tool name for
correctness checking.
"""

from __future__ import annotations

from dataclasses import dataclass

from tcp.core.descriptors import CapabilityFlags
from tcp.harness.models import ToolSelectionRequest


@dataclass(frozen=True)
class AgentTask:
    """A single agent loop task with natural language prompt."""

    name: str
    prompt: str
    expected_tool: str | None  # None when no tool should be selected
    selection_request: ToolSelectionRequest | None = None


def build_agent_tasks() -> list[AgentTask]:
    """Build the 12 EXP-2 agent tasks aligned with MT-3 benchmarks."""
    return [
        # --- Offline local tasks ---
        AgentTask(
            name="local file read",
            prompt="Read the file at /tmp/data.json and show me its contents.",
            expected_tool="fs-read-file",
            selection_request=ToolSelectionRequest.from_kwargs(
                required_commands={"read_file"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        AgentTask(
            name="local json processing",
            prompt=(
                "Use jq to extract the 'name' field from the JSON file "
                "at /tmp/data.json."
            ),
            expected_tool="jq",
            selection_request=ToolSelectionRequest.from_kwargs(
                required_commands={"jq"},
                required_input_formats={"json"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        AgentTask(
            name="git status check",
            prompt="Show me the current git status of the repository.",
            expected_tool="git-status",
            selection_request=ToolSelectionRequest.from_kwargs(
                required_commands={"git_status"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        AgentTask(
            name="file search",
            prompt=(
                "Use the filesystem search tool to find all files whose "
                "names match '*.config.json' under the /workspace directory."
            ),
            expected_tool="fs-search-files",
            selection_request=ToolSelectionRequest.from_kwargs(
                required_commands={"search_files"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        AgentTask(
            name="semantic document search",
            prompt=(
                "Find documents in the knowledge base related to "
                "authentication and session management."
            ),
            expected_tool="rag-query-documents",
            selection_request=ToolSelectionRequest.from_kwargs(
                required_commands={"query_documents"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        AgentTask(
            name="git commit (write)",
            prompt="Commit the currently staged changes with the message 'fix: resolve auth bug'.",
            expected_tool="git-commit",
            selection_request=ToolSelectionRequest.from_kwargs(
                required_commands={"git_commit"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        # --- Approval-gated tasks ---
        AgentTask(
            name="approval-guarded privileged command",
            prompt="Change the permissions of /etc/config.json to 644.",
            expected_tool="chmod",
            selection_request=ToolSelectionRequest.from_kwargs(
                required_commands={"chmod"},
                preferred_criteria="speed",
                require_auto_approval=True,
            ),
        ),
        AgentTask(
            name="approval-guarded systemctl",
            prompt="Restart the nginx service using systemctl.",
            expected_tool="systemctl",
            selection_request=ToolSelectionRequest.from_kwargs(
                required_commands={"systemctl"},
                preferred_criteria="speed",
                require_auto_approval=True,
            ),
        ),
        # --- Network tasks (should fail in offline env) ---
        AgentTask(
            name="network fetch denied offline",
            prompt="Fetch the contents of https://api.example.com/data.",
            expected_tool=None,
            selection_request=ToolSelectionRequest.from_kwargs(
                required_commands={"fetch"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        AgentTask(
            name="database query denied offline",
            prompt="Run the SQL query: SELECT * FROM users LIMIT 10.",
            expected_tool=None,
            selection_request=ToolSelectionRequest.from_kwargs(
                required_commands={"execute_query"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        # --- No-match tasks ---
        AgentTask(
            name="nonexistent command",
            prompt="Teleport the quantum state to the remote server.",
            expected_tool=None,
            selection_request=ToolSelectionRequest.from_kwargs(
                required_commands={"quantum_teleport"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        # --- Capability-flag tasks ---
        AgentTask(
            name="require JSON output",
            prompt=(
                "Use jq to extract the 'users' array from the JSON file "
                "at /tmp/response.json and pretty-print it."
            ),
            expected_tool="jq",
            selection_request=ToolSelectionRequest.from_kwargs(
                required_capability_flags=int(CapabilityFlags.JSON_OUTPUT),
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
    ]
