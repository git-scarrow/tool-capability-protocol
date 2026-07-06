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


# ═══════════════════════════════════════════════════════════════════════════
# Detector v2 (CRG Phase 2B) — tiered, context-guarded absence detection.
#
# v1 (above) is intentionally untouched: cc_proxy runs both side by side and
# logs v2 verdicts in denial_v2_* fields until the fixture + live gates pass
# (tests/data/absence_audit_v1.jsonl labels the historical v1 flag set).
#
# Tier A: assistant-voice, present-tense capability claims — the only tier
#   that can ever justify a denial_violation.
# Tier B: the v1 structural co-occurrence backstop — telemetry-only recall
#   candidates, never a violation by itself.
# in_surface: a Tier A claim must reference a capability token (known
#   connector universe, or the session's live server/tool names) in the same
#   sentence to be violation-eligible; first-person claims about arbitrary
#   files/hosts/infra are logged as out-of-surface, not violations.
# ═══════════════════════════════════════════════════════════════════════════

from dataclasses import dataclass, field
from typing import Iterable

ABSENCE_DETECTOR_VERSION_V2 = "crg.absence.v2"

# ── Layer 0: reported-context stripping ─────────────────────────────────────
_V2_FENCED_CODE_RE = re.compile(r"```.*?```", re.S)
# Inline code: keep short single-token spans (tool/identifier names are
# capability evidence), drop longer command/code fragments.
_V2_INLINE_CODE_RE = re.compile(r"`([^`\n]*)`")
_V2_INLINE_KEEP_MAX = 40
# Double-quoted spans are reported speech (error messages, quoted phrases,
# web content).  Bounded so an unbalanced quote can't swallow the response.
_V2_QUOTED_SPAN_RE = re.compile(r"[\"“][^\"“”\n]{1,300}[\"”]")
_V2_BLOCKQUOTE_RE = re.compile(r"^[ \t]*>.*$", re.M)
# Compaction / summary responses narrate prior session state; absence phrases
# inside them are history, not a user-facing denial.
_V2_NARRATION_PREFIX = "<analysis>"


def _v2_inline_code_sub(m: re.Match[str]) -> str:
    inner = m.group(1)
    if len(inner) <= _V2_INLINE_KEEP_MAX and " " not in inner:
        return inner
    return " "


def strip_reported_context(text: str) -> str:
    """Remove text spans that report others' words rather than assert claims.

    Strips fenced code blocks, blockquote lines, double-quoted spans, and
    long inline-code fragments.  Short single-token inline code (tool and
    identifier names) is kept because it is capability evidence for the
    in-surface check.
    """
    text = _V2_FENCED_CODE_RE.sub(" ", text)
    text = _V2_BLOCKQUOTE_RE.sub(" ", text)
    text = _V2_QUOTED_SPAN_RE.sub(" ", text)
    text = _V2_INLINE_CODE_RE.sub(_v2_inline_code_sub, text)
    return text


# ── Sentence machinery ───────────────────────────────────────────────────────
_V2_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+|;\s+")


def _v2_sentences(text: str) -> list[str]:
    return [s for s in _V2_SENTENCE_SPLIT_RE.split(text) if s.strip()]


# ── Capability tokens ────────────────────────────────────────────────────────
# Unambiguous connector names: always count as capability references.
_V2_UNAMBIGUOUS_TOKENS: tuple[str, ...] = (
    "notion",
    "github",
    "sharepoint",
    "slack",
    "jira",
    "linear",
    "confluence",
    "gmail",
    "outlook",
)
# Common nouns that are only capability references with a qualifier nearby
# ("email server", "calendar integration") or when the session surface
# contains a matching server/tool name.
_V2_AMBIGUOUS_TOKENS: tuple[str, ...] = ("email", "calendar", "drive", "oracle")
_V2_QUALIFIER_RE = re.compile(
    r"\b(?:server|tools?|tooling|integration|connector|mcp|api|skill)\b", re.I
)
# A connector token immediately followed by an artifact noun is an entity
# reference ("there's no GitHub Issue"), not a capability claim.
_V2_ARTIFACT_NOUN_RE = (
    r"(?!\s+(?:issue|pr|pull|repo|repository|commit|branch|mirror|action|"
    r"release|page|workflow|gist)s?\b)"
)


