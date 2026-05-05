"""Unit tests for denial enforcement (CRG Phase 2).

Covers:
  - absence_language detection and extraction
  - enforce_denial_gate: allowed/violation logic
  - signature verification: valid, tampered, unsigned
  - the review's canonical conformance test
"""

from __future__ import annotations

import pytest

from tcp.proxy.absence_language import (
    contains_absence_language,
    extract_absence_phrases,
    extract_text_from_response_body,
    extract_text_from_sse_buf,
)
from tcp.proxy.capability_resolution_gate import (
    CRGContext,
    resolve_capability,
    _compute_signature,
    _REQUIRED_SIX_SURFACES,
)
from tcp.proxy.denial_enforcement import (
    DenialGateDecision,
    _resolution_signature_valid,
    denial_violation_record,
    enforce_denial_gate,
)

_NOTION_TOOL = "mcp__notion-agents__query_database"


def _make_resolution(
    capability: str = "notion.search",
    status: str = "schema_deferred",
    matched_tools: tuple[str, ...] = (_NOTION_TOOL,),
    sign: bool = True,
    tamper_status: str | None = None,
):
    ctx = CRGContext(
        visible_tools=frozenset(),
        deferred_tools=frozenset(),
        latent_tools=frozenset({_NOTION_TOOL}),
        connector_servers=frozenset(),
        policy_blocked_tools=frozenset(),
        mode="live",
    )
    r = resolve_capability(capability, ctx)
    if tamper_status:
        from dataclasses import replace
        r = replace(r, status=tamper_status)
    if not sign:
        from dataclasses import replace
        r = replace(r, signature="")
    return r


def _make_unavailable_resolution(sign: bool = True):
    ctx = CRGContext(
        visible_tools=frozenset(),
        deferred_tools=frozenset(),
        latent_tools=frozenset(),
        connector_servers=frozenset(),
        policy_blocked_tools=frozenset(),
        mode="live",
    )
    r = resolve_capability("notion.search", ctx)
    assert r.status == "unavailable"
    if not sign:
        from dataclasses import replace
        r = replace(r, signature="")
    return r


# ── Absence language detection ────────────────────────────────────────────────

class TestAbsenceLanguageDetection:

    def test_no_notion_phrase_clean(self):
        assert not contains_absence_language("I can search Notion for you.")

    def test_dont_have_access_to(self):
        assert contains_absence_language("I don't have access to Notion.")

    def test_do_not_have_access_to(self):
        assert contains_absence_language("I do not have access to Notion.")

    def test_cannot_access(self):
        assert contains_absence_language("I cannot access the calendar.")

    def test_cant_reach(self):
        assert contains_absence_language("I can't reach your email.")

    def test_no_tool_available_for(self):
        assert contains_absence_language("No tool is available for Notion search.")

    def test_not_able_to_access(self):
        assert contains_absence_language("I'm not able to access your GitHub.")

    def test_extract_phrases_returns_match(self):
        phrases = extract_absence_phrases("I don't have access to Notion.")
        assert len(phrases) >= 1
        assert any("don't have access to" in p.lower() for p in phrases)

    def test_extract_phrases_empty_on_clean_text(self):
        assert extract_absence_phrases("Here is the Notion search result.") == []


# ── extract_text_from_response_body ──────────────────────────────────────────

class TestExtractTextFromResponseBody:

    def test_extracts_text_block(self):
        import json
        body = json.dumps({
            "content": [
                {"type": "text", "text": "I don't have access to Notion."},
                {"type": "tool_use", "name": "some_tool"},
            ]
        }).encode()
        assert "don't have access" in extract_text_from_response_body(body)

    def test_empty_on_tool_only_response(self):
        import json
        body = json.dumps({
            "content": [{"type": "tool_use", "name": "some_tool"}]
        }).encode()
        assert extract_text_from_response_body(body) == ""

    def test_empty_on_malformed_body(self):
        assert extract_text_from_response_body(b"not json") == ""


# ── extract_text_from_sse_buf ─────────────────────────────────────────────────

class TestExtractTextFromSSEBuf:

    def test_extracts_text_deltas(self):
        import json
        events = [
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "I don't "}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "have access."}},
        ]
        buf = b"".join(
            f"data: {json.dumps(e)}\n\n".encode() for e in events
        )
        text = extract_text_from_sse_buf(buf)
        assert "don't" in text
        assert "have access" in text

    def test_ignores_tool_events(self):
        import json
        events = [
            {"type": "content_block_start", "content_block": {"type": "tool_use", "name": "foo"}},
        ]
        buf = b"".join(f"data: {json.dumps(e)}\n\n".encode() for e in events)
        assert extract_text_from_sse_buf(buf) == ""


# ── enforce_denial_gate ───────────────────────────────────────────────────────

