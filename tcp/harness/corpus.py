"""Real-tool corpus builder for TCP-MT-3 benchmarks.

Builds CapabilityDescriptors from known MCP tool registries and system
commands.  Each tool is categorized by its capability profile and assigned
appropriate capability_flags for bitmask routing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from tcp.core.descriptors import (
    CapabilityDescriptor,
    CapabilityFlags,
    CommandDescriptor,
    FormatDescriptor,
    FormatType,
    ParameterDescriptor,
    ParameterType,
    PerformanceMetrics,
    ProcessingMode,
)


@dataclass(frozen=True)
class CorpusEntry:
    """A descriptor plus its source metadata for corpus tracking."""

    descriptor: CapabilityDescriptor
    source: str  # e.g. "mcp:notion-agents", "mcp:playwright", "system"
    category: str  # e.g. "read-only", "write", "network", "auth-guarded"


def build_mcp_corpus() -> list[CorpusEntry]:
    """Build corpus from known MCP tool registries."""
    corpus: list[CorpusEntry] = []
    corpus.extend(_notion_agents_tools())
    corpus.extend(_playwright_tools())
    corpus.extend(_filesystem_tools())
    corpus.extend(_git_tools())
    corpus.extend(_fetch_tools())
    corpus.extend(_exa_tools())
    corpus.extend(_context7_tools())
    corpus.extend(_oracle_tools())
    corpus.extend(_gmail_tools())
    corpus.extend(_google_calendar_tools())
    corpus.extend(_vercel_tools())
    corpus.extend(_tally_tools())
    corpus.extend(_writing_rag_tools())
    corpus.extend(_nixos_tools())
    corpus.extend(_system_commands())
    return corpus


def build_mt3_corpus() -> tuple[list[CapabilityDescriptor], list[CorpusEntry]]:
    """Build the full MT-3 corpus, returning descriptors and entries."""
    entries = build_mcp_corpus()
    descriptors = [e.descriptor for e in entries]
    return descriptors, entries


def corpus_summary(entries: Sequence[CorpusEntry]) -> dict[str, object]:
    """Summarize corpus composition."""
    sources: dict[str, int] = {}
    categories: dict[str, int] = {}
    for e in entries:
        sources[e.source] = sources.get(e.source, 0) + 1
        categories[e.category] = categories.get(e.category, 0) + 1
    return {
        "total_descriptors": len(entries),
        "sources": sources,
        "categories": categories,
    }


# --- Helper ---

def _d(
    name: str,
    *,
    commands: list[str],
    flags: int = 0,
    input_fmt: str = "json",
    output_fmt: str = "json",
    latency_ms: int = 100,
    memory_mb: int = 16,
    mode: ProcessingMode = ProcessingMode.SYNC,
    deps: list[str] | None = None,
    description: str = "",
) -> CapabilityDescriptor:
    """Shorthand descriptor builder."""
    fmt_map = {
        "json": FormatType.JSON,
        "text": FormatType.TEXT,
        "binary": FormatType.BINARY,
        "xml": FormatType.XML,
        "image": FormatType.IMAGE,
    }
    return CapabilityDescriptor(
        name=name,
        version="1.0",
        description=description,
        commands=[CommandDescriptor(name=c) for c in commands],
        input_formats=[FormatDescriptor(name=input_fmt, type=fmt_map.get(input_fmt, FormatType.TEXT))],
        output_formats=[FormatDescriptor(name=output_fmt, type=fmt_map.get(output_fmt, FormatType.TEXT))],
        processing_modes=[mode],
        capability_flags=flags,
        dependencies=deps or [],
        performance=PerformanceMetrics(
            avg_processing_time_ms=latency_ms,
            memory_usage_mb=memory_mb,
        ),
    )


def _entry(descriptor: CapabilityDescriptor, source: str, category: str) -> CorpusEntry:
    return CorpusEntry(descriptor=descriptor, source=source, category=category)


# --- MCP: Notion Agents ---

def _notion_agents_tools() -> list[CorpusEntry]:
    src = "mcp:notion-agents"
    NET = CapabilityFlags.SUPPORTS_NETWORK
    AUTH = CapabilityFlags.AUTH_REQUIRED
    JSON = CapabilityFlags.JSON_OUTPUT

    return [
        _entry(_d("notion-chat-with-agent", commands=["chat_with_agent"], flags=NET | AUTH | JSON, latency_ms=5000, description="Send message to Notion AI agent"), src, "network-auth"),
        _entry(_d("notion-query-database", commands=["query_database"], flags=NET | AUTH | JSON, latency_ms=2000, description="Query a Notion database"), src, "network-auth"),
        _entry(_d("notion-describe-database", commands=["describe_database"], flags=NET | AUTH | JSON, latency_ms=1000, description="Show database schema"), src, "network-auth"),
        _entry(_d("notion-create-agent", commands=["create_agent"], flags=NET | AUTH | JSON, latency_ms=3000, description="Create a new Notion agent"), src, "network-auth-write"),
        _entry(_d("notion-update-agent", commands=["update_agent"], flags=NET | AUTH | JSON, latency_ms=2000, description="Update agent configuration"), src, "network-auth-write"),
        _entry(_d("notion-list-agents", commands=["list_agents"], flags=NET | AUTH | JSON, latency_ms=1000, description="List workspace agents"), src, "network-auth"),
        _entry(_d("notion-discover-agent", commands=["discover_agent"], flags=NET | AUTH | JSON, latency_ms=1500, description="Find agent by capability"), src, "network-auth"),
        _entry(_d("notion-dump-agent", commands=["dump_agent"], flags=NET | AUTH | JSON, latency_ms=1000, description="Export agent config"), src, "network-auth"),
        _entry(_d("notion-get-conversation", commands=["get_conversation"], flags=NET | AUTH | JSON, latency_ms=1500, description="Read agent conversation"), src, "network-auth"),
        _entry(_d("notion-handle-final-return", commands=["handle_final_return"], flags=NET | AUTH | JSON, latency_ms=2000, description="Process dispatch return"), src, "network-auth-write"),
    ]


# --- MCP: Playwright ---

def _playwright_tools() -> list[CorpusEntry]:
    src = "mcp:playwright"
    NET = CapabilityFlags.SUPPORTS_NETWORK
    FILE = CapabilityFlags.SUPPORTS_FILES

    return [
        _entry(_d("browser-navigate", commands=["browser_navigate"], flags=NET, latency_ms=3000, description="Navigate to URL"), src, "network"),
        _entry(_d("browser-click", commands=["browser_click"], flags=NET, latency_ms=500, description="Click element"), src, "network"),
        _entry(_d("browser-fill-form", commands=["browser_fill_form"], flags=NET, latency_ms=500, description="Fill form fields"), src, "network-write"),
        _entry(_d("browser-snapshot", commands=["browser_snapshot"], flags=NET, latency_ms=1000, output_fmt="text", description="Get page accessibility snapshot"), src, "network"),
        _entry(_d("browser-screenshot", commands=["browser_take_screenshot"], flags=NET | FILE, latency_ms=2000, output_fmt="image", description="Capture screenshot"), src, "network-file"),
        _entry(_d("browser-evaluate", commands=["browser_evaluate"], flags=NET, latency_ms=500, description="Run JavaScript in page"), src, "network"),
        _entry(_d("browser-press-key", commands=["browser_press_key"], flags=NET, latency_ms=200, description="Send keypress"), src, "network"),
        _entry(_d("browser-tabs", commands=["browser_tabs"], flags=NET, latency_ms=200, output_fmt="json", description="List open tabs"), src, "network"),
    ]


# --- MCP: Filesystem ---

def _filesystem_tools() -> list[CorpusEntry]:
    src = "mcp:filesystem"
    FILE = CapabilityFlags.SUPPORTS_FILES

    return [
        _entry(_d("fs-read-file", commands=["read_file"], flags=FILE, latency_ms=5, output_fmt="text", description="Read file contents"), src, "file-read"),
        _entry(_d("fs-read-multiple", commands=["read_multiple_files"], flags=FILE | CapabilityFlags.BATCH_PROCESSING, latency_ms=10, output_fmt="text", description="Read multiple files"), src, "file-read"),
        _entry(_d("fs-write-file", commands=["write_file"], flags=FILE, latency_ms=5, description="Write file contents"), src, "file-write"),
        _entry(_d("fs-edit-file", commands=["edit_file"], flags=FILE, latency_ms=10, description="Edit file with search/replace"), src, "file-write"),
        _entry(_d("fs-list-directory", commands=["list_directory"], flags=FILE, latency_ms=5, output_fmt="json", description="List directory contents"), src, "file-read"),
        _entry(_d("fs-directory-tree", commands=["directory_tree"], flags=FILE, latency_ms=20, output_fmt="json", description="Recursive directory tree"), src, "file-read"),
        _entry(_d("fs-search-files", commands=["search_files"], flags=FILE, latency_ms=50, output_fmt="json", description="Search files by pattern"), src, "file-read"),
        _entry(_d("fs-get-file-info", commands=["get_file_info"], flags=FILE, latency_ms=2, output_fmt="json", description="File metadata"), src, "file-read"),
        _entry(_d("fs-move-file", commands=["move_file"], flags=FILE, latency_ms=5, description="Move/rename file"), src, "file-write"),
        _entry(_d("fs-create-directory", commands=["create_directory"], flags=FILE, latency_ms=2, description="Create directory"), src, "file-write"),
    ]


# --- MCP: Git ---

def _git_tools() -> list[CorpusEntry]:
    src = "mcp:git"
    FILE = CapabilityFlags.SUPPORTS_FILES

    return [
        _entry(_d("git-status", commands=["git_status"], flags=FILE, latency_ms=10, output_fmt="text", description="Git working tree status"), src, "file-read"),
        _entry(_d("git-log", commands=["git_log"], flags=FILE, latency_ms=20, output_fmt="text", description="Git commit log"), src, "file-read"),
        _entry(_d("git-diff", commands=["git_diff"], flags=FILE, latency_ms=15, output_fmt="text", description="Git diff"), src, "file-read"),
        _entry(_d("git-diff-staged", commands=["git_diff_staged"], flags=FILE, latency_ms=15, output_fmt="text", description="Git staged diff"), src, "file-read"),
        _entry(_d("git-show", commands=["git_show"], flags=FILE, latency_ms=10, output_fmt="text", description="Git show commit"), src, "file-read"),
        _entry(_d("git-commit", commands=["git_commit"], flags=FILE, latency_ms=50, description="Git commit"), src, "file-write"),
        _entry(_d("git-add", commands=["git_add"], flags=FILE, latency_ms=10, description="Git stage files"), src, "file-write"),
        _entry(_d("git-branch", commands=["git_branch"], flags=FILE, latency_ms=5, output_fmt="text", description="Git branch operations"), src, "file-read"),
        _entry(_d("git-checkout", commands=["git_checkout"], flags=FILE, latency_ms=20, description="Git checkout branch"), src, "file-write"),
        _entry(_d("git-reset", commands=["git_reset"], flags=FILE, latency_ms=15, description="Git reset"), src, "file-write"),
    ]


# --- MCP: Fetch ---

def _fetch_tools() -> list[CorpusEntry]:
    src = "mcp:fetch"
    NET = CapabilityFlags.SUPPORTS_NETWORK
    return [
        _entry(_d("web-fetch", commands=["fetch"], flags=NET, latency_ms=2000, output_fmt="text", description="Fetch URL content"), src, "network"),
    ]


# --- MCP: Exa ---

def _exa_tools() -> list[CorpusEntry]:
    src = "mcp:exa"
    NET = CapabilityFlags.SUPPORTS_NETWORK
    return [
        _entry(_d("exa-web-search", commands=["web_search_exa"], flags=NET, latency_ms=3000, description="Exa web search"), src, "network"),
        _entry(_d("exa-company-research", commands=["company_research_exa"], flags=NET, latency_ms=5000, description="Exa company research"), src, "network"),
        _entry(_d("exa-code-context", commands=["get_code_context_exa"], flags=NET, latency_ms=3000, description="Exa code context"), src, "network"),
    ]


# --- MCP: Context7 ---

def _context7_tools() -> list[CorpusEntry]:
    src = "mcp:context7"
    NET = CapabilityFlags.SUPPORTS_NETWORK
    return [
        _entry(_d("c7-resolve-library", commands=["resolve-library-id"], flags=NET, latency_ms=1000, description="Resolve library ID"), src, "network"),
        _entry(_d("c7-query-docs", commands=["query-docs"], flags=NET, latency_ms=2000, output_fmt="text", description="Query library docs"), src, "network"),
    ]


# --- MCP: Oracle ---

def _oracle_tools() -> list[CorpusEntry]:
    src = "mcp:oracle-remote"
    NET = CapabilityFlags.SUPPORTS_NETWORK
    AUTH = CapabilityFlags.AUTH_REQUIRED
    return [
        _entry(_d("oracle-execute-query", commands=["execute_query"], flags=NET | AUTH, latency_ms=500, description="Execute SQL query"), src, "network-auth"),
        _entry(_d("oracle-list-schemas", commands=["list_schemas"], flags=NET | AUTH, latency_ms=200, description="List database schemas"), src, "network-auth"),
        _entry(_d("oracle-list-tables", commands=["list_tables"], flags=NET | AUTH, latency_ms=200, description="List tables in schema"), src, "network-auth"),
        _entry(_d("oracle-describe-table", commands=["describe_table"], flags=NET | AUTH, latency_ms=200, description="Table column definitions"), src, "network-auth"),
        _entry(_d("oracle-get-indexes", commands=["get_table_indexes"], flags=NET | AUTH, latency_ms=200, description="Table index definitions"), src, "network-auth"),
        _entry(_d("oracle-get-constraints", commands=["get_table_constraints"], flags=NET | AUTH, latency_ms=200, description="Table constraints"), src, "network-auth"),
    ]


# --- MCP: Gmail ---

def _gmail_tools() -> list[CorpusEntry]:
    src = "mcp:gmail"
    NET = CapabilityFlags.SUPPORTS_NETWORK
    AUTH = CapabilityFlags.AUTH_REQUIRED
    return [
        _entry(_d("gmail-search", commands=["gmail_search_messages"], flags=NET | AUTH, latency_ms=1500, description="Search Gmail messages"), src, "network-auth"),
        _entry(_d("gmail-read-message", commands=["gmail_read_message"], flags=NET | AUTH, latency_ms=800, output_fmt="text", description="Read email message"), src, "network-auth"),
        _entry(_d("gmail-read-thread", commands=["gmail_read_thread"], flags=NET | AUTH, latency_ms=1000, output_fmt="text", description="Read email thread"), src, "network-auth"),
        _entry(_d("gmail-create-draft", commands=["gmail_create_draft"], flags=NET | AUTH, latency_ms=1000, description="Create email draft"), src, "network-auth-write"),
        _entry(_d("gmail-get-profile", commands=["gmail_get_profile"], flags=NET | AUTH, latency_ms=500, description="Get Gmail profile"), src, "network-auth"),
        _entry(_d("gmail-list-labels", commands=["gmail_list_labels"], flags=NET | AUTH, latency_ms=500, description="List Gmail labels"), src, "network-auth"),
    ]


# --- MCP: Google Calendar ---

def _google_calendar_tools() -> list[CorpusEntry]:
    src = "mcp:google-calendar"
    NET = CapabilityFlags.SUPPORTS_NETWORK
    AUTH = CapabilityFlags.AUTH_REQUIRED
    return [
        _entry(_d("gcal-list-events", commands=["gcal_list_events"], flags=NET | AUTH, latency_ms=1000, description="List calendar events"), src, "network-auth"),
        _entry(_d("gcal-create-event", commands=["gcal_create_event"], flags=NET | AUTH, latency_ms=1500, description="Create calendar event"), src, "network-auth-write"),
        _entry(_d("gcal-update-event", commands=["gcal_update_event"], flags=NET | AUTH, latency_ms=1500, description="Update calendar event"), src, "network-auth-write"),
        _entry(_d("gcal-delete-event", commands=["gcal_delete_event"], flags=NET | AUTH, latency_ms=1000, description="Delete calendar event"), src, "network-auth-write"),
        _entry(_d("gcal-find-free-time", commands=["gcal_find_my_free_time"], flags=NET | AUTH, latency_ms=2000, description="Find free time slots"), src, "network-auth"),
    ]


# --- MCP: Vercel ---

def _vercel_tools() -> list[CorpusEntry]:
    src = "mcp:vercel"
    NET = CapabilityFlags.SUPPORTS_NETWORK
    AUTH = CapabilityFlags.AUTH_REQUIRED
    return [
        _entry(_d("vercel-deploy", commands=["deploy_to_vercel"], flags=NET | AUTH, latency_ms=10000, description="Deploy to Vercel"), src, "network-auth-write"),
        _entry(_d("vercel-list-projects", commands=["list_projects"], flags=NET | AUTH, latency_ms=1000, description="List Vercel projects"), src, "network-auth"),
        _entry(_d("vercel-get-deployment", commands=["get_deployment"], flags=NET | AUTH, latency_ms=800, description="Get deployment details"), src, "network-auth"),
        _entry(_d("vercel-get-build-logs", commands=["get_deployment_build_logs"], flags=NET | AUTH, latency_ms=1500, output_fmt="text", description="Get build logs"), src, "network-auth"),
        _entry(_d("vercel-get-runtime-logs", commands=["get_runtime_logs"], flags=NET | AUTH, latency_ms=1500, output_fmt="text", description="Get runtime logs"), src, "network-auth"),
    ]


# --- MCP: Tally ---

def _tally_tools() -> list[CorpusEntry]:
    src = "mcp:tally"
    NET = CapabilityFlags.SUPPORTS_NETWORK
    AUTH = CapabilityFlags.AUTH_REQUIRED
    return [
        _entry(_d("tally-create-form", commands=["create_new_form"], flags=NET | AUTH, latency_ms=2000, description="Create new Tally form"), src, "network-auth-write"),
        _entry(_d("tally-list-forms", commands=["list_forms"], flags=NET | AUTH, latency_ms=800, description="List Tally forms"), src, "network-auth"),
        _entry(_d("tally-fetch-submissions", commands=["fetch_submissions"], flags=NET | AUTH, latency_ms=1500, description="Fetch form submissions"), src, "network-auth"),
        _entry(_d("tally-save-form", commands=["save_form"], flags=NET | AUTH, latency_ms=1000, description="Save form changes"), src, "network-auth-write"),
    ]


# --- MCP: Writing RAG ---

def _writing_rag_tools() -> list[CorpusEntry]:
    src = "mcp:writing-rag"
    FILE = CapabilityFlags.SUPPORTS_FILES
    return [
        _entry(_d("rag-query-documents", commands=["query_documents"], flags=FILE, latency_ms=500, output_fmt="text", description="Semantic search over writing corpus"), src, "file-read"),
        _entry(_d("rag-query-passages", commands=["query_passages"], flags=FILE, latency_ms=500, output_fmt="text", description="Query specific passages"), src, "file-read"),
        _entry(_d("rag-ingest-file", commands=["ingest_file"], flags=FILE, latency_ms=2000, description="Ingest file into corpus"), src, "file-write"),
        _entry(_d("rag-list-files", commands=["list_files"], flags=FILE, latency_ms=100, output_fmt="json", description="List ingested files"), src, "file-read"),
    ]


# --- MCP: NixOS ---

def _nixos_tools() -> list[CorpusEntry]:
    src = "mcp:nixos"
    return [
        _entry(_d("nix-eval", commands=["nix"], flags=0, latency_ms=500, input_fmt="text", output_fmt="text", deps=["nix"], description="Evaluate Nix expression"), src, "system"),
        _entry(_d("nix-versions", commands=["nix_versions"], flags=CapabilityFlags.SUPPORTS_NETWORK, latency_ms=1000, output_fmt="json", description="Query Nix package versions"), src, "network"),
    ]


# --- System commands ---

def _system_commands() -> list[CorpusEntry]:
    src = "system"
    FILE = CapabilityFlags.SUPPORTS_FILES

    return [
        _entry(_d("grep", commands=["grep"], flags=FILE, latency_ms=5, input_fmt="text", output_fmt="text", description="Search file contents"), src, "file-read"),
        _entry(_d("find", commands=["find"], flags=FILE, latency_ms=20, output_fmt="text", description="Find files by criteria"), src, "file-read"),
        _entry(_d("sed", commands=["sed"], flags=FILE, latency_ms=5, input_fmt="text", output_fmt="text", description="Stream editor"), src, "file-write"),
        _entry(_d("awk", commands=["awk"], flags=FILE, latency_ms=5, input_fmt="text", output_fmt="text", description="Pattern processing"), src, "file-read"),
        _entry(_d("curl", commands=["curl"], flags=CapabilityFlags.SUPPORTS_NETWORK, latency_ms=2000, output_fmt="text", description="Transfer data from URLs"), src, "network"),
        _entry(_d("ssh", commands=["ssh"], flags=CapabilityFlags.SUPPORTS_NETWORK | CapabilityFlags.AUTH_REQUIRED, latency_ms=5000, deps=["ssh"], description="Secure shell"), src, "network-auth"),
        _entry(_d("rsync", commands=["rsync"], flags=FILE | CapabilityFlags.SUPPORTS_NETWORK, latency_ms=5000, description="Remote file sync"), src, "network-file"),
        _entry(_d("tar", commands=["tar"], flags=FILE, latency_ms=100, input_fmt="binary", output_fmt="binary", description="Archive utility"), src, "file-write"),
        _entry(_d("rm", commands=["rm"], flags=FILE, latency_ms=2, output_fmt="text", description="Remove files"), src, "file-write-destructive"),
        _entry(_d("chmod", commands=["chmod"], flags=FILE, latency_ms=2, output_fmt="text", deps=["sudo"], description="Change file permissions"), src, "file-write-privileged"),
        _entry(_d("systemctl", commands=["systemctl"], flags=0, latency_ms=200, output_fmt="text", deps=["sudo"], description="Systemd service control"), src, "system-privileged"),
        _entry(_d("docker", commands=["docker"], flags=CapabilityFlags.SUPPORTS_NETWORK | FILE, latency_ms=5000, output_fmt="text", deps=["docker"], description="Container management"), src, "system"),
        _entry(_d("python", commands=["python"], flags=FILE, latency_ms=100, output_fmt="text", description="Python interpreter"), src, "system"),
        _entry(_d("jq", commands=["jq"], flags=FILE | CapabilityFlags.JSON_OUTPUT, latency_ms=5, input_fmt="json", output_fmt="json", description="JSON processor"), src, "file-read"),
    ]
