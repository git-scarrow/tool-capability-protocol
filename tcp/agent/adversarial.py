"""Adversarial task set for TCP-MT-6.

Tasks designed to confuse tool selection with near-collision names,
similar descriptions, and tempting-but-wrong tools in the corpus.
"""

from __future__ import annotations

from dataclasses import dataclass

from tcp.agent.tasks import AgentTask
from tcp.harness.models import ToolSelectionRequest


@dataclass(frozen=True)
class AdversarialTask:
    """Agent task with structured selection request for cold-path filtering."""

    agent_task: AgentTask
    selection_request: ToolSelectionRequest


def build_adversarial_tasks() -> list[AdversarialTask]:
    """Build adversarial tasks that stress-test tool selection.

    Each task has a near-collision scenario: the correct tool exists
    alongside 1-3 tempting-but-wrong alternatives with similar names
    or descriptions.
    """
    return [
        # --- Near-collision: fs-read-file vs fs-read-multiple ---
        # Both read files, but the prompt asks for a single file.
        AdversarialTask(
            agent_task=AgentTask(
                name="single file read (not multi)",
                prompt=(
                    "Read the single file at /etc/hostname and return its "
                    "contents. Do not use batch or multi-file tools."
                ),
                expected_tool="fs-read-file",
            ),
            selection_request=ToolSelectionRequest.from_kwargs(
                required_commands={"read_file"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        # --- Near-collision: git-diff vs git-diff-staged ---
        # Both show diffs, prompt specifically asks for unstaged.
        AdversarialTask(
            agent_task=AgentTask(
                name="unstaged diff (not staged)",
                prompt=(
                    "Show the unstaged changes in the working tree. "
                    "I want to see what hasn't been added to the index yet."
                ),
                expected_tool="git-diff",
            ),
            selection_request=ToolSelectionRequest.from_kwargs(
                required_commands={"git_diff"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        # --- Near-collision: git-log vs git-show ---
        # Both show commit info, prompt asks for history not a single commit.
        AdversarialTask(
            agent_task=AgentTask(
                name="commit history (not single commit)",
                prompt=(
                    "Show me the last 10 commits in the repository log "
                    "with their messages."
                ),
                expected_tool="git-log",
            ),
            selection_request=ToolSelectionRequest.from_kwargs(
                required_commands={"git_log"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        # --- Near-collision: fs-write-file vs fs-edit-file ---
        # Both modify files, prompt specifically asks to create a new file.
        AdversarialTask(
            agent_task=AgentTask(
                name="create new file (not edit)",
                prompt=(
                    "Create a new file at /tmp/output.txt with the content "
                    "'hello world'. The file does not exist yet."
                ),
                expected_tool="fs-write-file",
            ),
            selection_request=ToolSelectionRequest.from_kwargs(
                required_commands={"write_file"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        # --- Near-collision: exa-web-search vs c7-query-docs ---
        # Both search for information, but one requires network (denied offline).
        # The temptation is to use exa, but it should be denied.
        AdversarialTask(
            agent_task=AgentTask(
                name="library docs (not web search)",
                prompt=(
                    "Look up the documentation for the React useState hook. "
                    "Use the library documentation tool, not a web search."
                ),
                expected_tool="c7-query-docs",
            ),
            selection_request=ToolSelectionRequest.from_kwargs(
                required_commands={"query_docs"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        # --- Near-collision: fs-list-directory vs fs-directory-tree ---
        # Both list directory contents, prompt asks for flat listing.
        AdversarialTask(
            agent_task=AgentTask(
                name="flat directory listing (not tree)",
                prompt=(
                    "List the files and folders in /home/user/projects. "
                    "Just the immediate contents, not a recursive tree."
                ),
                expected_tool="fs-list-directory",
            ),
            selection_request=ToolSelectionRequest.from_kwargs(
                required_commands={"list_directory"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        # --- Permission confusion: chmod (approval-required) vs fs-get-file-info ---
        # Both deal with file metadata, but chmod needs approval.
        AdversarialTask(
            agent_task=AgentTask(
                name="file metadata (not permissions change)",
                prompt=(
                    "Show me the size and modification time of /var/log/syslog. "
                    "I just want to read the metadata, not change anything."
                ),
                expected_tool="fs-get-file-info",
            ),
            selection_request=ToolSelectionRequest.from_kwargs(
                required_commands={"get_file_info"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        # --- Near-collision: rag-query-documents vs rag-query-passages ---
        # Both query the RAG system but at different granularity.
        AdversarialTask(
            agent_task=AgentTask(
                name="document search (not passage search)",
                prompt=(
                    "Search the knowledge base for documents about "
                    "database migration strategies. Return full documents, "
                    "not individual passages."
                ),
                expected_tool="rag-query-documents",
            ),
            selection_request=ToolSelectionRequest.from_kwargs(
                required_commands={"query_documents"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        # --- Near-collision: git-add vs git-commit ---
        # Both are write operations, prompt asks to stage only.
        AdversarialTask(
            agent_task=AgentTask(
                name="stage files (not commit)",
                prompt=(
                    "Stage the file src/main.py for the next commit. "
                    "Do not commit yet, just add it to the index."
                ),
                expected_tool="git-add",
            ),
            selection_request=ToolSelectionRequest.from_kwargs(
                required_commands={"git_add"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
        # --- Network confusion: browser-navigate (denied) vs fs-read-file ---
        # Both could "fetch" content, but browser needs network.
        AdversarialTask(
            agent_task=AgentTask(
                name="read local html (not browser)",
                prompt=(
                    "Read the HTML file at /tmp/index.html and show me "
                    "its contents. This is a local file, not a URL."
                ),
                expected_tool="fs-read-file",
            ),
            selection_request=ToolSelectionRequest.from_kwargs(
                required_commands={"read_file"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
        ),
    ]