class TestEnforceDenialGate:

    def test_no_absence_language_is_allowed(self):
        decision = enforce_denial_gate(
            "Here are your Notion search results.",
            [_make_resolution()],
        )
        assert decision.allowed

    def test_schema_deferred_resolution_blocks_absence_language(self):
        """The review's canonical conformance test."""
        resolution = _make_resolution(status="schema_deferred")
        text = "I don't have access to Notion."
        decision = enforce_denial_gate(text, [resolution])
        assert decision.allowed is False
        assert decision.violation_kind == "denial_violation"

    def test_callable_now_blocks_absence_language(self):
        ctx = CRGContext(
            visible_tools=frozenset({_NOTION_TOOL}),
            deferred_tools=frozenset(),
            latent_tools=frozenset(),
            connector_servers=frozenset(),
            policy_blocked_tools=frozenset(),
            mode="live",
        )
        resolution = resolve_capability("notion.search", ctx)
        assert resolution.status == "callable_now"
        decision = enforce_denial_gate("I cannot access Notion.", [resolution])
        assert decision.allowed is False
        assert decision.violation_kind == "denial_violation"

    def test_valid_signed_unavailable_allows_denial(self):
        resolution = _make_unavailable_resolution(sign=True)
        decision = enforce_denial_gate("I don't have access to Notion.", [resolution])
        assert decision.allowed is True
        assert decision.attached_resolution_status == "unavailable"
        assert decision.resolution_signature_valid is True

    def test_unsigned_unavailable_blocks_denial(self):
        resolution = _make_unavailable_resolution(sign=False)
        decision = enforce_denial_gate("I don't have access to Notion.", [resolution])
        assert decision.allowed is False
        assert decision.violation_kind == "denial_violation"
        assert decision.resolution_signature_valid is False

    def test_no_resolutions_blocks_denial(self):
        decision = enforce_denial_gate("I can't reach your calendar.", [])
        assert decision.allowed is False
        assert decision.violation_kind == "denial_violation"
        assert decision.attached_resolution_status is None

    def test_rewrite_action_populated_on_violation(self):
        resolution = _make_resolution(status="schema_deferred")
        decision = enforce_denial_gate("I don't have access to Notion.", [resolution])
        assert decision.rewrite_action is not None
        assert decision.rewrite_action != "re_resolve_capability"

    def test_violation_record_is_json_serializable(self):
        import json
        resolution = _make_resolution(status="schema_deferred")
        decision = enforce_denial_gate("I don't have access to Notion.", [resolution])
        record = denial_violation_record(decision, "I don't have access to Notion.", "notion.search")
        assert record["kind"] == "denial_violation"
        json.dumps(record)


# ── Signature verification ────────────────────────────────────────────────────

class TestSignatureVerification:

    def test_resolver_computed_signature_is_valid(self):
        ctx = CRGContext(
            visible_tools=frozenset({_NOTION_TOOL}),
            deferred_tools=frozenset(),
            latent_tools=frozenset(),
            connector_servers=frozenset(),
            policy_blocked_tools=frozenset(),
            mode="live",
        )
        r = resolve_capability("notion.search", ctx)
        assert r.signature != ""
        assert _resolution_signature_valid(r)

    def test_tampered_status_invalidates_signature(self):
        from dataclasses import replace
        ctx = CRGContext(
            visible_tools=frozenset({_NOTION_TOOL}),
            deferred_tools=frozenset(),
            latent_tools=frozenset(),
            connector_servers=frozenset(),
            policy_blocked_tools=frozenset(),
            mode="live",
        )
        r = resolve_capability("notion.search", ctx)
        tampered = replace(r, status="unavailable")
        assert not _resolution_signature_valid(tampered)

    def test_tampered_matched_tools_invalidates_signature(self):
        from dataclasses import replace
        ctx = CRGContext(
            visible_tools=frozenset({_NOTION_TOOL}),
            deferred_tools=frozenset(),
            latent_tools=frozenset(),
            connector_servers=frozenset(),
            policy_blocked_tools=frozenset(),
            mode="live",
        )
        r = resolve_capability("notion.search", ctx)
        tampered = replace(r, matched_tools=("mcp__evil__tool",))
        assert not _resolution_signature_valid(tampered)

    def test_empty_signature_is_invalid(self):
        from dataclasses import replace
        ctx = CRGContext(
            visible_tools=frozenset({_NOTION_TOOL}),
            deferred_tools=frozenset(),
            latent_tools=frozenset(),
            connector_servers=frozenset(),
            policy_blocked_tools=frozenset(),
            mode="live",
        )
        r = resolve_capability("notion.search", ctx)
        unsigned = replace(r, signature="")
        assert not _resolution_signature_valid(unsigned)

    def test_tampered_unavailable_blocks_denial(self):
        """Core tamper test: mutate status on a signed resolution, gate rejects."""
        from dataclasses import replace
        ctx = CRGContext(
            visible_tools=frozenset({_NOTION_TOOL}),
            deferred_tools=frozenset(),
            latent_tools=frozenset(),
            connector_servers=frozenset(),
            policy_blocked_tools=frozenset(),
            mode="live",
        )
        real = resolve_capability("notion.search", ctx)
        tampered = replace(real, status="unavailable")
        assert not _resolution_signature_valid(tampered)
        decision = enforce_denial_gate("I don't have access to Notion.", [tampered])
        assert decision.allowed is False
        assert decision.violation_kind == "denial_violation"
