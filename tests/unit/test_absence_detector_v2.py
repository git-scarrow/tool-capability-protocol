"""Fixture gate + guard unit tests for the v2 absence detector (CRG Phase 2B).

The fixture (tests/data/absence_audit_v1.jsonl) contains every historical
denial_violation row from decisions.jsonl (2026-04-08 → 2026-07-06),
hand-labeled genuine/fp with a per-row basis note.

Hard gate (design kill line, stated in counts because N is small):
  * Zero genuine lost — every confident, gate-relevant, context-testable
    genuine row must be detected as Tier A in-surface.
  * FP reduction — at most 2 confident, context-testable fp rows may still
    flag as Tier A in-surface.

Rows with needs_review=true or context_quality="phrase_only" are reported,
never asserted (their 300-char excerpts lost the disambiguating context).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tcp.proxy.absence_language import (
    AbsenceDetectionV2,
    detect_absence_v2,
    extract_context_windows,
    strip_reported_context,
)

FIXTURE = Path(__file__).parent.parent / "data" / "absence_audit_v1.jsonl"


def _load_rows() -> list[dict]:
    with FIXTURE.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def _row_text(row: dict) -> str:
    """Best reconstruction of the response text available for this row."""
    phrase = row["matched_phrase"]
    excerpt = row["excerpt"]
    if row["context_quality"] == "full":
        return excerpt
    # window: excerpt (response head) + the matched window from later in the
    # same response — concatenation preserves narration prefixes and headers.
    return excerpt + "\n" + phrase


def _testable(row: dict) -> bool:
    return not row["needs_review"] and row["context_quality"] in ("full", "window")


def _detect(row: dict) -> AbsenceDetectionV2:
    return detect_absence_v2(_row_text(row), surface_tokens=row["surface_tokens"])


class TestFixtureGate:
    def test_fixture_is_complete(self) -> None:
        rows = _load_rows()
        assert len(rows) >= 46
        assert all(r["label"] in ("genuine", "fp") for r in rows)
        assert all(r["label_basis"] for r in rows)

    def test_zero_genuine_lost(self) -> None:
        """Every confident, gate-relevant, testable genuine row → Tier A in-surface."""
        missed = [
            r["id"]
            for r in _load_rows()
            if r["label"] == "genuine"
            and r["gate_relevant"]
            and _testable(r)
            and not ((d := _detect(r)).tier_a and d.tier_a_in_surface)
        ]
        assert missed == [], f"genuine denials lost by v2: {missed}"

    def test_fp_reduction(self) -> None:
        """At most 2 confident, testable fp rows may remain violation-eligible."""
        surviving = [
            r["id"]
            for r in _load_rows()
            if r["label"] == "fp"
            and _testable(r)
            and (d := _detect(r)).tier_a
            and d.tier_a_in_surface
        ]
        assert len(surviving) <= 2, f"fp rows still violation-eligible: {surviving}"

    def test_untestable_rows_reported(self, capsys: pytest.CaptureFixture) -> None:
        rows = [r for r in _load_rows() if not _testable(r)]
        print(f"\n{len(rows)} rows excluded from hard gate (needs_review/phrase_only)")
        assert len(rows) < len(_load_rows())


class TestGuards:
    SURFACE = ["bay-view-graph", "notion-agents", "get_file_content"]

    def test_first_person_in_surface(self) -> None:
        d = detect_absence_v2("I can't access Notion page body content right now.")
        assert d.tier_a and d.tier_a_in_surface

    def test_first_person_out_of_surface(self) -> None:
        d = detect_absence_v2("That file is on the Mac — I can't reach it from here.")
        assert d.tier_a and not d.tier_a_in_surface

    def test_past_tense_report_rejected(self) -> None:
        d = detect_absence_v2(
            "The push failed because the GitHub mirror was unreachable today."
        )
        assert not d.tier_a

    def test_idiom_no_way_to_know(self) -> None:
        d = detect_absence_v2("There's no way to know which Notion page they meant.")
        assert not d.tier_a

    def test_no_way_with_capability_context(self) -> None:
        d = detect_absence_v2(
            "The get_file_content tool only returns flattened text. "
            "There's no way to get the raw binary back out.",
            surface_tokens=self.SURFACE,
        )
        assert d.tier_a and d.tier_a_in_surface

    def test_quoted_denial_stripped(self) -> None:
        d = detect_absence_v2(
            'The model emitted "I can\'t access Notion" while the tool was callable.'
        )
        assert not d.tier_a

    def test_fenced_code_stripped(self) -> None:
        d = detect_absence_v2(
            "Run this:\n```\necho 'I cannot access Notion'\n```\nAll good."
        )
        assert not d.tier_a

    def test_narration_suppressed(self) -> None:
        d = detect_absence_v2(
            "<analysis>\nEarlier the user noted I can't access Notion.\n</analysis>"
        )
        assert not d.tier_a and d.narration_suppressed

    def test_artifact_noun_not_capability(self) -> None:
        d = detect_absence_v2("Closing out directly since there's no GitHub Issue.")
        assert not d.tier_a

    def test_ambiguous_token_needs_qualifier(self) -> None:
        assert not detect_absence_v2(
            "There is no email from Earl in the thread."
        ).tier_a
        d = detect_absence_v2("No email server is connected in this session.")
        assert d.tier_a and d.tier_a_in_surface

    def test_subject_pattern_disconnected(self) -> None:
        d = detect_absence_v2(
            "The Notion server (notion-agents) has disconnected this session, "
            "so I can't write the date right now.",
            surface_tokens=self.SURFACE,
        )
        assert d.tier_a and d.tier_a_in_surface

    def test_token_inside_identifier_not_matched(self) -> None:
        d = detect_absence_v2(
            "Signing uses id_op_github.pub whose private key isn't available "
            "in this non-interactive session."
        )
        assert not d.tier_a_in_surface

    def test_success_report_not_flagged(self) -> None:
        d = detect_absence_v2("Result: PASS. No tool failed, no access denied.")
        assert not d.tier_a

    def test_tier_b_still_observes(self) -> None:
        d = detect_absence_v2("Notion seems unavailable somehow.")
        assert d.tier_b


class TestHelpers:
    def test_strip_keeps_short_inline_identifiers(self) -> None:
        out = strip_reported_context("the `get_file_content` tool")
        assert "get_file_content" in out

    def test_strip_drops_long_inline_code(self) -> None:
        out = strip_reported_context("run `echo I cannot access Notion now`")
        assert "cannot access" not in out

    def test_context_windows_bounded(self) -> None:
        text = "x" * 1000 + "I can't access Notion" + "y" * 1000
        wins = extract_context_windows(text, ["I can't access Notion"], radius=50)
        assert len(wins) == 1 and len(wins[0]) <= 121


class TestEvaluateDenialV2:
    """Adjudication rules: tier/surface verdicts × resolution statuses."""

    def _resolution(self, status: str):
        from tcp.proxy.capability_resolution_gate import CRGContext, resolve_capability

        tool = "mcp__notion-agents__query_database"
        latent = frozenset({tool}) if status == "schema_deferred" else frozenset()
        ctx = CRGContext(
            visible_tools=frozenset(),
            deferred_tools=frozenset(),
            latent_tools=latent,
            connector_servers=frozenset(),
            policy_blocked_tools=frozenset(),
            mode="live",
        )
        r = resolve_capability("notion.search", ctx)
        assert r.status == status
        return r

    def test_in_surface_no_resolutions_is_violation(self) -> None:
        from tcp.proxy.denial_enforcement import evaluate_denial_v2

        d = evaluate_denial_v2("I can't access Notion from this session.", [])
        assert d.violation and d.reason == "tier_a_in_surface_no_resolutions"

    def test_in_surface_deferred_is_violation_with_rewrite(self) -> None:
        from tcp.proxy.denial_enforcement import evaluate_denial_v2

        d = evaluate_denial_v2(
            "I can't access Notion from this session.",
            [self._resolution("schema_deferred")],
        )
        assert d.violation
        assert d.reason == "tier_a_with_reachable_status_schema_deferred"
        assert d.rewrite_action == "surface_schema_deferred_tool"

    def test_valid_unavailable_resolution_allows(self) -> None:
        from tcp.proxy.denial_enforcement import evaluate_denial_v2

        d = evaluate_denial_v2(
            "I can't access Notion from this session.",
            [self._resolution("unavailable")],
        )
        assert not d.violation and d.reason == "valid_unavailable_resolution"

    def test_out_of_surface_never_violates(self) -> None:
        from tcp.proxy.denial_enforcement import evaluate_denial_v2

        d = evaluate_denial_v2(
            "That screenshot is on your Mac, so I can't reach it from here.",
            [self._resolution("schema_deferred")],
        )
        assert not d.violation and d.reason == "tier_a_out_of_surface"

    def test_tier_b_only_never_violates(self) -> None:
        from tcp.proxy.denial_enforcement import evaluate_denial_v2

        d = evaluate_denial_v2("Notion seems unavailable somehow.", [])
        assert not d.violation and d.reason == "tier_b_candidate"


class TestProxyDualRunWiring:
    """cc_proxy._check_denial_enforcement stamps v2 fields alongside v1."""

    def test_v1_and_v2_fields_stamped_together(self) -> None:
        from tcp.proxy.cc_proxy import _check_denial_enforcement

        meta: dict = {"crg_resolutions": []}
        _check_denial_enforcement("I don't have access to Notion.", meta)
        assert meta["denial_violation"] is True  # v1 unchanged
        assert meta["denial_v2_tier_a"] is True
        assert meta["denial_v2_violation"] is True
        assert meta["denial_v2_reason"] == "tier_a_in_surface_no_resolutions"
        assert meta["denial_detector_version_v2"] == "crg.absence.v2"
        assert meta["denial_context_excerpts"]

    def test_v1_flags_quoted_text_v2_does_not(self) -> None:
        """The historical FP class: v1 fires on quoted denials, v2 stays clean."""
        from tcp.proxy.cc_proxy import _check_denial_enforcement

        meta: dict = {"crg_resolutions": []}
        _check_denial_enforcement(
            'The model emitted "I can\'t access Notion" during the test.', meta
        )
        assert meta["denial_violation"] is True  # v1 (unchanged, imprecise)
        assert meta["denial_v2_violation"] is False  # v2 quote-strip
        assert meta["denial_v2_tier_a"] is False

    def test_clean_response_stamps_nothing(self) -> None:
        from tcp.proxy.cc_proxy import _check_denial_enforcement

        meta: dict = {"crg_resolutions": []}
        _check_denial_enforcement("All tests pass. Committed as abc123.", meta)
        assert "denial_violation" not in meta
        assert "denial_v2_violation" not in meta

    def test_surface_tokens_derived_from_meta(self) -> None:
        from tcp.proxy.cc_proxy import _surface_tokens_from_meta

        meta = {
            "materialized_schema_tools": [
                "mcp__bay-view-graph__send_email",
                "mcp__git__git_status",  # server "git" excluded as generic
                "Read",  # non-MCP ignored
            ],
            "deferred_schema_tools": ["mcp__notion-agents__query_database"],
        }
        tokens = _surface_tokens_from_meta(meta)
        assert "bay-view-graph" in tokens
        assert "send_email" in tokens
        assert "notion-agents" in tokens
        assert "git" not in tokens

    def test_out_of_surface_infra_claim_not_v2_violation(self) -> None:
        from tcp.proxy.cc_proxy import _check_denial_enforcement

        meta: dict = {"crg_resolutions": []}
        _check_denial_enforcement(
            "That path is on the Mac — I can't reach it from the Gentoo session.",
            meta,
        )
        assert meta["denial_v2_violation"] is False
        assert meta["denial_v2_reason"] == "tier_a_out_of_surface"
