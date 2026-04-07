"""Select the task-initiating user text from an Anthropic Messages API request."""

from __future__ import annotations

from typing import Any, Mapping


def _text_parts_from_content(content: Any) -> list[str]:
    """Collect plain text strings from a message content value."""
    parts: list[str] = []
    if content is None:
        return parts
    if isinstance(content, str):
        if content.strip():
            parts.append(content)
        return parts
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, Mapping):
                continue
            btype = block.get("type")
            if btype == "text":
                t = block.get("text")
                if isinstance(t, str) and t.strip():
                    parts.append(t)
        return parts
    return parts


def extract_task_prompt(messages: list[Mapping[str, Any]] | None) -> str:
    """Walk from the end; take the latest user message that has at least one text block."""
    if not messages:
        return ""
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if not isinstance(msg, Mapping):
            continue
        if msg.get("role") != "user":
            continue
        texts = _text_parts_from_content(msg.get("content"))
        if texts:
            return "\n".join(texts).strip()
    return ""
