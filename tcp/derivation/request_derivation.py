"""TCP-DS-2: Request derivation contract.

Converts raw Claude Code hook data (user prompt + environment state) into a
structured ToolSelectionRequest for TCP's gate_tools() filter.

See: TCP-DS-2 Design Spec for precision/recall targets and audit protocol.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from tcp.core.descriptors import CapabilityFlags
from tcp.harness.models import ToolSelectionRequest

# ── Event types (mirrors Claude Code hook payloads) ───────────────────────────

@dataclass(frozen=True)
class SessionStartEvent:
    session_id: str
    permission_mode: str  # "default" | "plan" | "bypassPermissions" | "dangerouslySkipPermissions"
    cwd: str


@dataclass(frozen=True)
class PostToolUseEvent:
    session_id: str
    tool_name: str
    tool_input: dict
    tool_use_id: str
    tool_result_status: str  # "ok" | "error" | "cancelled" | "timeout"


# ── Constants ─────────────────────────────────────────────────────────────────

_SYSTEM_TOOLS = frozenset({
    "TodoRead", "TodoWrite", "MemoryRead", "MemoryWrite",
    "TaskCreate", "TaskUpdate", "TaskGet", "TaskList", "TaskOutput", "TaskStop",
    "Skill",  # Claude Code skill invocation — no TCP descriptor, not a task tool
})

_CONTINUATION_PROMPTS = frozenset({
    "yes", "no", "ok", "okay", "sure", "continue", "go", "go ahead",
    "proceed", "done", "next", "yep", "yup", "fine", "great",
})

_SYSTEM_DIRS = {"/", "/usr", "/etc", "/var", "/boot", "/sys", "/proc", "/bin", "/sbin"}

# Capability flag trigger patterns — tuples of (flag, compiled_patterns)
_FILE_PATTERNS = re.compile(
    r'\b(read|write|edit|create|save|open|load|delete|copy|move|rename|list)'
    r'.*\b(file|files|dir|directory|path|folder)\b'
    r'|[\w/.-]+\.[a-zA-Z]{1,6}\b'  # file extension
    r'|\b/(home|tmp|var|etc|usr)\b',
    re.IGNORECASE,
)
_NETWORK_PATTERNS = re.compile(
    r'https?://\S+'
    r'|\b(fetch|curl|wget|download|upload|http|api|endpoint|request|GET|POST)\b',
    re.IGNORECASE,
)
_AUTH_PATTERNS = re.compile(
    r'\b(sudo|root|admin|privilege|/etc/|systemctl|apt|yum|install globally|chmod|chown)\b',
    re.IGNORECASE,
)

# Output format patterns
_JSON_PATTERNS = re.compile(
    r'\b(json|structured output|parse|schema)\b', re.IGNORECASE
)
_BINARY_PATTERNS = re.compile(
    r'\b(image|screenshot|diagram|binary|pdf|zip|tar)\b'
    r'|\.(png|jpg|jpeg|gif|pdf|zip|tar|gz|bin)\b',
    re.IGNORECASE,
)

# ── Equivalence class taxonomy ────────────────────────────────────────────────

_EXACT_CLASSES: dict[str, str] = {
    # FILE_READ
    "Read": "FILE_READ",
    "mcp__filesystem__read_file": "FILE_READ",
    "mcp__filesystem__read_multiple_files": "FILE_READ",
    "mcp__filesystem__read_text_file": "FILE_READ",
    "mcp__filesystem__read_media_file": "FILE_READ",
    # FILE_WRITE
    "Write": "FILE_WRITE",
    "mcp__filesystem__write_file": "FILE_WRITE",
    "mcp__filesystem__create_directory": "FILE_WRITE",
    # FILE_EDIT
    "Edit": "FILE_EDIT",
    "MultiEdit": "FILE_EDIT",
    # SEARCH_TEXT
    "Grep": "SEARCH_TEXT",
    "mcp__filesystem__search_files": "SEARCH_TEXT",
    # SEARCH_FILES
    "Glob": "SEARCH_FILES",
    "LS": "SEARCH_FILES",
    "mcp__filesystem__list_directory": "SEARCH_FILES",
    "mcp__filesystem__directory_tree": "SEARCH_FILES",
    "mcp__filesystem__list_directory_with_sizes": "SEARCH_FILES",
    # WEB_FETCH
    "WebFetch": "WEB_FETCH",
    "WebSearch": "WEB_FETCH",
    "mcp__fetch__fetch": "WEB_FETCH",
    # THINK
    "Think": "THINK",
    # GIT_READ (static MCP variants)
    "mcp__git__git_log": "GIT_READ",
    "mcp__git__git_diff": "GIT_READ",
    "mcp__git__git_diff_staged": "GIT_READ",
    "mcp__git__git_diff_unstaged": "GIT_READ",
    "mcp__git__git_status": "GIT_READ",
    "mcp__git__git_show": "GIT_READ",
    "mcp__git__git_branch": "GIT_READ",
    # GIT_WRITE (static MCP variants)
    "mcp__git__git_add": "GIT_WRITE",
    "mcp__git__git_commit": "GIT_WRITE",
    "mcp__git__git_checkout": "GIT_WRITE",
    "mcp__git__git_reset": "GIT_WRITE",
    "mcp__git__git_create_branch": "GIT_WRITE",
}

_GIT_WRITE_COMMANDS = re.compile(
    r'^git\s+(add|commit|push|checkout\s+-b|rebase|merge|tag|rm|mv)\b',
    re.IGNORECASE,
)
_GIT_READ_COMMANDS = re.compile(r'^git\b', re.IGNORECASE)
_WEB_COMMANDS = re.compile(r'^(curl|wget|fetch)\b', re.IGNORECASE)


# ── Public API ────────────────────────────────────────────────────────────────

def derive_request(
    prompt: str,
    session: SessionStartEvent,
) -> ToolSelectionRequest:
    """Convert a UserPromptSubmit prompt + SessionStart into a ToolSelectionRequest.

    Implements the v1 rule-based derivation from TCP-DS-2.
    """
    capability_flags = _derive_capability_flags(prompt, session)
    output_formats = _derive_output_formats(prompt)
    require_auto_approval = _derive_approval_mode(session)

    return ToolSelectionRequest.from_kwargs(
        required_capability_flags=capability_flags,
        required_output_formats=output_formats,
        require_auto_approval=require_auto_approval,
        preferred_criteria="speed",
    )


def get_equivalence_class(tool_name: str, tool_input: dict) -> str:
    """Return the functional equivalence class for a tool call.

    Uses the taxonomy defined in TCP-DS-2 §4. Bash is disambiguated by
    inspecting tool_input['command'].
    """
    # Direct lookup first
    if tool_name in _EXACT_CLASSES:
        return _EXACT_CLASSES[tool_name]

    # Bash disambiguation
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        if _WEB_COMMANDS.match(cmd):
            return "WEB_FETCH"
        if _GIT_WRITE_COMMANDS.match(cmd):
            return "GIT_WRITE"
        if _GIT_READ_COMMANDS.match(cmd):
            return "GIT_READ"
        return "EXEC_COMMAND"

    # MCP tool prefix matching
    if tool_name.startswith("mcp__git__"):
        # Catch any git write patterns by name
        name_part = tool_name.split("__")[-1]
        if any(w in name_part for w in ("add", "commit", "push", "checkout", "reset", "create_branch")):
            return "GIT_WRITE"
        return "GIT_READ"

    # Singleton fallback
    return tool_name


def classify_unscorable(prompt: str, tool_event: PostToolUseEvent) -> bool:
    """Return True if this turn cannot support a coverage claim.

    Implements the 5 unscorable predicates from TCP-DS-2 §5.
    """
    # Predicate 1: system/housekeeping tool
    if tool_event.tool_name in _SYSTEM_TOOLS:
        return True

    # Predicate 2: empty or continuation prompt
    stripped = prompt.strip().lower()
    if not stripped or stripped in _CONTINUATION_PROMPTS or len(stripped.split()) <= 2:
        if not stripped or stripped in _CONTINUATION_PROMPTS:
            return True

    # Predicate 3: failed tool result
    if tool_event.tool_result_status != "ok":
        return True

    # Predicate 4: highly complex multi-capability prompt (popcount >= 3)
    flags = _derive_capability_flags_from_prompt_only(prompt)
    if bin(flags).count("1") >= 3:
        return True

    return False


# ── Internal helpers ──────────────────────────────────────────────────────────

def _derive_capability_flags(prompt: str, session: SessionStartEvent) -> int:
    flags = _derive_capability_flags_from_prompt_only(prompt)

    # System cwd implies AUTH_REQUIRED even for neutral prompts
    cwd = session.cwd.rstrip("/")
    if any(cwd == sd or cwd.startswith(sd + "/") for sd in _SYSTEM_DIRS):
        flags |= int(CapabilityFlags.AUTH_REQUIRED)

    return flags


def _derive_capability_flags_from_prompt_only(prompt: str) -> int:
    """Extract capability flags from prompt text alone (no session context)."""
    stripped = prompt.strip()

    # URLs and strong signals bypass the length gate
    has_url = bool(re.search(r'https?://\S+', stripped))
    too_short = len(stripped.split()) < 5 and not has_url

    if too_short:
        return 0

    flags = 0
    if _FILE_PATTERNS.search(prompt):
        flags |= int(CapabilityFlags.SUPPORTS_FILES)
    if _NETWORK_PATTERNS.search(prompt):
        flags |= int(CapabilityFlags.SUPPORTS_NETWORK)
    if _AUTH_PATTERNS.search(prompt):
        flags |= int(CapabilityFlags.AUTH_REQUIRED)
    return flags


def _derive_output_formats(prompt: str) -> frozenset[str]:
    formats = {"text"}
    if _JSON_PATTERNS.search(prompt):
        formats.add("json")
    if _BINARY_PATTERNS.search(prompt):
        formats.add("binary")
    return frozenset(formats)


def _derive_approval_mode(session: SessionStartEvent) -> bool:
    """Return True if tools should be auto-approved (no prompt)."""
    return session.permission_mode in ("bypassPermissions", "dangerouslySkipPermissions")
