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
    "continue this work", "yes please", "yes, please", "yes.", "no."
})

_SYSTEM_DIRS = {"/", "/usr", "/etc", "/var", "/boot", "/sys", "/proc", "/bin", "/sbin"}

# Intent verbs
_FILE_VERBS = re.compile(r'\b(read|write|edit|create|save|open|load|delete|copy|move|rename|list|grep|glob|ls|find|dig|proceed|pick|analyze|check|search|scan|fix|harden|identify|perform|review|access|show|use)\b', re.I)
_NET_VERBS = re.compile(r'\b(fetch|curl|wget|download|upload|api|endpoint|http|get|post|visit|open|search|check|request|deploy|reply|send|did|any|use)\b', re.I)

# Objects
_FILE_OBJECTS = re.compile(
    r'\b[\w/.-]+\.(py|js|ts|go|rb|rs|c|h|cpp|hpp|sh|json|yaml|yml|md|sql|html|css)\b'  # Removed txt
    r'|\b(file|files|dir|directory|path|folder|code|repo|doc|docs|source|fonts|source-grounded)\b',
    re.I
)
_NEGATIVE_OBJECTS = re.compile(r'\b(hello world|worship folder|ADMIN|notion)\b', re.I)

_NET_OBJECTS = re.compile(
    r'https?://\S+'
    r'|\b(notion|email|emails|thread|message|messages|nixos|aws-ec2|remote|api|endpoint|playwright|myanonamouse)\b',
    re.I
)


_URL_PATTERN = re.compile(r'https?://\S+')

# Absolute paths (strong signal)
_ABS_PATH_PATTERN = re.compile(r'(?:^|\s)/(home|tmp|var|etc|usr)/[\w/.-]+\b')

# Auth patterns
_AUTH_PATTERNS = re.compile(
    r'\b(sudo|privilege|systemctl|apt|yum|chmod|chown)\b'
    r'|\broot\b(?!.*\b(dir|directory|folder|path)\b)'
    r'|\b(notion|email|emails|myanonamouse|login)\b',
    re.I
)


# Output format patterns
_JSON_PATTERNS = re.compile(
    r'\b(output json|format as json|return json|as json|json format|structured output)\b', re.IGNORECASE
)
_BINARY_PATTERNS = re.compile(
    r'\b(image|screenshot|diagram|pdf|zip|tar)\b'
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
    # Other MCP surfaces (singleton equivalence classes; include in shadow inventory)
    "mcp__notion-agents__start_agent_run": "MCP_NOTION_AGENT_RUN",
    "mcp__proxmox__get_vms": "MCP_PROXMOX_READ",
}

_GIT_WRITE_COMMANDS = re.compile(
    r'^git\s+(add|commit|push|checkout\s+-b|rebase|merge|tag|rm|mv)\b',
    re.IGNORECASE,
)
_GIT_READ_COMMANDS = re.compile(r'^git\b', re.IGNORECASE)
_WEB_COMMANDS = re.compile(r'^(curl|wget|fetch)\b', re.IGNORECASE)


# ── Public API ────────────────────────────────────────────────────────────────

def normalize_mcp_git_tool_name(tool_name: str) -> str:
    """Map ``mcp__<server-slug>__git__…`` to ``mcp__git__…`` for stable matching.

    Some MCP installs register git as ``mcp__agents__git__git_add`` while tables
    and shadow inventory use the shorter ``mcp__git__`` prefix.
    """
    if not tool_name.startswith("mcp__"):
        return tool_name
    parts = tool_name.split("__")
    if len(parts) < 3:
        return tool_name
    try:
        git_idx = parts.index("git")
    except ValueError:
        return tool_name
    suffix = parts[git_idx + 1 :]
    if not suffix:
        return tool_name
    return "mcp__git__" + "__".join(suffix)


