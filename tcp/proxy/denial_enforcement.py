"""Denial enforcement for the Capability Resolution Gate — CRG Phase 2.

Implements the refusal gate: a capability denial is only permitted when the
resolver has returned a signed CapabilityResolution{status=unavailable}.

May-emit rule:
  A response containing absence-language is valid only if:
    1. At least one attached resolution has status=unavailable, AND
    2. That resolution carries a valid signature, AND
    3. That resolution lists all six required surfaces.

Any other combination is a denial_violation.
"""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from typing import Sequence

from tcp.proxy.absence_language import (
    contains_absence_language,
    extract_absence_phrases,
)
from tcp.proxy.capability_resolution_gate import (
    CapabilityResolution,
    _REQUIRED_SIX_SURFACES,
    _CRG_RESOLVER_SECRET,
    _compute_signature,
)


@dataclass(frozen=True)
class DenialGateDecision:
    """Result of running a response through the refusal gate."""
    allowed: bool
    # Populated only when allowed=False.
    violation_kind: str | None              # "denial_violation"
    matched_phrases: tuple[str, ...]        # absence phrases that triggered
    attached_resolution_status: str | None  # status of the attached resolution (if any)
    resolution_signature_valid: bool | None # None = no resolution attached
    rewrite_action: str | None              # suggested rewrite target


def _resolution_signature_valid(resolution: CapabilityResolution) -> bool:
    """Return True if the resolution's signature field is correct."""
    if not resolution.signature:
        return False
    expected = _compute_signature(
        resolver_id=resolution.resolver_id,
        requested_capability=resolution.requested_capability,
        status=resolution.status,
        matched_tools=resolution.matched_tools,
    )
    return hmac.compare_digest(resolution.signature, expected)


def _has_valid_unavailable(resolutions: Sequence[CapabilityResolution]) -> bool:
    """Return True if any resolution is a properly signed unavailable result."""
    for r in resolutions:
        if r.status != "unavailable":
            continue
        if not _resolution_signature_valid(r):
            continue
        if set(r.checked_surfaces) != set(_REQUIRED_SIX_SURFACES):
            continue
        return True
    return False


def _rewrite_action_for(resolutions: Sequence[CapabilityResolution]) -> str:
    """Suggest the appropriate rewrite action given the attached resolutions."""
    for r in resolutions:
        if r.status == "callable_now":
            return "invoke_resolved_tool"
        if r.status == "schema_deferred":
            return "hydrate_or_project_resolved_tool"
        if r.status == "approval_required":
            return "request_approval"
        if r.status == "policy_blocked":
            return "cite_policy_and_explain"
    return "re_resolve_capability"


def enforce_denial_gate(
    text: str,
    resolutions: Sequence[CapabilityResolution],
) -> DenialGateDecision:
    """Check whether a response text may include capability-absence language.

    Returns a DenialGateDecision.  ``allowed=True`` means the denial is
    justified.  ``allowed=False`` is a denial_violation.
    """
    if not contains_absence_language(text):
        return DenialGateDecision(
            allowed=True,
            violation_kind=None,
            matched_phrases=(),
            attached_resolution_status=None,
            resolution_signature_valid=None,
            rewrite_action=None,
        )

    phrases = tuple(extract_absence_phrases(text))

    # No resolutions attached at all — automatic violation.
    if not resolutions:
        return DenialGateDecision(
            allowed=False,
            violation_kind="denial_violation",
            matched_phrases=phrases,
            attached_resolution_status=None,
            resolution_signature_valid=None,
            rewrite_action="re_resolve_capability",
        )

    # Check if a valid signed unavailable resolution backs this denial.
    if _has_valid_unavailable(resolutions):
        return DenialGateDecision(
            allowed=True,
            violation_kind=None,
            matched_phrases=phrases,
            attached_resolution_status="unavailable",
            resolution_signature_valid=True,
            rewrite_action=None,
        )

    # Denial present but no valid unavailable resolution — violation.
    first = resolutions[0]
    sig_valid = _resolution_signature_valid(first) if first.signature else False
    return DenialGateDecision(
        allowed=False,
        violation_kind="denial_violation",
        matched_phrases=phrases,
        attached_resolution_status=first.status,
        resolution_signature_valid=sig_valid,
        rewrite_action=_rewrite_action_for(resolutions),
    )


def denial_violation_record(
    decision: DenialGateDecision,
    model_text_excerpt: str,
    requested_capability: str | None = None,
) -> dict:
    """Serialize a denial_violation to a decisions.jsonl record shape."""
    return {
        "kind": "denial_violation",
        "model_text_excerpt": model_text_excerpt[:300],
        "matched_phrases": list(decision.matched_phrases),
        "attached_resolution_status": decision.attached_resolution_status,
        "resolution_signature_valid": decision.resolution_signature_valid,
        "rewrite_action": decision.rewrite_action,
        "requested_capability": requested_capability,
    }
