#!/usr/bin/env python3
"""TCP shadow pilot — PostToolUse hook.

Fires on: PostToolUse (after every tool call)
Logs: non-sensitive tool call features to session JSONL

Install in ~/.claude/settings.json:
  "PostToolUse": [{"hooks": [{"type": "command",
    "command": "/path/to/tcp_shadow_tool.py"}]}]
"""

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

SESSIONS_DIR = Path.home() / ".tcp-shadow" / "sessions"

_URL_RE = re.compile(r'https?://\S+')
_SQL_RE = re.compile(r'\b(SELECT|INSERT|UPDATE|DELETE|FROM|WHERE)\b', re.IGNORECASE)
_PATH_RE = re.compile(r'(^|[\s\'"])(\/[\w/.-]+|\.\./[\w/.-]+)')
_REGEX_RE = re.compile(r'[.*+?^${}()|[\]\\]')


def main() -> None:
    payload = json.loads(sys.stdin.read())

    session_id = payload.get("session_id", "unknown")
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    tool_response = payload.get("tool_response")
    tool_use_id = payload.get("tool_use_id", "")

    tool_result_status = _classify_result(tool_response)
    input_features = _extract_features(tool_input)
    input_str = json.dumps(tool_input, sort_keys=True)
    input_hash = hashlib.sha256(input_str.encode()).hexdigest()[:16]

    ts = time.time()
    call_id = hashlib.sha256(
        f"{session_id}:{tool_use_id}:{tool_name}:{input_hash}".encode()
    ).hexdigest()[:16]

    log_path = SESSIONS_DIR / f"{session_id}.jsonl"
    turn_id = _current_turn_id(log_path)

    record = {
        "event": "tool_use",
        "session_id": session_id,
        "turn_id": turn_id,
        "call_id": call_id,
        "tool_use_id": tool_use_id,
        "tool_name": tool_name,
        "tool_input_hash": input_hash,
        "tool_input_features": input_features,
        "tool_result_status": tool_result_status,
        "timestamp": ts,
    }

    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    _append_jsonl(log_path, record)


def _classify_result(tool_response: object) -> str:
    if tool_response is None:
        return "empty"
    if isinstance(tool_response, dict):
        if tool_response.get("type") == "tool_result" and tool_response.get("is_error"):
            return "error"
    if isinstance(tool_response, str) and tool_response.startswith("Error"):
        return "error"
    return "ok"


def _extract_features(tool_input: dict) -> dict:
    """Extract non-sensitive structural features from tool_input."""
    text = json.dumps(tool_input)
    return {
        "has_path": bool(_PATH_RE.search(text)),
        "has_url": bool(_URL_RE.search(text)),
        "has_sql": bool(_SQL_RE.search(text)),
        "has_regex": bool(_REGEX_RE.search(text)),
        "arg_size_bucket": _size_bucket(len(text)),
        "top_level_keys": sorted(tool_input.keys()) if isinstance(tool_input, dict) else [],
    }


def _size_bucket(n: int) -> str:
    if n < 100:
        return "xs"
    if n < 1000:
        return "sm"
    if n < 10_000:
        return "md"
    return "lg"


def _current_turn_id(log_path: Path) -> int:
    if not log_path.exists():
        return 1
    prompts = [
        json.loads(l) for l in log_path.read_text().splitlines()
        if l and json.loads(l).get("event") == "user_prompt"
    ]
    return prompts[-1]["turn_id"] if prompts else 1


def _append_jsonl(path: Path, record: dict) -> None:
    tmp = Path(str(path) + ".tmp")
    existing = path.read_text() if path.exists() else ""
    tmp.write_text(existing + json.dumps(record) + "\n")
    os.replace(tmp, path)


if __name__ == "__main__":
    main()
