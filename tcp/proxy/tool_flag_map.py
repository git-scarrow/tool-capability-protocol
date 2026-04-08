"""Static tool name → capability flags for Claude Code built-ins and common MCP tools."""

from __future__ import annotations

from typing import Final

from tcp.core.descriptors import CapabilityFlags

_F = CapabilityFlags

# Mirrors scripts/tcp_shadow_session._build_inventory; extend as new names stabilize.
STATIC_FLAG_BY_NAME: Final[dict[str, int]] = {
    # ── Claude Code built-in tools ────────────────────────────────────────
    "Read": int(_F.SUPPORTS_FILES),
    "Write": int(_F.SUPPORTS_FILES),
    "Edit": int(_F.SUPPORTS_FILES),
    "MultiEdit": int(_F.SUPPORTS_FILES),
    "Glob": int(_F.SUPPORTS_FILES),
    "Grep": int(_F.SUPPORTS_FILES),
    "Bash": int(_F.SUPPORTS_FILES) | int(_F.SUPPORTS_NETWORK),
    "WebFetch": int(_F.SUPPORTS_NETWORK),
    "WebSearch": int(_F.SUPPORTS_NETWORK),
    "Think": 0,
    "NotebookEdit": int(_F.SUPPORTS_FILES),
    # Agent/orchestration — no external capability needed
    "Agent": 0,
    "SendMessageTool": 0,
    "EnterPlanMode": 0,
    "ExitPlanMode": 0,
    "AskUserQuestion": 0,
    "Skill": 0,
    # Task management — no external capability needed
    "TaskCreate": 0,
    "TaskUpdate": 0,
    "TaskList": 0,
    "TaskGet": 0,
    "TaskStop": 0,
    "TaskOutput": 0,
    "TodoWrite": 0,
    "TodoRead": 0,
    # MCP meta-tools — resource listing/reading
    "ReadMcpResourceTool": 0,
    "ListMcpResourcesTool": 0,
    # Config/settings
    "ConfigTool": 0,
    # ── MCP filesystem ────────────────────────────────────────────────────
    "mcp__filesystem__read_file": int(_F.SUPPORTS_FILES),
    "mcp__filesystem__write_file": int(_F.SUPPORTS_FILES),
    "mcp__filesystem__read_multiple_files": int(_F.SUPPORTS_FILES),
    "mcp__filesystem__list_directory": int(_F.SUPPORTS_FILES),
    "mcp__filesystem__search_files": int(_F.SUPPORTS_FILES),
    "mcp__filesystem__directory_tree": int(_F.SUPPORTS_FILES),
    "mcp__filesystem__create_directory": int(_F.SUPPORTS_FILES),
    "mcp__filesystem__list_directory_with_sizes": int(_F.SUPPORTS_FILES),
    "mcp__filesystem__get_file_info": int(_F.SUPPORTS_FILES),
    "mcp__filesystem__read_text_file": int(_F.SUPPORTS_FILES),
    "mcp__filesystem__read_media_file": int(_F.SUPPORTS_FILES),
    # ── MCP git ───────────────────────────────────────────────────────────
    "mcp__git__git_log": int(_F.SUPPORTS_FILES),
    "mcp__git__git_diff": int(_F.SUPPORTS_FILES),
    "mcp__git__git_diff_staged": int(_F.SUPPORTS_FILES),
    "mcp__git__git_diff_unstaged": int(_F.SUPPORTS_FILES),
    "mcp__git__git_status": int(_F.SUPPORTS_FILES),
    "mcp__git__git_show": int(_F.SUPPORTS_FILES),
    "mcp__git__git_branch": int(_F.SUPPORTS_FILES),
    "mcp__git__git_commit": int(_F.SUPPORTS_FILES),
    "mcp__git__git_add": int(_F.SUPPORTS_FILES),
    "mcp__git__git_checkout": int(_F.SUPPORTS_FILES),
    "mcp__git__git_reset": int(_F.SUPPORTS_FILES),
    "mcp__git__git_create_branch": int(_F.SUPPORTS_FILES),
    # ── MCP network services ──────────────────────────────────────────────
    "mcp__fetch__fetch": int(_F.SUPPORTS_NETWORK),
    "mcp__notion-agents__start_agent_run": int(_F.SUPPORTS_NETWORK),
    "mcp__proxmox__get_vms": int(_F.SUPPORTS_NETWORK),
}
