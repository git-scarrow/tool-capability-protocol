#!/home/sam/projects/tool-capability-protocol/.venv/bin/python3
"""TCP shadow pilot — SessionStart hook.

Fires on: SessionStart
Logs: inventory snapshot + session metadata to ~/.tcp-shadow/

Install in ~/.claude/settings.json:
  "SessionStart": [{"hooks": [{"type": "command",
    "command": "/path/to/tcp_shadow_session.py"}]}]
"""

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

    inventory = _build_inventory()
    inventory_json = json.dumps(inventory, sort_keys=True)
    snapshot_id = _short_sha256(inventory_json)

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
        "inventory_artifact_version": inventory.get("version"),
        "inventory_tool_count": len(inventory.get("tools", [])),
        "inventory_sha256": _short_sha256(inventory_json),
    }

    log_path = SESSIONS_DIR / f"{session_id}.jsonl"
    _append_jsonl(log_path, record)


def _build_inventory() -> dict:
    """Return the canonical inventory artifact used by shadow replay."""
    from tcp.proxy.tool_flag_map import build_static_inventory

    return build_static_inventory()


def _short_sha256(content: str) -> str:
    import hashlib

    return hashlib.sha256(content.encode()).hexdigest()[:16]


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