def derive_request(
    prompt: str,
    session: SessionStartEvent,
) -> ToolSelectionRequest:
    """Convert a UserPromptSubmit prompt + SessionStart into a ToolSelectionRequest."""
    stripped = prompt.strip().lower()
    
    # 1. Skip system/continuation prompts (High Precision Filter)
    if not stripped or stripped in _CONTINUATION_PROMPTS:
        return ToolSelectionRequest.from_kwargs(
            required_capability_flags=0,
            heuristic_capability_flags=0,
            required_output_formats=frozenset({"text"}),
            require_auto_approval=_derive_approval_mode(session),
        )
        
    # 2. Skip tool-output/notification-like prompts
    if "<task-notification>" in prompt or "tool-use-id" in prompt or "output-file" in prompt:
        return ToolSelectionRequest.from_kwargs(
            required_capability_flags=0,
            heuristic_capability_flags=0,
            required_output_formats=frozenset({"text"}),
            require_auto_approval=_derive_approval_mode(session),
        )

    prompt_flags = _derive_capability_flags_from_prompt_only(prompt)
    env_flags = _derive_env_flags(prompt, session)
    all_flags = prompt_flags | env_flags

    output_formats = _derive_output_formats(prompt)
    require_auto_approval = _derive_approval_mode(session)

    return ToolSelectionRequest.from_kwargs(
        required_capability_flags=all_flags,
        heuristic_capability_flags=prompt_flags,
        required_output_formats=output_formats,
        require_auto_approval=require_auto_approval,
        preferred_criteria="speed",
    )



def get_equivalence_class(tool_name: str, tool_input: dict) -> str:
    """Return the functional equivalence class for a tool call.

    Uses the taxonomy defined in TCP-DS-2 §4. Bash is disambiguated by
    inspecting tool_input['command'].
    """
    tool_name = normalize_mcp_git_tool_name(tool_name)
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
    """Legacy: returns union of prompt + env flags. Kept for classify_unscorable."""
    return _derive_capability_flags_from_prompt_only(prompt) | _derive_env_flags(prompt, session)


def _derive_env_flags(_prompt: str, session: SessionStartEvent) -> int:
    """Environment-derived flags only — safe for live hard rejection."""
    flags = 0
    cwd = session.cwd.rstrip("/")
    if any(cwd == sd or cwd.startswith(sd + "/") for sd in _SYSTEM_DIRS):
        flags |= int(CapabilityFlags.AUTH_REQUIRED)
    return flags


def _strip_tool_logs(text: str) -> str:
    """Remove patterns like Read(...), Bash(...), Tracebacks, and JSON from the text."""
    # Catch interactive interruptions
    text = re.sub(r'●\s+[\w-]+ - [\w-]+ \(MCP\).*?What should Claude do instead\?', '', text, flags=re.DOTALL)
    text = re.sub(r'●\s+(Read|Write|Edit|Bash|Glob|Grep|WebFetch|Skill).*?What should Claude do instead\?', '', text, flags=re.DOTALL)
    text = re.sub(r'\u23bf\s+\u00a0Interrupted \u00b7 What should Claude do instead\?', '', text)
    # Generic MCP and Claude Code tool payloads
    text = re.sub(r'●\s+[\w-]+ - [\w-]+ \(MCP\)\(.*?\)[\s\u23bf\u2022]*', '', text, flags=re.DOTALL)
    text = re.sub(r'[●\s]*\b(Read|Write|Edit|Bash|Glob|Grep|WebFetch|Skill)\(.*?\)[\s\u23bf\u2022]*', '', text, flags=re.DOTALL)
    # Strip Tracebacks and general system paths in quotes
    text = re.sub(r'\bFile\s+"[^"]+",\s+line\s+\d+', '', text)
    # Strip JSON-like blocks
    text = re.sub(r'\{[^{}]*?"[^{}]*?":.*\}', '', text, flags=re.DOTALL)
    return text






