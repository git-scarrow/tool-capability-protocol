#!/home/sam/projects/tool-capability-protocol/.venv/bin/python3
"""TCP shadow pilot — UserPromptSubmit hook.

Fires on: UserPromptSubmit (BEFORE any tool fires — independent of tool choice)
Logs: {session_id, turn_id, timestamp, prompt} to session JSONL

Install in ~/.claude/settings.json:
  "UserPromptSubmit": [{"hooks": [{"type": "command",
    "command": "/path/to/tcp_shadow_prompt.py"}]}]
"""

import json
import os
import sys
import time
from pathlib import Path

SESSIONS_DIR = Path.home() / ".tcp-shadow" / "sessions"
PROMPT_RETENTION_SECONDS = 48 * 3600  # 48h


def main() -> None:
    payload = json.loads(sys.stdin.read())

    session_id = payload.get("session_id", "unknown")
    prompt = payload.get("prompt", "")

    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    log_path = SESSIONS_DIR / f"{session_id}.jsonl"
    turn_id = _next_turn_id(log_path)

    record = {
        "event": "user_prompt",
        "session_id": session_id,
        "turn_id": turn_id,
        "timestamp": time.time(),
        "prompt": prompt,
    }

    _append_jsonl(log_path, record)


def _next_turn_id(log_path: Path) -> int:
    """Return a monotonically increasing turn counter for this session."""
    if not log_path.exists():
        return 1
    count = sum(
        1 for line in log_path.read_text().splitlines()
        if line and json.loads(line).get("event") == "user_prompt"
    )
    return count + 1


def _append_jsonl(path: Path, record: dict) -> None:
    tmp = Path(str(path) + ".tmp")
    existing = path.read_text() if path.exists() else ""
    tmp.write_text(existing + json.dumps(record) + "\n")
    os.replace(tmp, path)


if __name__ == "__main__":
    main()
