"""Absence-language detection for the Capability Resolution Gate.

Detects text patterns that assert a capability is unavailable.  These phrases
are only valid when backed by a signed CapabilityResolution{status=unavailable}.
"""

from __future__ import annotations

import re


_ABSENCE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bI (?:do not|don't) have access to\b", re.I),
    re.compile(r"\bI (?:can't|cannot) access\b", re.I),
    re.compile(r"\bI (?:can't|cannot) reach\b", re.I),
    re.compile(r"\bI (?:do not|don't) have (?:a |the )?(?:tool|connector|integration|plugin) for\b", re.I),
    re.compile(r"\bno (?:tool|connector|integration) (?:is )?available for\b", re.I),
    re.compile(r"\bI(?:'m| am) not able to (?:access|reach|connect to|use)\b", re.I),
    re.compile(r"\bI (?:do not|don't) have (?:the )?ability to (?:access|reach|connect to)\b", re.I),
    re.compile(r"\bI (?:do not|don't) have (?:Notion|GitHub|calendar|email|Oracle|Slack|Jira|Linear)\b", re.I),
    re.compile(r"\bno (?:Notion|GitHub|calendar|email|Oracle|Slack|Jira|Linear) (?:tool|access|connector|integration)\b", re.I),
    re.compile(r"\bI (?:can't|cannot) (?:search|query|access|read|write|fetch) (?:your |the )?(?:Notion|GitHub|calendar|email|database)\b", re.I),
    re.compile(r"\bI don't have (?:direct )?access to (?:any )?(?:external|connected|linked)\b", re.I),
)


def contains_absence_language(text: str) -> bool:
    """Return True if text contains any capability-absence assertion."""
    return any(p.search(text) for p in _ABSENCE_PATTERNS)


def extract_absence_phrases(text: str) -> list[str]:
    """Return the matching substrings of any absence-language patterns found."""
    phrases: list[str] = []
    for pattern in _ABSENCE_PATTERNS:
        m = pattern.search(text)
        if m:
            phrases.append(m.group(0))
    return phrases


def extract_text_from_response_body(body: bytes) -> str:
    """Extract all text block content from a non-streamed Anthropic response body."""
    import json
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    parts: list[str] = []
    for block in data.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            t = block.get("text")
            if isinstance(t, str):
                parts.append(t)
    return " ".join(parts)


def extract_text_from_sse_buf(buf: bytes) -> str:
    """Extract accumulated text from an SSE byte buffer (content_block_delta events)."""
    import json
    try:
        text = buf.decode("utf-8", errors="replace")
    except Exception:
        return ""
    parts: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        payload = stripped[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            continue
        if data.get("type") == "content_block_delta":
            delta = data.get("delta", {})
            if isinstance(delta, dict) and delta.get("type") == "text_delta":
                chunk = delta.get("text")
                if isinstance(chunk, str):
                    parts.append(chunk)
    return "".join(parts)
