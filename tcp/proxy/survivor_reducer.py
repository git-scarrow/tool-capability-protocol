"""Evidence-gated Stage 4.5 survivor reducer (TCP-IMP-23, shadow-only).

Post-IMP-22 analysis showed that Stage 1-4 of the cc_proxy gating pipeline never
narrows ``active_survivors`` to a single tool: the modal operating point is 196
survivors and the maximum observed is 336.  IMP-22 correctly abstains from
emitting ``expected_tool_name`` in that regime, but the proxy lacks any bounded
ranking + shortlisting step that records *which* of those survivors are most
plausibly relevant to the request.

This module supplies that step.  ``reduce_survivors`` ranks ``active_survivors``
using transparent, evidence-bearing features and produces a capped shortlist.
It is **shadow-only** in this PR: callers must not use the shortlist to mutate
the live tool list sent upstream.  The shortlist is recorded in decisions.jsonl
for offline replay against the IMP-23 acceptance metrics.

Design constraints (from spec):

  1. Safety-floor tools are never *demotable* (see ``demotion_candidates``),
     independent of shortlist membership.  As of reducer v2 the shortlist is
     evidence-only: floor tools appear in it only when they earn evidence,
     because floor protection lives at the demotion layer, not in the
     shortlist.  (v1 unioned the ~36-tool floor into every shortlist, which
     inflated the size metric without adding any protection.)
  2. Never hard-prune solely on ``heuristic_capability_flags`` — those flags
     contribute to *ranking* but cannot, on their own, determine emit/abstain.
  3. Score with multiple transparent features:
        - exact tool/server name mention in prompt
        - CRG capability family match
        - heuristic_capability_flags overlap with the tool's flags
        - command/name lexical match
        - pack state ACTIVE > DEFERRED > SUPPRESSED
  4. If no positive evidence exists, abstain — do not produce a misleading
     shortlist.  The original survivor count is preserved in telemetry.
  5. If positive evidence exists, emit a shortlist capped at ``max_shortlist``
     (default 15, the Gate 2 median target).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping, Sequence

# Reducer ranking reuses CRG's canonical capability → MCP server mapping so the
# two modules cannot drift.  ``capability_resolution_gate`` does not import
# anything from this module, so the dependency is acyclic.
from tcp.proxy.capability_resolution_gate import (
    _CAPABILITY_SERVERS as CAPABILITY_SERVERS,
)

# v2 (Gate 2 tightening): the shortlist is the evidence-ranked prefix only —
# the safety floor is no longer unioned in (its protection is enforced in
# demotion_candidates) and the default cap drops 20 → 15.
REDUCER_VERSION: str = "imp24.evidence_gated_reducer.v2"

# TCP-IMP-24: version string for the demotion-enforcement policy layered on top
# of the (unchanged) ranking algorithm above.  Logged so replay tooling can
# distinguish enforcement policies without re-deriving them from row shapes.
# v2: demotion candidates additionally exclude tools on recency-shielded MCP
# servers (servers observed called in the same workspace within the TTL).
ENFORCEMENT_VERSION: str = "imp24.demote.v2"

# Transparent integer score weights.  Kept as module-level constants so a future
# spec can tune them with explicit traceability rather than buried magic numbers.
SCORE_EXACT_NAME: int = 10
SCORE_CRG_FAMILY: int = 6
SCORE_LEXICAL_NAME: int = 3
SCORE_HEURISTIC_OVERLAP_PER_BIT: int = 2
SCORE_HEURISTIC_OVERLAP_CAP: int = 6
SCORE_STATE_ACTIVE: int = 2
SCORE_STATE_DEFERRED: int = 1
SCORE_STATE_SUPPRESSED: int = 0

STATE_ACTIVE = "ACTIVE"
STATE_DEFERRED = "DEFERRED"
STATE_SUPPRESSED = "SUPPRESSED"

ABSTAIN_NO_SURVIVORS = "no_survivors"
ABSTAIN_NO_POSITIVE_EVIDENCE = "no_positive_evidence"


@dataclass(frozen=True)
class SurvivorReduction:
    """Outcome of one reducer pass.

    Attributes:
        original_count:        Survivor count entering the reducer.
        shortlisted_count:     Length of ``shortlisted_tools``.  When abstaining,
                               this is 0 even though the survivor set is intact.
        shortlisted_tools:     Tools the reducer would propose in shadow mode.
                               Evidence-ranked prefix only (v2); safety-floor
                               protection is enforced by demotion_candidates,
                               not by shortlist membership.
        ranked_tools:          All survivors ordered by descending score.  Lets
                               replay scripts inspect ranking without needing
                               feature reconstruction.
        abstained:             True when no survivor met positive-evidence
                               criteria.  When True, ``shortlisted_tools`` is
                               empty and the proxy must record the original
                               survivor set in the existing
                               ``survivor_names_sorted`` field for downstream
                               analysis.
        abstain_reason:        Stable string identifier for the abstention.
        reducer_version:       Algorithm version string written into telemetry.
        feature_summary:       Aggregate signal counts for offline diagnosis.
    """

    original_count: int
    shortlisted_count: int
    shortlisted_tools: tuple[str, ...]
    ranked_tools: tuple[str, ...]
    abstained: bool
    abstain_reason: str | None
    reducer_version: str
    feature_summary: dict[str, object] = field(default_factory=dict)


# ── Helpers ────────────────────────────────────────────────────────────────────


_TOKEN_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")
_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _extract_mcp_server(tool_name: str) -> str | None:
    if not tool_name.startswith("mcp__"):
        return None
    parts = tool_name.split("__")
    if len(parts) < 2:
        return None
    server = parts[1].strip()
    return server or None


def _lexical_tokens(tool_name: str) -> frozenset[str]:
    """Return lowercased tokens that lexically identify the tool.

    For built-ins, splits CamelCase and snake_case into individual tokens.
    For MCP tools, includes the server name and the action portion's tokens.
    All tokens are at least 3 characters long; shorter ones make the lexical
    score noisy on common words like ``rm`` or ``ls``.
    """
    if not tool_name:
        return frozenset()
    raw_pieces: list[str] = []
    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__")
        if len(parts) >= 2:
            raw_pieces.append(parts[1])  # server
        if len(parts) >= 3:
            raw_pieces.extend(parts[2:])  # action segments
    else:
        raw_pieces.append(tool_name)
    tokens: set[str] = set()
    for piece in raw_pieces:
        for chunk in _TOKEN_SPLIT_RE.split(piece):
            if not chunk:
                continue
            for sub in _CAMEL_SPLIT_RE.split(chunk):
                if len(sub) >= 3:
                    tokens.add(sub.lower())
    return frozenset(tokens)


def _popcount(value: int) -> int:
    return bin(value & 0xFFFFFFFFFFFFFFFF).count("1") if value else 0


def _coerce_state(raw: object) -> str:
    if isinstance(raw, str) and raw.upper() in {
        STATE_ACTIVE,
        STATE_DEFERRED,
        STATE_SUPPRESSED,
    }:
        return raw.upper()
    return STATE_ACTIVE


def _state_score(state: str) -> int:
    if state == STATE_ACTIVE:
        return SCORE_STATE_ACTIVE
    if state == STATE_DEFERRED:
        return SCORE_STATE_DEFERRED
    return SCORE_STATE_SUPPRESSED


def _crg_servers_for_capabilities(capabilities: Sequence[str]) -> frozenset[str]:
    servers: set[str] = set()
    for cap in capabilities:
        servers.update(CAPABILITY_SERVERS.get(cap, frozenset()))
    return frozenset(servers)


def _word_boundary_present(needle: str, prompt_lc: str) -> bool:
    """Return True iff ``needle`` (lowercased) appears in ``prompt_lc`` bounded
    by non-alphanumeric characters on both sides.

    Substring containment alone over-fires: the prompt ``"explain digital
    signatures"`` would match an MCP server named ``git`` inside ``digital``,
    producing a false positive-evidence verdict.  Word-boundary matching keeps
    the exact-name feature true to its name.
    """
    if not needle or not prompt_lc:
        return False
    needle_lc = needle.lower()
    pattern = re.compile(
        r"(?:^|[^A-Za-z0-9])" + re.escape(needle_lc) + r"(?:[^A-Za-z0-9]|$)"
    )
    return bool(pattern.search(prompt_lc))


# ── Public API ─────────────────────────────────────────────────────────────────


def reduce_survivors(
    prompt: str,
    survivor_names: frozenset[str],
    tool_surface_by_name: Mapping[str, Mapping[str, object]],
    required_capability_flags: int,
    hard_capability_flags: int,
    heuristic_capability_flags: int,
    crg_requested_capabilities: Sequence[str],
    safety_floor_tools: frozenset[str],
    max_shortlist: int = 15,
) -> SurvivorReduction:
    """Rank and shortlist surviving tools using transparent feature scoring.

    The reducer is deterministic and pure: same inputs always produce the same
    output.  See module docstring for invariants.

    Parameters:
        prompt: The extracted task prompt.  May be empty; when empty, the
            lexical/exact-name features cannot fire and the reducer typically
            abstains unless CRG capabilities supply positive evidence.
        survivor_names: Tools that passed Stages 1-4 (post-safety-floor).
        tool_surface_by_name: Per-tool metadata.  Each value may include
            ``description`` (str), ``capability_flags`` (int),
            ``surface_state`` (one of ACTIVE/DEFERRED/SUPPRESSED), and
            ``mcp_server`` (str|None).  Missing keys default to safe values.
        required_capability_flags: Aggregate capability flags the prompt
            implies (heuristic + hard).  Used for telemetry only.
        hard_capability_flags: Environment-derived capability flags.  Used for
            telemetry only.
        heuristic_capability_flags: Prompt-derived capability flags.  Allowed to
            *rank* survivors (overlap adds to score) but cannot, on its own,
            determine emit/abstain — see invariant 2.
        crg_requested_capabilities: Capability identifiers extracted from the
            prompt by ``capability_resolution_gate.extract_requested_capabilities``
            (or the equivalent).  When a survivor's MCP server is in any of the
            corresponding capability families, that survivor scores positive
            evidence.
        safety_floor_tools: Tool names that are never demotable.  Used here for
            telemetry (``safety_floor_preserved``); the actual protection is
            enforced in ``demotion_candidates``, not by shortlist membership.
        max_shortlist: Hard cap on shortlist size.  The shortlist is exactly
            the evidence-ranked prefix, length <= max_shortlist.

    Returns:
        A ``SurvivorReduction`` describing the shortlist, ranking, and
        feature aggregates.  Shadow-only: callers must not use the shortlist
        to mutate ``live_tools`` in this PR.
    """
    if not survivor_names:
        # Same key set as the populated path so downstream telemetry consumers
        # see a stable schema regardless of abstain reason.
        return SurvivorReduction(
            original_count=0,
            shortlisted_count=0,
            shortlisted_tools=(),
            ranked_tools=(),
            abstained=True,
            abstain_reason=ABSTAIN_NO_SURVIVORS,
            reducer_version=REDUCER_VERSION,
            feature_summary={
                "exact_name_matches": 0,
                "crg_family_matches": 0,
                "lexical_name_matches": 0,
                "heuristic_flag_matches": 0,
                "state_active": 0,
                "state_deferred": 0,
                "state_suppressed": 0,
                "safety_floor_preserved": 0,
                "positive_evidence_tools": 0,
                "crg_capabilities": list(crg_requested_capabilities),
                "heuristic_capability_flags": int(heuristic_capability_flags),
                "hard_capability_flags": int(hard_capability_flags),
                "required_capability_flags": int(required_capability_flags),
                "max_shortlist": int(max_shortlist),
            },
        )

    prompt_lc = (prompt or "").lower()
    crg_servers = _crg_servers_for_capabilities(crg_requested_capabilities)

    # Per-tool feature evaluation.  Sorted survivors so iteration order — and
    # therefore the tie-breaking for equally scored tools — is deterministic.
    sorted_survivors = sorted(survivor_names)

    scored: list[tuple[int, str, dict[str, bool]]] = []
    aggregates = {
        "exact_name_matches": 0,
        "crg_family_matches": 0,
        "lexical_name_matches": 0,
        "heuristic_flag_matches": 0,
        "state_active": 0,
        "state_deferred": 0,
        "state_suppressed": 0,
    }

    for name in sorted_survivors:
        surface = tool_surface_by_name.get(name) or {}
        capability_flags_raw = surface.get("capability_flags", 0)
        try:
            tool_flags = int(capability_flags_raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            tool_flags = 0
        state = _coerce_state(surface.get("surface_state"))
        mcp_server = surface.get("mcp_server")
        if not isinstance(mcp_server, str) or not mcp_server:
            mcp_server = _extract_mcp_server(name)

        # Feature 1: exact tool name or server name appears in the prompt,
        # bounded by non-alphanumeric characters so incidental substrings
        # (e.g. "git" inside "digital") do not register as evidence.
        feat_exact_name = False
        if prompt_lc:
            if name and _word_boundary_present(name, prompt_lc):
                feat_exact_name = True
            elif (
                isinstance(mcp_server, str)
                and mcp_server
                and _word_boundary_present(mcp_server, prompt_lc)
            ):
                feat_exact_name = True

        # Feature 2: CRG capability family match by MCP server.
        feat_crg_family = bool(
            isinstance(mcp_server, str) and mcp_server and mcp_server in crg_servers
        )

        # Feature 3: heuristic capability flags overlap.
        overlap_bits = _popcount(tool_flags & heuristic_capability_flags)
        feat_heuristic = overlap_bits > 0
        heuristic_score = min(
            overlap_bits * SCORE_HEURISTIC_OVERLAP_PER_BIT,
            SCORE_HEURISTIC_OVERLAP_CAP,
        )

        # Feature 4: command/name lexical match — any tool token appears in the
        # prompt as a whole word.  Word-boundary checking keeps short tokens
        # like ``git`` from matching inside ``digital``.
        feat_lexical_name = False
        if prompt_lc:
            for token in _lexical_tokens(name):
                if _word_boundary_present(token, prompt_lc):
                    feat_lexical_name = True
                    break

        # Feature 5: pack state preference.
        state_score = _state_score(state)

        score = (
            (SCORE_EXACT_NAME if feat_exact_name else 0)
            + (SCORE_CRG_FAMILY if feat_crg_family else 0)
            + (SCORE_LEXICAL_NAME if feat_lexical_name else 0)
            + heuristic_score
            + state_score
        )

        if feat_exact_name:
            aggregates["exact_name_matches"] += 1
        if feat_crg_family:
            aggregates["crg_family_matches"] += 1
        if feat_lexical_name:
            aggregates["lexical_name_matches"] += 1
        if feat_heuristic:
            aggregates["heuristic_flag_matches"] += 1
        if state == STATE_ACTIVE:
            aggregates["state_active"] += 1
        elif state == STATE_DEFERRED:
            aggregates["state_deferred"] += 1
        else:
            aggregates["state_suppressed"] += 1

        scored.append(
            (
                score,
                name,
                {
                    "exact_name": feat_exact_name,
                    "crg_family": feat_crg_family,
                    "lexical_name": feat_lexical_name,
                    "heuristic_flags": feat_heuristic,
                },
            )
        )

    # Ranked ordering: descending score, then ascending name (stable).
    scored.sort(key=lambda item: (-item[0], item[1]))
    ranked_tools = tuple(name for _, name, _ in scored)

    # Positive-evidence detection.  Heuristic flags alone do NOT count.
    positive_evidence_tools = [
        name
        for _, name, feats in scored
        if feats["exact_name"] or feats["crg_family"] or feats["lexical_name"]
    ]

    aggregates["safety_floor_preserved"] = sum(
        1 for name in survivor_names if name in safety_floor_tools
    )
    aggregates["positive_evidence_tools"] = len(positive_evidence_tools)

    feature_summary: dict[str, object] = dict(aggregates)
    feature_summary["crg_capabilities"] = list(crg_requested_capabilities)
    feature_summary["heuristic_capability_flags"] = int(heuristic_capability_flags)
    feature_summary["hard_capability_flags"] = int(hard_capability_flags)
    feature_summary["required_capability_flags"] = int(required_capability_flags)
    feature_summary["max_shortlist"] = int(max_shortlist)

    if not positive_evidence_tools:
        # No survivor cleared the positive-evidence bar.  Abstain so callers do
        # not assemble a misleading shortlist.  Telemetry preserves the original
        # set via the proxy's existing ``survivor_names_sorted`` field.
        return SurvivorReduction(
            original_count=len(survivor_names),
            shortlisted_count=0,
            shortlisted_tools=(),
            ranked_tools=ranked_tools,
            abstained=True,
            abstain_reason=ABSTAIN_NO_POSITIVE_EVIDENCE,
            reducer_version=REDUCER_VERSION,
            feature_summary=feature_summary,
        )

    # Build the shortlist: positive-evidence tools by descending score, capped
    # at max_shortlist.  v2 deliberately does NOT union the safety floor in:
    # replay over 45k logged decisions showed the union added zero protection
    # (identical called-tool-demoted rate with or without it) while inflating
    # the median size metric — floor protection lives in demotion_candidates.
    shortlist: list[str] = []
    for _, name, feats in scored:
        if feats["exact_name"] or feats["crg_family"] or feats["lexical_name"]:
            shortlist.append(name)
            if len(shortlist) >= max_shortlist:
                break

    return SurvivorReduction(
        original_count=len(survivor_names),
        shortlisted_count=len(shortlist),
        shortlisted_tools=tuple(shortlist),
        ranked_tools=ranked_tools,
        abstained=False,
        abstain_reason=None,
        reducer_version=REDUCER_VERSION,
        feature_summary=feature_summary,
    )


def demotion_candidates(
    reduction: SurvivorReduction,
    survivor_names: frozenset[str],
    tool_surface_by_name: Mapping[str, Mapping[str, object]],
    safety_floor_tools: frozenset[str],
    recent_mcp_servers: frozenset[str] = frozenset(),
) -> frozenset[str]:
    """Survivors eligible for deferred-schema demotion under TCP-IMP-24.

    Demotion is the only enforcement the reducer is allowed to drive: a demoted
    tool keeps its name in the model-visible tool list but is sent with the
    minimal deferred-schema surface instead of its materialized schema.  This
    function never proposes removal.

    ``recent_mcp_servers`` is the enforcement-v2 recency shield: MCP servers
    observed handling a tool call in the same workspace within the recency TTL
    (see cc_proxy._recent_servers_for_workspace).  Tools on a shielded server
    are excluded from demotion because in-flight tasks routinely re-call
    tools that the *current* prompt no longer names.  The shield only narrows
    the candidate set — it never adds tools to the shortlist, so the shortlist
    size metric stays an honest measure of prompt-evidence selectivity.  The
    set used is logged per decision row (reducer_recent_servers) so replay can
    reproduce it exactly.

    Invariants (each pinned by a test in test_reducer_enforcement.py):
      - Abstained reduction → empty set (enforcement is a strict no-op).
      - Safety-floor tools are never candidates, independent of scoring.
      - Non-MCP built-ins are never candidates (deferral is an MCP-surface
        mechanism; built-ins have no hydration path).
      - Tools whose surface is already DEFERRED/SUPPRESSED are never
        candidates (their schema is already minimal; re-deferring would
        clobber the pack-state attribution in the audit log).
      - Shortlisted tools are never candidates.
      - Tools on a recency-shielded MCP server are never candidates.
    """
    if reduction.abstained:
        return frozenset()
    shortlisted = set(reduction.shortlisted_tools)
    out: set[str] = set()
    for name in survivor_names:
        if name in shortlisted or name in safety_floor_tools:
            continue
        surface = tool_surface_by_name.get(name) or {}
        server = surface.get("mcp_server")
        if not isinstance(server, str) or not server:
            server = _extract_mcp_server(name)
        if server is None:
            continue
        if server in recent_mcp_servers:
            continue
        if _coerce_state(surface.get("surface_state")) != STATE_ACTIVE:
            continue
        out.add(name)
    return frozenset(out)