def _v2_token_res(
    surface_tokens: frozenset[str],
) -> list[re.Pattern[str]]:
    pats = [
        re.compile(r"\b" + re.escape(t) + r"\b", re.I) for t in _V2_UNAMBIGUOUS_TOKENS
    ]
    pats.extend(
        re.compile(r"\b" + re.escape(t) + r"\b", re.I)
        for t in sorted(surface_tokens)
        if t
    )
    return pats


def _v2_sentence_has_capability_token(
    sentence: str, token_res: list[re.Pattern[str]]
) -> bool:
    if any(p.search(sentence) for p in token_res):
        return True
    if _V2_QUALIFIER_RE.search(sentence):
        for t in _V2_AMBIGUOUS_TOKENS:
            if re.search(r"\b" + t + r"\b", sentence, re.I):
                return True
    return False


# ── Tier A patterns ──────────────────────────────────────────────────────────
# First-person present-tense inability.  Matched per sentence; tense guard
# applied afterwards.
_V2_FIRST_PERSON_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bI (?:do not|don't) have (?:direct )?access to\b", re.I),
    re.compile(
        r"\bI (?:can't|cannot) (?:access|reach|connect to|use|see|query|call|"
        r"read|write|fetch|search)\b",
        re.I,
    ),
    re.compile(r"\bI(?:'m| am) not able to (?:access|reach|connect to|use)\b", re.I),
    re.compile(r"\bI have no (?:means|access|ability) to\b", re.I),
    re.compile(r"\bI have no (?:[\w-]+ )?tools?\b", re.I),
    re.compile(r"\bI have no access\b", re.I),
    re.compile(r"\bI lack (?:the )?(?:access|ability|capability|means)\b", re.I),
    re.compile(
        r"\bI (?:do not|don't) have (?:a |the )?"
        r"(?:tool|connector|integration|plugin|server)\b",
        re.I,
    ),
)
# "no way to <verb>" — knowledge verbs excluded (idiomatic "no way to know");
# requires a capability token in the same or an adjacent sentence.
_V2_NO_WAY_RE = re.compile(
    r"\bno way to (?!know\b|tell\b|be sure\b|verify\b|confirm\b|guarantee\b|"
    r"predict\b)\w+",
    re.I,
)
# Capability-subject shapes (server/tool/integration as grammatical subject).
_V2_SUBJECT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "No Notion server is connected in this session"
    re.compile(
        r"\bno (?:[\w.-]+ )?(?:server|tools?|integration|connector|mcp)s?\b"
        r"[^.!?\n;]{0,60}?\b(?:connected|available|configured|enabled|loaded)\b",
        re.I,
    ),
    # "the notion-agents tooling, which isn't connected in this ... session"
    re.compile(
        r"\b(?:server|tooling|tools?|integration|connector|skill|mcp)s?\b"
        r"[^.!?\n;]{0,40}?\b(?:is(?: not|n't)|not|aren't|are not)\s+"
        r"(?:connected|available|configured|enabled|accessible)\b",
        re.I,
    ),
    # "The Notion server (notion-agents) has disconnected this session"
    re.compile(
        r"\b(?:server|integration|connector|tools?|mcp)s?\b[^.!?\n;]{0,40}?"
        r"\bdisconnected\b",
        re.I,
    ),
    # "that tool is unavailable"
    re.compile(
        r"\b(?:tool|server|integration|connector)s?\s+(?:is|are)\s+"
        r"(?:unavailable|not available)\b",
        re.I,
    ),
    # "they're not available in this session"
    re.compile(
        r"\b(?:is(?: not|n't)|are(?: not|n't)|not|n't)\s+available\s+"
        r"(?:in|for) (?:this|the current) (?:[\w-]+ )?session\b",
        re.I,
    ),
    # "there's no Notion" (bare connector, artifact nouns excluded) and
    # "there's no tool/server/connector ..."
    re.compile(
        r"\bthere(?:'s| is| are) no (?:"
        + "|".join(_V2_UNAMBIGUOUS_TOKENS)
        + r")\b"
        + _V2_ARTIFACT_NOUN_RE,
        re.I,
    ),
    re.compile(
        r"\bthere(?:'s| is| are) no (?:[\w.-]+ )?"
        r"(?:server|tools?|connectors?|integrations?|mcp|access)\b",
        re.I,
    ),
)
# Past/report auxiliaries shortly before the match ⇒ event report, not claim.
_V2_PAST_GUARD_RE = re.compile(
    r"\b(?:was|were|had been|turned out|failed because)\b[^.!?\n;]{0,60}$", re.I
)


