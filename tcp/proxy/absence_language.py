"""Absence-language detection for the Capability Resolution Gate.

Detects text patterns that assert a capability is unavailable.  These phrases
are only valid when backed by a signed CapabilityResolution{status=unavailable}.
"""

from __future__ import annotations

import re

_ABSENCE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # First-person inability — original 11 patterns
    re.compile(r"\bI (?:do not|don't) have access to\b", re.I),
    re.compile(r"\bI (?:can't|cannot) access\b", re.I),
    re.compile(r"\bI (?:can't|cannot) reach\b", re.I),
    re.compile(
        r"\bI (?:do not|don't) have (?:a |the )?(?:tool|connector|integration|plugin) for\b",
        re.I,
    ),
    re.compile(r"\bno (?:tool|connector|integration) (?:is )?available for\b", re.I),
    re.compile(r"\bI(?:'m| am) not able to (?:access|reach|connect to|use)\b", re.I),
    re.compile(
        r"\bI (?:do not|don't) have (?:the )?ability to (?:access|reach|connect to)\b",
        re.I,
    ),
    re.compile(
        r"\bI (?:do not|don't) have (?:Notion|GitHub|calendar|email|Oracle|Slack|Jira|Linear)\b",
        re.I,
    ),
    re.compile(
        r"\bno (?:Notion|GitHub|calendar|email|Oracle|Slack|Jira|Linear) (?:tool|access|connector|integration)\b",
        re.I,
    ),
    re.compile(
        r"\bI (?:can't|cannot) (?:search|query|access|read|write|fetch) (?:your |the )?(?:Notion|GitHub|calendar|email|database)\b",
        re.I,
    ),
    re.compile(
        r"\bI don't have (?:direct )?access to (?:any )?(?:external|connected|linked)\b",
        re.I,
    ),
    # Verb alternatives — "lack", "no way to", "have no"
    re.compile(
        r"\bI lack (?:the )?(?:access|ability|capability|means|tool|tools|way) (?:to|for)\b",
        re.I,
    ),
    re.compile(r"\bI lack access\b", re.I),
    re.compile(r"\bI have no (?:way|means|access|ability|tool|tools) to\b", re.I),
    re.compile(r"\bI (?:do not|don't) support\b", re.I),
    # Subject-inversion / impersonal — capability-token subject
    re.compile(
        r"\b(?:Notion|GitHub|calendar|email|Oracle|Slack|Jira|Linear)(?:\s+\w+){0,3}\s+is (?:not |un)(?:accessible|available|connected|reachable|supported)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:Notion|GitHub|calendar|email|Oracle|Slack|Jira|Linear) integration is (?:not |un)(?:available|connected|enabled|configured|accessible)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:the )?(?:Notion|GitHub|calendar|email|Oracle|Slack|Jira|Linear) integration (?:is(?:n't| not)|isn't) (?:available|enabled|connected|configured)\b",
        re.I,
    ),
    # Existential negation — "There is no X"
    re.compile(
        r"\bthere(?:'s| is) no (?:Notion|GitHub|calendar|email|Oracle|Slack|Jira|Linear|tool|connector|integration|capability|way)\b",
        re.I,
    ),
    re.compile(
        r"\bthere are no (?:tools|connectors|integrations|capabilities)\b", re.I
    ),
    # Passive voice
    re.compile(
        r"\b(?:Notion|GitHub|calendar|email|Oracle|Slack|Jira|Linear|that|it) (?:can(?:not| ?'?t)|could(?:not| ?n'?t)) be (?:accessed|reached|queried|searched|opened|connected|used)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:is|are) not (?:accessible|reachable|available|supported|connected) (?:from|in|to)\b",
        re.I,
    ),
    # Workspace / context phrasing
    re.compile(
        r"\b(?:your |the )?workspace is not (?:connected|accessible|available|configured)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:your |the )?(?:account|workspace|integration) (?:isn't|is not) (?:connected|linked|set up|configured)\b",
        re.I,
    ),
    # Hedged / apologetic — "Unfortunately", "I'm afraid"
    re.compile(
        r"\b(?:unfortunately|sadly|regrettably)[,\s].{0,80}?(?:not (?:available|accessible|connected|supported|enabled)|isn't (?:available|accessible|connected|supported|enabled))\b",
        re.I | re.S,
    ),
    re.compile(
        r"\bI(?:'m| am) (?:afraid|sorry)[,\s].{0,80}?(?:do not|don't|cannot|can't|can ?not)\b",
        re.I | re.S,
    ),
)


_NEGATION_TOKENS: tuple[str, ...] = (
    "not accessible",
    "unaccessible",
    "unavailable",
    "not available",
    "not reachable",
    "unreachable",
    "not supported",
    "unsupported",
    "not connected",
    "disconnected",
    "isn't available",
    "isn't accessible",
    "isn't connected",
    "isn't supported",
    "no access",
    "without access",
)

_CAPABILITY_TOKENS: tuple[str, ...] = (
    "notion",
    "github",
    "calendar",
    "email",
    "oracle",
    "slack",
    "jira",
    "linear",
    "gmail",
    "outlook",
    "drive",
    "sharepoint",
    "confluence",
)

# Window for capability-token / negation-token co-occurrence detection.
_CO_OCCURRENCE_WINDOW = 80


def _structural_absence_match(text: str) -> str | None:
    """Backstop detector: capability token within N chars of a negation indicator.

    Returns the surrounding substring if a co-occurrence is found, else None.
    More general than the regex set above — catches phrasings that don't fit
    a fixed grammatical template but still pair a capability with negation.
    """
    lower = text.lower()
    for cap in _CAPABILITY_TOKENS:
        cap_idx = 0
        while True:
            cap_idx = lower.find(cap, cap_idx)
            if cap_idx < 0:
                break
            window_start = max(0, cap_idx - _CO_OCCURRENCE_WINDOW)
            window_end = min(len(lower), cap_idx + len(cap) + _CO_OCCURRENCE_WINDOW)
            window = lower[window_start:window_end]
            for neg in _NEGATION_TOKENS:
                if neg in window:
                    return text[window_start:window_end]
            cap_idx += len(cap)
    return None


def contains_absence_language(text: str) -> bool:
    """Return True if text contains any capability-absence assertion.

    Uses two layers:
      1. Compiled regex patterns (high precision, well-known phrasings).
      2. Structural co-occurrence detector (capability token near negation
         token within an N-character window) — catches paraphrases the regex
         set would miss.
    """
    if any(p.search(text) for p in _ABSENCE_PATTERNS):
        return True
    return _structural_absence_match(text) is not None


def extract_absence_phrases(text: str) -> list[str]:
    """Return the matching substrings of any absence-language patterns found.

    Includes hits from both the regex patterns and the structural detector.
    """
    phrases: list[str] = []
    for pattern in _ABSENCE_PATTERNS:
        m = pattern.search(text)
        if m:
            phrases.append(m.group(0))
    structural = _structural_absence_match(text)
    if structural and not any(structural in p or p in structural for p in phrases):
        phrases.append(structural)
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
