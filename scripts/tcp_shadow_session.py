#!/usr/bin/env python3
"""TCP shadow pilot — SessionStart hook.

Fires on: SessionStart
Logs: inventory snapshot + session metadata to ~/.tcp-shadow/

Install in ~/.claude/settings.json:
  "SessionStart": [{"hooks": [{"type": "command",
    "command": "/path/to/tcp_shadow_session.py"}]}]
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

SHADOW_DIR = Path.home() / ".tcp-shadow"
SESSIONS_DIR = SHADOW_DIR / "sessions"
INVENTORIES_DIR = SHADOW_DIR / "inventories"


def main() -> None:
    payload = json.loads(sys.stdin.read())

    session_id = payload.get("session_id", "unknown")
    permission_mode = payload.get("permission_mode", "default")
    cwd = payload.get("cwd", "")

    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    INVENTORIES_DIR.mkdir(parents=True, exist_ok=True)

    # Build inventory snapshot from the known Claude Code tool list.
    # In a real session the tools are fixed at startup, so we enumerate them
    # statically here. A future version could read from the MCP server manifest.
    inventory = _build_inventory()
    inventory_json = json.dumps(inventory, sort_keys=True)
    snapshot_id = hashlib.sha256(inventory_json.encode()).hexdigest()[:16]

    inventory_path = INVENTORIES_DIR / f"{snapshot_id}.json"
    if not inventory_path.exists():
        _atomic_write(inventory_path, inventory_json)

    record = {
        "event": "session_start",
        "session_id": session_id,
        "timestamp": time.time(),
        "permission_mode": permission_mode,
        "cwd": cwd,
        "inventory_snapshot_id": snapshot_id,
    }

    log_path = SESSIONS_DIR / f"{session_id}.jsonl"
    _append_jsonl(log_path, record)


def _build_inventory() -> dict:
    """Return a static snapshot of known Claude Code tools with capability hints."""
    from tcp.core.descriptors import CapabilityFlags

    F = CapabilityFlags
    tools = [
        {"name": "Read",       "flags": int(F.SUPPORTS_FILES)},
        {"name": "Write",      "flags": int(F.SUPPORTS_FILES)},
        {"name": "Edit",       "flags": int(F.SUPPORTS_FILES)},
        {"name": "MultiEdit",  "flags": int(F.SUPPORTS_FILES)},
        {"name": "Glob",       "flags": int(F.SUPPORTS_FILES)},
        {"name": "Grep",       "flags": int(F.SUPPORTS_FILES)},
        {"name": "Bash",       "flags": int(F.SUPPORTS_FILES) | int(F.SUPPORTS_NETWORK)},
        {"name": "WebFetch",   "flags": int(F.SUPPORTS_NETWORK)},
        {"name": "WebSearch",  "flags": int(F.SUPPORTS_NETWORK)},
        {"name": "Think",      "flags": 0},
        # MCP filesystem
        {"name": "mcp__filesystem__read_file",       "flags": int(F.SUPPORTS_FILES)},
        {"name": "mcp__filesystem__write_file",      "flags": int(F.SUPPORTS_FILES)},
        {"name": "mcp__filesystem__read_multiple_files", "flags": int(F.SUPPORTS_FILES)},
        {"name": "mcp__filesystem__list_directory",  "flags": int(F.SUPPORTS_FILES)},
        {"name": "mcp__filesystem__search_files",    "flags": int(F.SUPPORTS_FILES)},
        {"name": "mcp__filesystem__directory_tree",  "flags": int(F.SUPPORTS_FILES)},
        {"name": "mcp__filesystem__create_directory","flags": int(F.SUPPORTS_FILES)},
        # MCP git
        {"name": "mcp__git__git_log",          "flags": int(F.SUPPORTS_FILES)},
        {"name": "mcp__git__git_diff",         "flags": int(F.SUPPORTS_FILES)},
        {"name": "mcp__git__git_status",       "flags": int(F.SUPPORTS_FILES)},
        {"name": "mcp__git__git_commit",       "flags": int(F.SUPPORTS_FILES)},
        {"name": "mcp__git__git_add",          "flags": int(F.SUPPORTS_FILES)},
        {"name": "mcp__git__git_branch",       "flags": int(F.SUPPORTS_FILES)},
        {"name": "mcp__git__git_checkout",     "flags": int(F.SUPPORTS_FILES)},
        # MCP fetch
        {"name": "mcp__fetch__fetch",          "flags": int(F.SUPPORTS_NETWORK)},
    ]
    return {"tools": tools, "version": "1.0"}


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content)
    os.replace(tmp, path)


def _append_jsonl(path: Path, record: dict) -> None:
    tmp = Path(str(path) + ".tmp")
    existing = path.read_text() if path.exists() else ""
    tmp.write_text(existing + json.dumps(record) + "\n")
    os.replace(tmp, path)


if __name__ == "__main__":
    main()