@dataclass(frozen=True)
class AbsenceDetectionV2:
    """Result of the v2 tiered absence detection.

    tier_a_phrases:  guarded assistant-voice capability claims.
    tier_b_phrases:  structural co-occurrence candidates (telemetry only).
    tier_a_in_surface: True when at least one Tier A claim references a
        capability token and is therefore violation-eligible.
    narration_suppressed: True when Tier A was skipped because the response
        is a narration/summary document (e.g. compaction ``<analysis>``).
    """

    tier_a_phrases: tuple[str, ...] = ()
    tier_b_phrases: tuple[str, ...] = ()
    tier_a_in_surface: bool = False
    narration_suppressed: bool = False
    detector_version: str = field(default=ABSENCE_DETECTOR_VERSION_V2)

    @property
    def tier_a(self) -> bool:
        return bool(self.tier_a_phrases)

    @property
    def tier_b(self) -> bool:
        return bool(self.tier_b_phrases)


def _v2_match_survives_tense_guard(sentence: str, start: int) -> bool:
    return _V2_PAST_GUARD_RE.search(sentence[:start]) is None


def detect_absence_v2(
    text: str,
    surface_tokens: Iterable[str] | None = None,
) -> AbsenceDetectionV2:
    """Run the tiered v2 absence detector over a full response text.

    Args:
        text: Complete assistant response text (markdown intact).
        surface_tokens: Session-specific capability names — MCP server names
            and full tool base names — used for the in-surface check in
            addition to the static connector universe.

    Returns:
        AbsenceDetectionV2 with Tier A / Tier B phrases and surface verdict.
    """
    if not text:
        return AbsenceDetectionV2()

    surface = frozenset(t.lower() for t in surface_tokens or ())
    token_res = _v2_token_res(surface)

    narration = text.lstrip().startswith(_V2_NARRATION_PREFIX)
    stripped = strip_reported_context(text)
    sentences = _v2_sentences(stripped)

    tier_a: list[str] = []
    in_surface = False

    if not narration:
        for idx, sent in enumerate(sentences):
            neighborhood = sentences[max(0, idx - 1) : idx + 2]
            for pat in _V2_FIRST_PERSON_PATTERNS:
                m = pat.search(sent)
                if m and _v2_match_survives_tense_guard(sent, m.start()):
                    tier_a.append(m.group(0))
                    if _v2_sentence_has_capability_token(sent, token_res):
                        in_surface = True
            for pat in _V2_SUBJECT_PATTERNS:
                m = pat.search(sent)
                if m and _v2_match_survives_tense_guard(sent, m.start()):
                    tier_a.append(m.group(0))
                    if _v2_sentence_has_capability_token(sent, token_res):
                        in_surface = True
            m = _V2_NO_WAY_RE.search(sent)
            if (
                m
                and _v2_match_survives_tense_guard(sent, m.start())
                and any(
                    _v2_sentence_has_capability_token(s, token_res)
                    for s in neighborhood
                )
            ):
                tier_a.append(m.group(0))
                in_surface = True

    structural = _structural_absence_match(stripped)
    tier_b: tuple[str, ...] = (structural,) if structural else ()

    return AbsenceDetectionV2(
        tier_a_phrases=tuple(dict.fromkeys(tier_a)),
        tier_b_phrases=tier_b,
        tier_a_in_surface=in_surface,
        narration_suppressed=narration and bool(sentences),
    )


def extract_context_windows(
    text: str,
    phrases: Iterable[str],
    radius: int = 200,
    max_windows: int = 5,
) -> list[str]:
    """Return bounded context windows around each phrase occurrence in text.

    Used for decision-log excerpting so future audits can re-label absence
    events with real context (the historical fixture only had 300 chars).
    """
    windows: list[str] = []
    for phrase in phrases:
        if len(windows) >= max_windows:
            break
        if not phrase:
            continue
        idx = text.find(phrase[:80])
        if idx < 0:
            continue
        start = max(0, idx - radius)
        end = min(len(text), idx + len(phrase) + radius)
        windows.append(text[start:end])
    return windows