def _derive_capability_flags_from_prompt_only(prompt: str) -> int:
    """Extract capability flags using a score-based intent classifier."""
    # Pre-strip logs to avoid false positives from history
    clean_prompt = _strip_tool_logs(prompt)
    stripped = clean_prompt.strip()
    
    # Base signals
    has_abs_path = bool(_ABS_PATH_PATTERN.search(stripped))
    has_http_method = bool(re.search(r'\bHTTP\s+(?:GET|POST|PUT|DELETE|PATCH)\b', clean_prompt))
    
    # Request Score Calculation
    score = 0
    
    # 1. Imperative/Directive starts (Strong Signal)
    if re.search(r'^(?:please\s+)?\b(do|run|fix|harden|create|read|write|edit|find|search|grep|glob|ls|list|check|analyze|dig|proceed|pick|deploy|visit|open|fetch|download|upload|search|scan|identify|perform)\b', stripped, re.I):
        score += 4
    
    # 2. Standalone strong signals
    if has_abs_path:
        score += 5
    if has_http_method:
        score += 5
        
    # Semantic pairs bump score dramatically to bypass long-prompt penalties
    if _FILE_VERBS.search(stripped) and _FILE_OBJECTS.search(stripped):
        score += 5
    if _NET_VERBS.search(stripped) and _NET_OBJECTS.search(stripped):
        score += 5
    if _AUTH_PATTERNS.search(stripped):
        score += 5
        
    # 3. Question marks
    if "?" in stripped:
        score += 2
        
    # 4. Direct address/Personal intent
    if re.search(r'\b(can you|could you|please|i want to|i need to|help me|did any)\b', stripped, re.I):
        score += 2
        
    # 5. Length penalties
    words = stripped.split()
    if len(words) > 50:
        score -= 1
    if len(words) > 200:
        score -= 2

    # 6. Report/Metadata penalties
    if stripped.count("\n- ") > 5 or stripped.count("\n* ") > 5:
        score -= 2
    if "│" in stripped or "└─" in stripped:
        score -= 5
    if stripped.count(":") > 15:
        score -= 2

    # Score-based gate
    if score < 2:
        return 0

    return _derive_capability_flags_unconditional(clean_prompt)










def _derive_capability_flags_unconditional(text: str) -> int:
    """Apply FILE/NETWORK/AUTH pattern rules without the short-prompt early exit."""
    flags = 0

    # 1. FILE_READ/WRITE (SUPPORTS_FILES)
    file_verb = _FILE_VERBS.search(text)
    file_obj = _FILE_OBJECTS.search(text)
    neg_obj = _NEGATIVE_OBJECTS.search(text)
    abs_path = _ABS_PATH_PATTERN.search(text)
    if ((file_verb and file_obj) or abs_path) and not neg_obj:
        flags |= int(CapabilityFlags.SUPPORTS_FILES)

    # 2. NETWORK (SUPPORTS_NETWORK)
    net_verb = _NET_VERBS.search(text)
    net_obj = _NET_OBJECTS.search(text)
    http_method = re.search(r'\bHTTP\s+(?:GET|POST|PUT|DELETE|PATCH)\b', text)
    if (net_verb and net_obj) or http_method:
        flags |= int(CapabilityFlags.SUPPORTS_NETWORK)

    # 3. AUTH (AUTH_REQUIRED)
    is_network_auth = bool(net_verb and re.search(r'\b(notion|email|emails|login|myanonamouse|thread)\b', text, re.I))
    if _AUTH_PATTERNS.search(text) or is_network_auth:
        flags |= int(CapabilityFlags.AUTH_REQUIRED)

    return flags




def derive_capability_flags_from_description(description: str) -> int:
    """Tier-2 tool projection: infer flags from the tool's description text.

    Omits the user-prompt length gate so short MCP descriptions still participate.
    """
    if not (description or "").strip():
        return 0
    return _derive_capability_flags_unconditional(description.strip())


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
