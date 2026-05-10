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

  1. Never drop ``safety_floor_tools`` from the shortlist when present in
     survivors.
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
  5. If positive evidence exists, emit a shortlist capped at ``max_shortlist``,
     with the safety floor always preserved (its members may push the result
     above the cap rather than displace one another).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping, Sequence


REDUCER_VERSION: str = "imp23.evidence_gated_reducer.v1"

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

# Capability identifier → MCP server family.  Mirrors the table in
# ``tcp.proxy.capability_resolution_gate`` so the reducer can rank survivors by
# CRG match without importing the full CRG module (avoids circular telemetry
# coupling).  Kept narrow on purpose; missing capabilities yield no score.
_CAPABILITY_SERVERS: dict[str, frozenset[str]] = {
    "notion.search": frozenset({"notion-agents", "plugin_Notion_notion"}),
    "notion.read": frozenset({"notion-agents", "plugin_Notion_notion"}),
    "notion.write": frozenset({"notion-agents", "plugin_Notion_notion"}),
    "github.code_search": frozenset({"github"}),
    "github.pr": frozenset({"github"}),
    "calendar.read": frozenset({"bay-view-graph"}),
    "calendar.write": frozenset({"bay-view-graph"}),
    "email.read": frozenset({"bay-view-graph"}),
    "email.send": frozenset({"bay-view-graph"}),
    "email.search": frozenset({"bay-view-graph"}),
    "oracle.query": frozenset({"oracle-remote"}),
    "web.fetch": frozenset({"fetch", "exa"}),
    "web.search": frozenset({"exa", "nixos", "fetch"}),
    "nix.package_search": frozenset({"nixos"}),
    "filesystem.read": frozenset({"filesystem"}),
    "filesystem.write": frozenset({"filesystem"}),
    "git.log": frozenset({"git"}),
    "git.diff": frozenset({"git"}),
    "chatsearch.find": frozenset({"chatsearch"}),
    "writing.rag": frozenset({"writing-rag"}),
    "claude_projects.read": frozenset({"claude-projects"}),
    "context7.docs": frozenset({"context7"}),
}


@dataclass(frozen=True)
class SurvivorReduction:
    """Outcome of one reducer pass.

    Attributes:
        original_count:        Survivor count entering the reducer.
        shortlisted_count:     Length of ``shortlisted_tools``.  When abstaining,
                               this is 0 even though the survivor set is intact.
        shortlisted_tools:     Tools the reducer would propose in shadow mode.
                               Always preserves any safety-floor entries that
                               were in the input survivor set.
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
    if isinstance(raw, str) and raw.upper() in {STATE_ACTIVE, STATE_DEFERRED, STATE_SUPPRESSED}:
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
        servers.update(_CAPABILITY_SERVERS.get(cap, frozenset()))
    return frozenset(servers)


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
    max_shortlist: int = 20,
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
        safety_floor_tools: Tool names guaranteed to remain in the shortlist
            when present in ``survivor_names``.
        max_shortlist: Soft cap on shortlist size.  Safety-floor entries are
            always preserved even if they push the final count above the cap.

    Returns:
        A ``SurvivorReduction`` describing the shortlist, ranking, and
        feature aggregates.  Shadow-only: callers must not use the shortlist
        to mutate ``live_tools`` in this PR.
    """
    if not survivor_names:
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

        # Feature 1: exact tool name or server name appears in the prompt.
        feat_exact_name = False
        if prompt_lc:
            if name and name.lower() in prompt_lc:
                feat_exact_name = True
            elif isinstance(mcp_server, str) and mcp_server and mcp_server.lower() in prompt_lc:
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

        # Feature 4: command/name lexical match — any tool token appears in prompt.
        feat_lexical_name = False
        if prompt_lc:
            for token in _lexical_tokens(name):
                if token in prompt_lc:
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

    # Build the shortlist.  Order: positive-evidence tools by descending score,
    # capped at max_shortlist; then unioned with safety-floor survivors (which
    # may push the total above the cap rather than displace evidence-positive
    # tools).
    capped: list[str] = []
    for _, name, feats in scored:
        if feats["exact_name"] or feats["crg_family"] or feats["lexical_name"]:
            capped.append(name)
            if len(capped) >= max_shortlist:
                break

    shortlist: list[str] = list(capped)
    shortlist_set = set(shortlist)
    for name in sorted(survivor_names & safety_floor_tools):
        if name not in shortlist_set:
            shortlist.append(name)
            shortlist_set.add(name)

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
