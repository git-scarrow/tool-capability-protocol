"""Static tool name → capability flags for Claude Code built-ins and common MCP tools."""

from __future__ import annotations

from typing import Final

from tcp.core.descriptors import CapabilityFlags

_F = CapabilityFlags

# Mirrors scripts/tcp_shadow_session._build_inventory; extend as new names stabilize.
STATIC_FLAG_BY_NAME: Final[dict[str, int]] = {
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
    # MCP filesystem
    "mcp__filesystem__read_file": int(_F.SUPPORTS_FILES),
    "mcp__filesystem__write_file": int(_F.SUPPORTS_FILES),
    "mcp__filesystem__read_multiple_files": int(_F.SUPPORTS_FILES),
    "mcp__filesystem__list_directory": int(_F.SUPPORTS_FILES),
    "mcp__filesystem__search_files": int(_F.SUPPORTS_FILES),
    "mcp__filesystem__directory_tree": int(_F.SUPPORTS_FILES),
    "mcp__filesystem__create_directory": int(_F.SUPPORTS_FILES),
    # MCP git
    "mcp__git__git_log": int(_F.SUPPORTS_FILES),
    "mcp__git__git_diff": int(_F.SUPPORTS_FILES),
    "mcp__git__git_status": int(_F.SUPPORTS_FILES),
    "mcp__git__git_commit": int(_F.SUPPORTS_FILES),
    "mcp__git__git_add": int(_F.SUPPORTS_FILES),
    "mcp__git__git_branch": int(_F.SUPPORTS_FILES),
    "mcp__git__git_checkout": int(_F.SUPPORTS_FILES),
    # MCP fetch
    "mcp__fetch__fetch": int(_F.SUPPORTS_NETWORK),
}
