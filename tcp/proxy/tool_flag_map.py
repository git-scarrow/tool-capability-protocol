"""Static tool name → capability flags and inventory helpers.

The shadow hooks need a stable per-session inventory artifact that is broad
enough to support replay without consulting the current machine state.
"""

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
    "ToolSearch": 0,
    "CronCreate": 0,
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
    "mcp__chatsearch__chatsearch_find": int(_F.SUPPORTS_NETWORK),
    "mcp__chatsearch__chatsearch_ask": int(_F.SUPPORTS_NETWORK),
    "mcp__chatsearch__chatsearch_watch_cycles": int(_F.SUPPORTS_NETWORK),
    "mcp__chatsearch__chatsearch_cycles": int(_F.SUPPORTS_NETWORK),
    "mcp__notion-agents__chat_with_agent": int(_F.SUPPORTS_NETWORK),
    "mcp__notion-agents__check_agent_response": int(_F.SUPPORTS_NETWORK),
    "mcp__notion-agents__query_database": int(_F.SUPPORTS_NETWORK),
    "mcp__notion-agents__describe_database": int(_F.SUPPORTS_NETWORK),
    "mcp__notion-agents__get_agent_config_raw": int(_F.SUPPORTS_NETWORK),
    "mcp__notion-agents__get_agent_triggers": int(_F.SUPPORTS_NETWORK),
    "mcp__notion-agents__get_conversation": int(_F.SUPPORTS_NETWORK),
    "mcp__notion-agents__list_agents": int(_F.SUPPORTS_NETWORK),
    "mcp__notion-agents__list_workspace_agents": int(_F.SUPPORTS_NETWORK),
    "mcp__notion-agents__set_agent_model": int(_F.SUPPORTS_NETWORK),
    "mcp__notion-agents__stamp_dispatch_consumed": int(_F.SUPPORTS_NETWORK),
    "mcp__notion-agents__build_dispatch_packet": int(_F.SUPPORTS_NETWORK),
    "mcp__notion-agents__check_gates": int(_F.SUPPORTS_NETWORK),
    "mcp__notion-agents__start_agent_run": int(_F.SUPPORTS_NETWORK),
    "mcp__oracle-remote__execute_query": int(_F.SUPPORTS_NETWORK),
    "mcp__oracle-remote__describe_table": int(_F.SUPPORTS_NETWORK),
    "mcp__writing-rag__query_passages": int(_F.SUPPORTS_NETWORK),
    "mcp__writing-rag__list_files": int(_F.SUPPORTS_NETWORK),
    "mcp__writing-rag__delete_file": int(_F.SUPPORTS_NETWORK),
    "mcp__writing-rag__ingest_file": int(_F.SUPPORTS_NETWORK),
    "mcp__context7__query-docs": int(_F.SUPPORTS_NETWORK),
    "mcp__context7__resolve-library-id": int(_F.SUPPORTS_NETWORK),
    "mcp__exa__web_search_exa": int(_F.SUPPORTS_NETWORK),
    "mcp__exa__get_code_context_exa": int(_F.SUPPORTS_NETWORK),
    "mcp__claude-projects__claude_get_instructions": int(_F.SUPPORTS_NETWORK),
    "mcp__claude-projects__claude_sync_docs": int(_F.SUPPORTS_NETWORK),
    "mcp__claude-projects__claude_list_chats": int(_F.SUPPORTS_NETWORK),
    "mcp__claude-projects__claude_list_docs": int(_F.SUPPORTS_NETWORK),
    "mcp__claude-projects__claude_list_projects": int(_F.SUPPORTS_NETWORK),
    "mcp__claude-projects__claude_read_chat": int(_F.SUPPORTS_NETWORK),
    "mcp__claude-projects__claude_read_doc": int(_F.SUPPORTS_NETWORK),
    "mcp__proxmox__get_vms": int(_F.SUPPORTS_NETWORK),
    "mcp__proxmox__get_nodes": int(_F.SUPPORTS_NETWORK),
    "mcp__proxmox__start_vm": int(_F.SUPPORTS_NETWORK),
    "mcp__proxmox__stop_vm": int(_F.SUPPORTS_NETWORK),
    "mcp__playwright__browser_click": int(_F.SUPPORTS_NETWORK),
    "mcp__playwright__browser_snapshot": int(_F.SUPPORTS_NETWORK),
    "mcp__playwright__browser_navigate": int(_F.SUPPORTS_NETWORK),
    "mcp__playwright__browser_run_code": int(_F.SUPPORTS_NETWORK),
    "mcp__playwright__browser_take_screenshot": int(_F.SUPPORTS_NETWORK),
    "mcp__playwright__browser_fill_form": int(_F.SUPPORTS_NETWORK),
    "mcp__playwright__browser_wait_for": int(_F.SUPPORTS_NETWORK),
    "mcp__playwright__browser_select_option": int(_F.SUPPORTS_NETWORK),
    "mcp__playwright__browser_type": int(_F.SUPPORTS_NETWORK),
    "mcp__claude_ai_tally__list_forms": int(_F.SUPPORTS_NETWORK),
    "mcp__claude_ai_tally__create_blocks": int(_F.SUPPORTS_NETWORK),
    "mcp__claude_ai_Vercel__deploy_to_vercel": int(_F.SUPPORTS_NETWORK),
    "mcp__claude_ai_Vercel__list_projects": int(_F.SUPPORTS_NETWORK),
    "mcp__claude_ai_Google_Calendar__gcal_list_events": int(_F.SUPPORTS_NETWORK),
    "mcp__claude_ai_Gmail__gmail_search_messages": int(_F.SUPPORTS_NETWORK),
    "mcp__plugin:Notion:notion__authenticate": int(_F.SUPPORTS_NETWORK),
    "mcp__plugin:Notion:notion__notion-fetch": int(_F.SUPPORTS_NETWORK),
    "mcp__plugin:Notion:notion__notion-create-pages": int(_F.SUPPORTS_NETWORK),
    "mcp__plugin:Notion:notion__notion-update-page": int(_F.SUPPORTS_NETWORK),
    "mcp__plugin:Notion:notion__notion-search": int(_F.SUPPORTS_NETWORK),
    "mcp__plugin:Notion:notion__notion-move-pages": int(_F.SUPPORTS_NETWORK),
    "mcp__bay-view-graph__list_emails": int(_F.SUPPORTS_NETWORK),
    "mcp__bay-view-graph__search_emails": int(_F.SUPPORTS_NETWORK),
    "mcp__bay-view-graph__get_email": int(_F.SUPPORTS_NETWORK),
    "mcp__bay-view-graph__reply_email": int(_F.SUPPORTS_NETWORK),
    "mcp__bay-view-graph__send_email": int(_F.SUPPORTS_NETWORK),
    "mcp__bay-view-graph__search_files": int(_F.SUPPORTS_NETWORK),
    "mcp__bay-view-graph__get_email_attachments": int(_F.SUPPORTS_NETWORK),
    "mcp__bay-view-graph__list_site_drives": int(_F.SUPPORTS_NETWORK),
    "mcp__bay-view-graph__list_drive_items": int(_F.SUPPORTS_NETWORK),
}

STATIC_INVENTORY_VERSION: Final[str] = "2.0"


def build_static_inventory() -> dict[str, object]:
    """Return the canonical shadow inventory artifact.

    The shadow hooks persist this exact structure at session start so replay can
    reason from captured evidence instead of the current machine state.
    """
    tools = [
        {"name": name, "flags": flags}
        for name, flags in sorted(STATIC_FLAG_BY_NAME.items())
    ]
    return {"tools": tools, "version": STATIC_INVENTORY_VERSION}
