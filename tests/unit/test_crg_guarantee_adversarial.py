"""Adversarial verification of the CRG denial-enforcement guarantee.

The guarantee under test
------------------------
  If a signed resolver decision establishes that a capability is available,
  and the assistant response nevertheless denies that capability in detected
  absence language, the proxy records a denial violation.

History of findings → fixes (each test below is a regression guard):

  Stream-abort breach        → cc_proxy.py finally block now calls
                               _check_denial_enforcement before
                               _write_decision_record.

  Pattern evasion            → _ABSENCE_PATTERNS expanded; structural
                               capability-token / negation-token co-occurrence
                               detector added as a backstop.

  HMAC key in source         → default secret removed; resolver generates an
                               ephemeral per-process key when env var is unset,
                               or fails closed under TCP_CRG_REQUIRE_KEY=1.

  Signature scope            → _compute_signature now signs checked_surfaces
                               and surface_results in addition to the original
                               (resolver_id, capability, status, matched_tools).
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import os
from dataclasses import replace

import pytest

from tcp.proxy.absence_language import contains_absence_language
from tcp.proxy.capability_resolution_gate import (
    CRGContext,
    CapabilityResolution,
    SurfaceResult,
    _CRG_RESOLVER_SECRET,
    _REQUIRED_SIX_SURFACES,
    _compute_signature,
    resolve_capability,
)
from tcp.proxy.denial_enforcement import (
    DenialGateDecision,
    _has_valid_unavailable,
    _resolution_signature_valid,
    enforce_denial_gate,
)

_NOTION_TOOL = "mcp__notion-agents__query_database"
_KNOWN_LITERAL_KEY = "crg-resolver-default-v1"  # the historical default

# ── fixtures ──────────────────────────────────────────────────────────────────


def _available_ctx() -> CRGContext:
    return CRGContext(
        visible_tools=frozenset(),
        deferred_tools=frozenset(),
        latent_tools=frozenset({_NOTION_TOOL}),
        connector_servers=frozenset(),
        policy_blocked_tools=frozenset(),
        mode="live",
    )


def _unavailable_ctx() -> CRGContext:
    return CRGContext(
        visible_tools=frozenset(),
        deferred_tools=frozenset(),
        latent_tools=frozenset(),
        connector_servers=frozenset(),
        policy_blocked_tools=frozenset(),
        mode="live",
    )


def _make_available_resolution() -> CapabilityResolution:
    r = resolve_capability("notion.search", _available_ctx())
    assert r.status in (
        "callable_now",
        "schema_deferred",
    ), f"pre-condition: expected available resolution, got {r.status}"
    return r


def _forge_with_known_literal(
    capability: str = "notion.search",
) -> CapabilityResolution:
    """Forge an unavailable resolution using the historical literal key.

    With the fix, this should NOT validate, because the resolver no longer
    uses the literal key.
    """
    resolver_id = "crg:v1"
    status = "unavailable"
    matched_tools: tuple[str, ...] = ()
    surface_results = tuple(
        SurfaceResult(
            surface=s, matched=False, tools=(), timestamp="", reason="", stale=False
        )
        for s in _REQUIRED_SIX_SURFACES
    )

    canonical_surfaces = sorted(
        (
            {
                "surface": sr.surface,
                "matched": sr.matched,
                "tools": sorted(sr.tools),
                "stale": sr.stale,
            }
            for sr in surface_results
        ),
        key=lambda d: d["surface"],
    )
    payload = json.dumps(
        {
            "resolver_id": resolver_id,
            "requested_capability": capability,
            "status": status,
            "matched_tools": sorted(matched_tools),
            "checked_surfaces": sorted(_REQUIRED_SIX_SURFACES),
            "surface_results": canonical_surfaces,
        },
        sort_keys=True,
    )
    forged_sig = hmac_mod.new(
        _KNOWN_LITERAL_KEY.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()[:32]

    return CapabilityResolution(
        requested_capability=capability,
        status=status,
        matched_tools=matched_tools,
        checked_surfaces=_REQUIRED_SIX_SURFACES,
        surface_results=surface_results,
        confidence=1.0,
        reason="forged",
        resolver_id=resolver_id,
        signature=forged_sig,
    )


# ── Nominal guarantee ─────────────────────────────────────────────────────────


class TestNominalGuaranteeHolds:
    def test_p1_true_p2_true_produces_violation(self):
        resolution = _make_available_resolution()
        decision = enforce_denial_gate("I don't have access to Notion.", [resolution])
        assert decision.allowed is False
        assert decision.violation_kind == "denial_violation"

    def test_p1_true_p2_false_no_violation(self):
        resolution = _make_available_resolution()
        decision = enforce_denial_gate(
            "Here are the Notion search results you requested.",
            [resolution],
        )
        assert decision.allowed is True

    def test_p1_false_p2_true_allows_denial(self):
        resolution = resolve_capability("notion.search", _unavailable_ctx())
        assert resolution.status == "unavailable"
        decision = enforce_denial_gate("I don't have access to Notion.", [resolution])
        assert decision.allowed is True

    def test_p1_true_p2_true_callable_now(self):
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


# ── Stream-abort breach: REMEDIATED ───────────────────────────────────────────


class TestStreamAbortRemediated:
    """Regression guard: the finally block must call _check_denial_enforcement."""

    def test_finally_block_calls_denial_enforcement(self):
        """AST inspection: a Try whose finally body calls _check_denial_enforcement
        must exist in cc_proxy.py."""
        import ast
        from pathlib import Path

        proxy_path = (
            Path(__file__).parent.parent.parent / "tcp" / "proxy" / "cc_proxy.py"
        )
        tree = ast.parse(proxy_path.read_text())

        denial_check_in_any_finally = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            for stmt in node.finalbody:
                for subnode in ast.walk(stmt):
                    if isinstance(subnode, ast.Call):
                        func = subnode.func
                        name = ""
                        if isinstance(func, ast.Name):
                            name = func.id
                        elif isinstance(func, ast.Attribute):
                            name = func.attr
                        if name == "_check_denial_enforcement":
                            denial_check_in_any_finally = True

        assert denial_check_in_any_finally, (
            "REGRESSION: no finally block in cc_proxy.py calls "
            "_check_denial_enforcement.  Stream abort would silently skip "
            "denial enforcement again."
        )

    def test_text_buf_is_extracted_and_checked_on_abort(self):
        """The buffer logic the finally block depends on must still extract text."""
        from tcp.proxy.absence_language import extract_text_from_sse_buf

        chunks = [
            b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"I don\'t "}}\n\n',
            b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"have access to Notion."}}\n\n',
        ]
        partial_buf = b"".join(chunks)
        extracted = extract_text_from_sse_buf(partial_buf)
        assert contains_absence_language(extracted), (
            "extract_text_from_sse_buf no longer recovers denial text from a "
            "partial buffer; finally-block enforcement would be ineffective."
        )


# ── Pattern evasion: REMEDIATED ───────────────────────────────────────────────


class TestPatternEvasionRemediated:
    """Regression guard: the 14 documented evasion phrases must all be detected."""

    _PREVIOUSLY_EVADING_PHRASES = [
        "Notion is not accessible to me.",
        "Notion is not accessible from this context.",
        "I lack access to Notion.",
        "I lack the ability to query Notion.",
        "There's no Notion capability available.",
        "There is no Notion capability available in this session.",
        "Notion integration is unavailable.",
        "The Notion integration is not available.",
        "Notion cannot be accessed from here.",
        "Notion cannot be queried in this context.",
        "Unfortunately, the Notion integration isn't available.",
        "I'm afraid I don't support Notion.",
        "I don't support Notion queries.",
        "I have no way to search Notion.",
    ]

    @pytest.mark.parametrize("phrase", _PREVIOUSLY_EVADING_PHRASES)
    def test_previously_evading_phrase_now_detected(self, phrase: str):
        assert contains_absence_language(
            phrase
        ), f"REGRESSION: '{phrase}' is no longer detected as absence language."

    def test_evasion_phrase_now_blocks_unjustified_denial(self):
        resolution = _make_available_resolution()
        evasive_denial = "Notion is not accessible to me."
        decision = enforce_denial_gate(evasive_denial, [resolution])
        assert decision.allowed is False, (
            "REGRESSION: gate silently passed an evasive denial that now "
            "should be detected."
        )
        assert decision.violation_kind == "denial_violation"

    def test_clean_text_still_passes(self):
        """Precision check — non-denial text containing capability tokens stays clean."""
        clean_phrases = [
            "Here are the Notion search results.",
            "Your GitHub PR is now ready for review.",
            "I'll send the calendar invite shortly.",
            "I queried the Oracle database successfully.",
            "Found 3 results in your email inbox.",
            "Notion search returned 12 pages.",
            "The Notion integration is enabled and working.",
        ]
        for text in clean_phrases:
            assert not contains_absence_language(
                text
            ), f"FALSE POSITIVE: '{text}' is being flagged as absence language."


# ── HMAC key in source: REMEDIATED ────────────────────────────────────────────


class TestHMACKeyInSourceRemediated:
    """Regression guard: forging with the historical literal key must fail."""

    def test_runtime_secret_is_not_the_known_literal(self):
        """Without TCP_CRG_RESOLVER_SECRET set, the runtime key must be ephemeral
        (unequal to the known historical literal)."""
        if os.environ.get("TCP_CRG_RESOLVER_SECRET"):
            pytest.skip("env var explicitly set; ephemeral key path not exercised")
        assert _CRG_RESOLVER_SECRET != _KNOWN_LITERAL_KEY, (
            "REGRESSION: resolver secret has reverted to the known literal "
            "from source.  Source readers can forge unavailable resolutions."
        )

    def test_forgery_with_known_literal_key_fails_signature_check(self):
        forged = _forge_with_known_literal("notion.search")
        assert not _resolution_signature_valid(
            forged
        ), "REGRESSION: forgery using the historical literal key validates."

    def test_forgery_with_known_literal_key_does_not_authorise_denial(self):
        forged = _forge_with_known_literal("notion.search")
        decision = enforce_denial_gate("I don't have access to Notion.", [forged])
        assert (
            decision.allowed is False
        ), "REGRESSION: forged unavailable resolution silenced the denial gate."
        assert decision.violation_kind == "denial_violation"

    def test_genuine_unavailable_still_authorises_denial(self):
        """Control: legitimate resolver-produced unavailable still works end-to-end."""
        real = resolve_capability("notion.search", _unavailable_ctx())
        assert real.status == "unavailable"
        decision = enforce_denial_gate("I don't have access to Notion.", [real])
        assert decision.allowed is True

    def test_require_key_flag_fails_closed_when_unset(self):
        """With TCP_CRG_REQUIRE_KEY=1 and no secret env var, _load_resolver_secret
        must raise."""
        from tcp.proxy.capability_resolution_gate import _load_resolver_secret

        prev_secret = os.environ.pop("TCP_CRG_RESOLVER_SECRET", None)
        os.environ["TCP_CRG_REQUIRE_KEY"] = "1"
        try:
            with pytest.raises(RuntimeError, match="TCP_CRG_RESOLVER_SECRET"):
                _load_resolver_secret()
        finally:
            del os.environ["TCP_CRG_REQUIRE_KEY"]
            if prev_secret is not None:
                os.environ["TCP_CRG_RESOLVER_SECRET"] = prev_secret


# ── Signature scope: REMEDIATED ───────────────────────────────────────────────


class TestSignatureScopeRemediated:
    """Regression guard: checked_surfaces and surface_results are now signed."""

    def test_mutating_checked_surfaces_invalidates_signature(self):
        ctx = CRGContext(
            visible_tools=frozenset({_NOTION_TOOL}),
            deferred_tools=frozenset(),
            latent_tools=frozenset(),
            connector_servers=frozenset(),
            policy_blocked_tools=frozenset(),
            mode="live",
        )
        r = resolve_capability("notion.search", ctx)
        assert _resolution_signature_valid(r)
        stripped = replace(r, checked_surfaces=())
        assert not _resolution_signature_valid(
            stripped
        ), "REGRESSION: checked_surfaces is no longer covered by the HMAC."

    def test_mutating_surface_results_invalidates_signature(self):
        r = resolve_capability("notion.search", _unavailable_ctx())
        assert _resolution_signature_valid(r)
        forged_surface = SurfaceResult(
            surface="visible",
            matched=True,  # claim a match that didn't happen
            tools=(_NOTION_TOOL,),
            timestamp="",
            reason="forged",
            stale=False,
        )
        new_surfaces = (forged_surface,) + r.surface_results[1:]
        tampered = replace(r, surface_results=new_surfaces)
        assert not _resolution_signature_valid(
            tampered
        ), "REGRESSION: surface_results is no longer covered by the HMAC."

    def test_resolver_signed_resolutions_remain_valid(self):
        """Control: legitimate resolutions still pass the wider signature check."""
        for ctx in (_available_ctx(), _unavailable_ctx()):
            r = resolve_capability("notion.search", ctx)
            assert _resolution_signature_valid(r)


# ── End-to-end: forged + evasion combo ────────────────────────────────────────


class TestEndToEndDefenseInDepth:
    """Each fix is independent; verify no single fix relies on another."""

    def test_evasion_phrase_with_genuine_unavailable_resolution_allowed(self):
        """Pattern coverage shouldn't cause false positives when a real
        unavailable resolution backs the (evasive) denial."""
        real = resolve_capability("notion.search", _unavailable_ctx())
        decision = enforce_denial_gate("Notion is not accessible to me.", [real])
        assert decision.allowed is True

    def test_evasion_phrase_with_forged_resolution_blocked(self):
        """Belt-and-braces: forged key + evasive phrase = still blocked,
        because both layers fail closed."""
        forged = _forge_with_known_literal("notion.search")
        decision = enforce_denial_gate("Notion is not accessible to me.", [forged])
        assert decision.allowed is False
        assert decision.violation_kind == "denial_violation"
