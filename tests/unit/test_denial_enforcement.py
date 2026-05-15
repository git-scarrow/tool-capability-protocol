"""Unit tests for denial enforcement (CRG Phase 2).

Covers:
  - absence_language detection and extraction
  - enforce_denial_gate / may_emit_capability_denial: allowed/violation logic
  - contains_capability_denial, resolution_allows_denial
  - rewrite-action names per status
  - violation_kind="invalid_resolution" for incomplete surfaces
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
    _REQUIRED_SIX_SURFACES,
    CRGContext,
    _compute_signature,
    resolve_capability,
)
from tcp.proxy.denial_enforcement import (
    DenialGateDecision,
    _resolution_signature_valid,
    contains_capability_denial,
    denial_violation_record,
    enforce_denial_gate,
    may_emit_capability_denial,
    resolution_allows_denial,
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

        body = json.dumps(
            {
                "content": [
                    {"type": "text", "text": "I don't have access to Notion."},
                    {"type": "tool_use", "name": "some_tool"},
                ]
            }
        ).encode()
        assert "don't have access" in extract_text_from_response_body(body)

    def test_empty_on_tool_only_response(self):
        import json

        body = json.dumps(
            {"content": [{"type": "tool_use", "name": "some_tool"}]}
        ).encode()
        assert extract_text_from_response_body(body) == ""

    def test_empty_on_malformed_body(self):
        assert extract_text_from_response_body(b"not json") == ""


# ── extract_text_from_sse_buf ─────────────────────────────────────────────────


class TestExtractTextFromSSEBuf:
    def test_extracts_text_deltas(self):
        import json

        events = [
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "I don't "},
            },
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "have access."},
            },
        ]
        buf = b"".join(f"data: {json.dumps(e)}\n\n".encode() for e in events)
        text = extract_text_from_sse_buf(buf)
        assert "don't" in text
        assert "have access" in text

    def test_ignores_tool_events(self):
        import json

        events = [
            {
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "name": "foo"},
            },
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
        record = denial_violation_record(
            decision, "I don't have access to Notion.", "notion.search"
        )
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


# ── Phase-2 canonical API: may_emit_capability_denial ────────────────────────


def _make_policy_blocked_resolution():
    """Return a policy_blocked resolution for notion.search."""
    ctx = CRGContext(
        visible_tools=frozenset(),
        deferred_tools=frozenset(),
        latent_tools=frozenset(),
        connector_servers=frozenset(),
        policy_blocked_tools=frozenset({_NOTION_TOOL}),
        mode="live-strict",
    )
    r = resolve_capability("notion.search", ctx)
    assert r.status == "policy_blocked"
    return r


class TestMayEmitCapabilityDenial:
    """Spec-required tests for may_emit_capability_denial (CRG Phase 2)."""

    def test_no_absence_language_allowed(self):
        """1. No absence-language → allowed."""
        decision = may_emit_capability_denial(
            "Here are your Notion results.", [_make_resolution()]
        )
        assert decision.allowed is True
        assert decision.violation_kind is None

    def test_absence_no_resolutions_rejected(self):
        """2. Absence-language + no resolutions → rejected."""
        decision = may_emit_capability_denial("I don't have access to Notion.", [])
        assert decision.allowed is False
        assert decision.violation_kind == "denial_violation"

    def test_schema_deferred_rewrite_action(self):
        """3. schema_deferred → rejected with rewrite_action='surface_schema_deferred_tool'."""
        ctx = CRGContext(
            visible_tools=frozenset(),
            deferred_tools=frozenset(),
            latent_tools=frozenset({_NOTION_TOOL}),
            connector_servers=frozenset(),
            policy_blocked_tools=frozenset(),
            mode="live",
        )
        r = resolve_capability("notion.search", ctx)
        assert r.status == "schema_deferred"
        decision = may_emit_capability_denial("I don't have access to Notion.", [r])
        assert decision.allowed is False
        assert decision.rewrite_action == "surface_schema_deferred_tool"

    def test_callable_now_rewrite_action(self):
        """4. callable_now → rejected with rewrite_action='use_callable_tool'."""
        ctx = CRGContext(
            visible_tools=frozenset({_NOTION_TOOL}),
            deferred_tools=frozenset(),
            latent_tools=frozenset(),
            connector_servers=frozenset(),
            policy_blocked_tools=frozenset(),
            mode="live",
        )
        r = resolve_capability("notion.search", ctx)
        assert r.status == "callable_now"
        decision = may_emit_capability_denial("I cannot access Notion.", [r])
        assert decision.allowed is False
        assert decision.rewrite_action == "use_callable_tool"

    def test_approval_required_rewrite_action(self):
        """5. approval_required → rejected with rewrite_action='ask_for_approval'."""
        from dataclasses import replace

        ctx = CRGContext(
            visible_tools=frozenset(),
            deferred_tools=frozenset(),
            latent_tools=frozenset(),
            connector_servers=frozenset(),
            policy_blocked_tools=frozenset(),
            mode="live",
        )
        r = resolve_capability("notion.search", ctx)
        r = replace(r, status="approval_required")
        decision = may_emit_capability_denial("I don't have access to Notion.", [r])
        assert decision.allowed is False
        assert decision.rewrite_action == "ask_for_approval"

    def test_policy_blocked_rewrite_action(self):
        """6. policy_blocked → rejected with rewrite_action='explain_policy_block'."""
        r = _make_policy_blocked_resolution()
        decision = may_emit_capability_denial("I don't have access to Notion.", [r])
        assert decision.allowed is False
        assert decision.rewrite_action == "explain_policy_block"

    def test_unavailable_all_six_surfaces_allowed(self):
        """7. unavailable + all six surfaces + valid sig → allowed."""
        r = _make_unavailable_resolution(sign=True)
        decision = may_emit_capability_denial("I don't have access to Notion.", [r])
        assert decision.allowed is True

    def test_unavailable_missing_surface_invalid_resolution(self):
        """8. unavailable + missing a surface → invalid_resolution."""
        from dataclasses import replace

        r = _make_unavailable_resolution(sign=True)
        incomplete = tuple(s for s in r.checked_surfaces if s != "latent")
        r_bad = replace(r, checked_surfaces=incomplete)
        decision = may_emit_capability_denial("I don't have access to Notion.", [r_bad])
        assert decision.allowed is False
        assert decision.violation_kind == "invalid_resolution"

    def test_multiple_resolutions_one_valid_unavailable_allowed(self):
        """9. Multiple resolutions, one valid unavailable → allowed."""
        r_deferred = _make_resolution()
        r_unavail = _make_unavailable_resolution(sign=True)
        decision = may_emit_capability_denial(
            "I don't have access to Notion.", [r_deferred, r_unavail]
        )
        assert decision.allowed is True

    def test_case_insensitive_cant(self):
        """10. "can't" triggers absence-language detection."""
        assert may_emit_capability_denial("I can't access Notion.", []).allowed is False

    def test_case_insensitive_cannot(self):
        """10. "cannot" triggers absence-language detection."""
        assert (
            may_emit_capability_denial("I cannot access Notion.", []).allowed is False
        )

    def test_case_insensitive_dont(self):
        """10. "don't" triggers absence-language detection."""
        assert (
            may_emit_capability_denial("I don't have access to Notion.", []).allowed
            is False
        )

    def test_case_insensitive_do_not(self):
        """10. "do not" triggers absence-language detection."""
        assert (
            may_emit_capability_denial("I do not have access to Notion.", []).allowed
            is False
        )


class TestContainsCapabilityDenial:
    """contains_capability_denial wraps absence-language detection."""

    def test_absence_phrase_detected(self):
        assert contains_capability_denial("I don't have access to Notion.") is True

    def test_clean_text_not_detected(self):
        assert contains_capability_denial("I can search Notion for you.") is False

    def test_cannot_reach_detected(self):
        assert contains_capability_denial("I cannot reach your email.") is True

    def test_no_tool_available_detected(self):
        assert contains_capability_denial("No tool is available for Notion.") is True


class TestResolutionAllowsDenial:
    """resolution_allows_denial: only signed unavailable with all six surfaces passes."""

    def test_valid_unavailable_allows(self):
        r = _make_unavailable_resolution(sign=True)
        assert resolution_allows_denial(r) is True

    def test_unsigned_unavailable_denies(self):
        r = _make_unavailable_resolution(sign=False)
        assert resolution_allows_denial(r) is False

    def test_schema_deferred_denies(self):
        r = _make_resolution()
        assert resolution_allows_denial(r) is False

    def test_callable_now_denies(self):
        ctx = CRGContext(
            visible_tools=frozenset({_NOTION_TOOL}),
            deferred_tools=frozenset(),
            latent_tools=frozenset(),
            connector_servers=frozenset(),
            policy_blocked_tools=frozenset(),
            mode="live",
        )
        r = resolve_capability("notion.search", ctx)
        assert r.status == "callable_now"
        assert resolution_allows_denial(r) is False

    def test_unavailable_missing_surface_denies(self):
        from dataclasses import replace

        r = _make_unavailable_resolution(sign=True)
        incomplete = tuple(s for s in r.checked_surfaces if s != "latent")
        r_bad = replace(r, checked_surfaces=incomplete)
        assert resolution_allows_denial(r_bad) is False
