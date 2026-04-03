"""EXP-2 agent task definitions with natural language prompts.

Each task maps to one of the 12 MT-3 benchmark tasks but adds a natural
language prompt for the agent loop and an expected tool name for
correctness checking.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentTask:
    """A single agent loop task with natural language prompt."""

    name: str
    prompt: str
    expected_tool: str | None  # None when no tool should be selected


def build_agent_tasks() -> list[AgentTask]:
    """Build the 12 EXP-2 agent tasks aligned with MT-3 benchmarks."""
    return [
        # --- Offline local tasks ---
        AgentTask(
            name="local file read",
            prompt="Read the file at /tmp/data.json and show me its contents.",
            expected_tool="fs-read-file",
        ),
        AgentTask(
            name="local json processing",
            prompt=(
                "Use jq to extract the 'name' field from the JSON file "
                "at /tmp/data.json."
            ),
            expected_tool="jq",
        ),
        AgentTask(
            name="git status check",
            prompt="Show me the current git status of the repository.",
            expected_tool="git-status",
        ),
        AgentTask(
            name="file search",
            prompt="Search for all files containing 'TODO' in the src directory.",
            expected_tool="fs-search-files",
        ),
        AgentTask(
            name="semantic document search",
            prompt=(
                "Find documents in the knowledge base related to "
                "authentication and session management."
            ),
            expected_tool="rag-query-documents",
        ),
        AgentTask(
            name="git commit (write)",
            prompt="Commit the currently staged changes with the message 'fix: resolve auth bug'.",
            expected_tool="git-commit",
        ),
        # --- Approval-gated tasks ---
        AgentTask(
            name="approval-guarded privileged command",
            prompt="Change the permissions of /etc/config.json to 644.",
            expected_tool="chmod",
        ),
        AgentTask(
            name="approval-guarded systemctl",
            prompt="Restart the nginx service using systemctl.",
            expected_tool="systemctl",
        ),
        # --- Network tasks (should fail in offline env) ---
        AgentTask(
            name="network fetch denied offline",
            prompt="Fetch the contents of https://api.example.com/data.",
            expected_tool=None,
        ),
        AgentTask(
            name="database query denied offline",
            prompt="Run the SQL query: SELECT * FROM users LIMIT 10.",
            expected_tool=None,
        ),
        # --- No-match tasks ---
        AgentTask(
            name="nonexistent command",
            prompt="Teleport the quantum state to the remote server.",
            expected_tool=None,
        ),
        # --- Capability-flag tasks ---
        AgentTask(
            name="require JSON output",
            prompt="Convert the input data to well-formatted JSON output.",
            expected_tool="jq",
        ),
    ]
