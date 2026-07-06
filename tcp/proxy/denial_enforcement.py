"""Denial enforcement for the Capability Resolution Gate — CRG Phase 2.

Implements the refusal gate: a capability denial is only permitted when the
resolver has returned a signed CapabilityResolution{status=unavailable}.

Public API (CRG Phase 2):
  contains_capability_denial(text) -> bool
  resolution_allows_denial(resolution) -> bool
  may_emit_capability_denial(text, resolutions) -> DenialGateDecision
  enforce_denial_gate(text, resolutions) -> DenialGateDecision  (compat alias)
  denial_violation_record(decision, excerpt, capability) -> dict

May-emit rule:
  A response containing absence-language is valid only if:
    1. At least one attached resolution has status=unavailable, AND
    2. That resolution carries a valid signature, AND
    3. That resolution lists all six required surfaces.

  Any other combination is a denial_violation.

Rewrite actions (status → action):
  callable_now      → use_callable_tool
  schema_deferred   → surface_schema_deferred_tool
  approval_required → ask_for_approval
  policy_blocked    → explain_policy_block

# TODO CRG-Phase-3: replace _resolution_signature_valid() with a
# verifier seam that supports out-of-process signing.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Sequence

from tcp.proxy.absence_language import (
    contains_absence_language,
    extract_absence_phrases,
)
from tcp.proxy.capability_resolution_gate import (
    _CRG_RESOLVER_SECRET,
    _REQUIRED_SIX_SURFACES,
    CapabilityResolution,
    _compute_signature,
)

# ── Rewrite-action mapping ─────────────────────────────────────────────────────

_REWRITE_ACTION: dict[str, str] = {
    "callable_now": "use_callable_tool",
    "schema_deferred": "surface_schema_deferred_tool",
    "approval_required": "ask_for_approval",
    "policy_blocked": "explain_policy_block",
}

# Precedence when picking one rewrite action from multiple resolutions.
_REWRITE_PRECEDENCE: tuple[str, ...] = (
    "callable_now",
    "schema_deferred",
    "policy_blocked",
    "approval_required",
)


@dataclass(frozen=True)
class DenialGateDecision:
    """Result of running a response through the refusal gate.

    Phase-2 canonical fields:
      allowed              — True means the denial is justified.
      violation_kind       — "denial_violation" or "invalid_resolution" when allowed=False.
      reason               — Human-readable explanation.
      rewrite_action       — Suggested corrective action (None when allowed=True).
      matched_absence_phrase — First absence phrase found (None when none detected).

    Legacy fields (kept for backward compat with denial_violation_record and callers):
      matched_phrases             — All detected absence phrases.
      attached_resolution_status  — Status of the first resolution considered.
      resolution_signature_valid  — Signature check result (None when no resolution).
    """

    allowed: bool
    # Populated only when allowed=False.
    violation_kind: str | None  # "denial_violation"
    matched_phrases: tuple[str, ...]  # absence phrases that triggered
    attached_resolution_status: str | None  # resolution status, if attached
    resolution_signature_valid: bool | None  # None = no resolution attached
    rewrite_action: str | None  # suggested rewrite target
    reason: str | None = None  # human-readable denial-gate reason
    matched_absence_phrase: str | None = None  # first absence phrase that triggered


def _resolution_signature_valid(resolution: CapabilityResolution) -> bool:
    """Return True if the resolution's signature is correct.

    # TODO CRG-Phase-3: replace with a verifier seam for out-of-process signing.
    """
    if not resolution.signature:
        return False
    expected = _compute_signature(
        resolver_id=resolution.resolver_id,
        requested_capability=resolution.requested_capability,
        status=resolution.status,
        matched_tools=resolution.matched_tools,
        checked_surfaces=resolution.checked_surfaces,
        surface_results=resolution.surface_results,
    )
    return hmac.compare_digest(resolution.signature, expected)


def resolution_allows_denial(resolution: CapabilityResolution) -> bool:
    """Return True if this resolution provides valid proof of unavailability.

    Requirements (all three must hold):
      - status == "unavailable"
      - checked_surfaces exactly matches all six required surfaces
      - HMAC signature is valid (resolver-authored)
    """
    if resolution.status != "unavailable":
        return False
    if set(resolution.checked_surfaces) != set(_REQUIRED_SIX_SURFACES):
        return False
    return _resolution_signature_valid(resolution)


def contains_capability_denial(text: str) -> bool:
    """Return True if text contains capability-denial (absence-language) phrases."""
    return contains_absence_language(text)


def _pick_rewrite_action(resolutions: Sequence[CapabilityResolution]) -> str | None:
    """Return the most actionable rewrite-action string from the resolution set."""
    status_set = {r.status for r in resolutions}
    for status in _REWRITE_PRECEDENCE:
        if status in status_set:
            return _REWRITE_ACTION.get(status)
    return None


def may_emit_capability_denial(
    text: str,
    resolutions: Sequence[CapabilityResolution],
) -> DenialGateDecision:
    """Check whether a response text may include capability-absence language.

    Rules applied in order:
      1. No absence-language → allowed.
      2. Absence-language + no resolutions → denial_violation.
      3. Any resolution: status=unavailable + all six surfaces + valid sig → allowed.
      4. Unavailable resolution with incomplete surfaces → invalid_resolution.
      5. Unavailable resolution with correct surfaces but invalid/missing sig
         → denial_violation.
      6. All other statuses (callable_now / schema_deferred / approval_required /
         policy_blocked) → denial_violation with appropriate rewrite_action.

    Returns DenialGateDecision.  ``allowed=True`` means the denial is justified.
    ``allowed=False`` is a denial_violation or invalid_resolution.
    """
    if not contains_absence_language(text):
        return DenialGateDecision(
            allowed=True,
            violation_kind=None,
            matched_phrases=(),
            attached_resolution_status=None,
            resolution_signature_valid=None,
            rewrite_action=None,
            reason="no absence-language detected",
            matched_absence_phrase=None,
        )

    phrases = tuple(extract_absence_phrases(text))
    first_phrase = phrases[0] if phrases else None

    # Rule 2: no resolutions at all.
    if not resolutions:
        return DenialGateDecision(
            allowed=False,
            violation_kind="denial_violation",
            matched_phrases=phrases,
            attached_resolution_status=None,
            resolution_signature_valid=None,
            rewrite_action=None,
            reason="absence-language emitted with no CRG resolutions attached",
            matched_absence_phrase=first_phrase,
        )

    # Rule 3: check for any valid signed unavailable resolution.
    for r in resolutions:
        if resolution_allows_denial(r):
            return DenialGateDecision(
                allowed=True,
                violation_kind=None,
                reason="valid signed unavailable resolution present",
                rewrite_action=None,
                matched_absence_phrase=first_phrase,
                matched_phrases=phrases,
                attached_resolution_status="unavailable",
                resolution_signature_valid=True,
            )

    # Rule 4: unavailable resolution with incomplete surfaces → invalid_resolution.
    for r in resolutions:
        if r.status == "unavailable" and set(r.checked_surfaces) != set(
            _REQUIRED_SIX_SURFACES
        ):
            return DenialGateDecision(
                allowed=False,
                violation_kind="invalid_resolution",
                reason=(
                    f"unavailable resolution has incomplete surface check; "
                    f"got {sorted(r.checked_surfaces)}, "
                    f"need {sorted(_REQUIRED_SIX_SURFACES)}"
                ),
                rewrite_action=None,
                matched_absence_phrase=first_phrase,
                matched_phrases=phrases,
                attached_resolution_status="unavailable",
                resolution_signature_valid=_resolution_signature_valid(r),
            )

    # Rule 5: unavailable with correct surfaces but invalid/missing signature.
    for r in resolutions:
        if r.status == "unavailable":
            return DenialGateDecision(
                allowed=False,
                violation_kind="denial_violation",
                reason="unavailable resolution has invalid or missing signature",
                rewrite_action=None,
                matched_absence_phrase=first_phrase,
                matched_phrases=phrases,
                attached_resolution_status="unavailable",
                resolution_signature_valid=False,
            )

    # Rule 6: callable / deferred / approval / policy — pick appropriate rewrite.
    first = resolutions[0]
    rewrite = _pick_rewrite_action(resolutions)
    sig_valid = _resolution_signature_valid(first) if first.signature else False
    return DenialGateDecision(
        allowed=False,
        violation_kind="denial_violation",
        reason=(
            f"capability has attached status={first.status!r}; "
            "absence-language is unwarranted when the capability is reachable"
        ),
        rewrite_action=rewrite,
        matched_absence_phrase=first_phrase,
        matched_phrases=phrases,
        attached_resolution_status=first.status,
        resolution_signature_valid=sig_valid,
    )


def enforce_denial_gate(
    text: str,
    resolutions: Sequence[CapabilityResolution],
) -> DenialGateDecision:
    """Compat alias for may_emit_capability_denial (CRG Phase 2)."""
    return may_emit_capability_denial(text, resolutions)


def denial_violation_record(
    decision: DenialGateDecision,
    model_text_excerpt: str,
    requested_capability: str | None = None,
) -> dict:
    """Serialize a denial gate decision to a decisions.jsonl record shape."""
    return {
        "kind": "denial_violation",
        "model_text_excerpt": model_text_excerpt[:300],
        "matched_phrases": list(decision.matched_phrases),
        "matched_absence_phrase": decision.matched_absence_phrase,
        "attached_resolution_status": decision.attached_resolution_status,
        "resolution_signature_valid": decision.resolution_signature_valid,
        "rewrite_action": decision.rewrite_action,
        "violation_kind": decision.violation_kind,
        "reason": decision.reason,
        "requested_capability": requested_capability,
    }


# ── Denial gate v2 (CRG Phase 2B) ─────────────────────────────────────────────
# Dual-run alongside v1: v2 verdicts are telemetry-only (denial_v2_* fields)
# until the fixture + live disagreement-review gates pass.  Enforcement stays
# dormant in both versions.

from tcp.proxy.absence_language import (  # noqa: E402  (section-local import)
    ABSENCE_DETECTOR_VERSION_V2,
    detect_absence_v2,
)


@dataclass(frozen=True)
class DenialV2Decision:
    """v2 verdict for one response: tiered detection + resolution adjudication.

    ``violation`` requires ALL of: a Tier A (assistant-voice, guarded) absence
    claim, an in-surface capability reference, and no valid signed
    unavailable resolution.  Tier B candidates and out-of-surface claims are
    observations, never violations.
    """

    tier_a: bool
    tier_b: bool
    in_surface: bool
    narration_suppressed: bool
    violation: bool
    reason: str
    rewrite_action: str | None
    matched_phrases: tuple[str, ...]
    detector_version: str = ABSENCE_DETECTOR_VERSION_V2


def evaluate_denial_v2(
    text: str,
    resolutions: Sequence[CapabilityResolution],
    surface_tokens: Sequence[str] | None = None,
) -> DenialV2Decision:
    """Run the v2 tiered detector and adjudicate against CRG resolutions.

    Rules (in order):
      1. No Tier A → never a violation (tier_b_candidate / narration /
         no_absence_language).
      2. Tier A out-of-surface (files, hosts, infra) → observed, not a
         violation.
      3. Tier A in-surface + valid signed unavailable resolution → allowed.
      4. Tier A in-surface + no resolutions → violation
         (tier_a_in_surface_no_resolutions).
      5. Tier A in-surface + only reachable statuses → violation with the
         v1 rewrite-action precedence.
    """
    det = detect_absence_v2(text, surface_tokens)
    phrases = det.tier_a_phrases or det.tier_b_phrases

    if not det.tier_a:
        if det.narration_suppressed:
            reason = "narration_suppressed"
        elif det.tier_b:
            reason = "tier_b_candidate"
        else:
            reason = "no_absence_language"
        return DenialV2Decision(
            tier_a=False,
            tier_b=det.tier_b,
            in_surface=False,
            narration_suppressed=det.narration_suppressed,
            violation=False,
            reason=reason,
            rewrite_action=None,
            matched_phrases=phrases,
        )

    if not det.tier_a_in_surface:
        return DenialV2Decision(
            tier_a=True,
            tier_b=det.tier_b,
            in_surface=False,
            narration_suppressed=False,
            violation=False,
            reason="tier_a_out_of_surface",
            rewrite_action=None,
            matched_phrases=phrases,
        )

    if any(resolution_allows_denial(r) for r in resolutions):
        return DenialV2Decision(
            tier_a=True,
            tier_b=det.tier_b,
            in_surface=True,
            narration_suppressed=False,
            violation=False,
            reason="valid_unavailable_resolution",
            rewrite_action=None,
            matched_phrases=phrases,
        )

    if not resolutions:
        return DenialV2Decision(
            tier_a=True,
            tier_b=det.tier_b,
            in_surface=True,
            narration_suppressed=False,
            violation=True,
            reason="tier_a_in_surface_no_resolutions",
            rewrite_action=None,
            matched_phrases=phrases,
        )

    statuses = sorted({r.status for r in resolutions})
    return DenialV2Decision(
        tier_a=True,
        tier_b=det.tier_b,
        in_surface=True,
        narration_suppressed=False,
        violation=True,
        reason="tier_a_with_reachable_status_" + "+".join(statuses),
        rewrite_action=_pick_rewrite_action(resolutions),
        matched_phrases=phrases,
    )
