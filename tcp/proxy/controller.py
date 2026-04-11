"""ToolPackController — DS-3 deterministic 3-state pack resolution.

Implements the TCP-DS-3 design spec state resolution order:
  1. Policy query        — BANNED  → SUPPRESSED  (hard override)
  2. Manifest baseline   — workspace/profile match → min DEFERRED;
                           default_state=active → ACTIVE; else SUPPRESSED
  3. Heuristic upgrade   — DEFERRED + prompt trigger → ACTIVE
  4. Safety floor        — core-coding pack always ACTIVE

Invariants (DS-3 §5):
  - Visibility floor: any server listed in active_workspaces/active_profiles
    for the current context MUST be ≥ DEFERRED, never SUPPRESSED.
  - Monotonicity: heuristics may upgrade DEFERRED→ACTIVE but MUST NOT
    downgrade ACTIVE/DEFERRED→SUPPRESSED.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from tcp.proxy.pack_manifest import (
    PackContext,
    PackDecision,
    PackManifest,
    PackRule,
    STATE_ACTIVE,
    STATE_DEFERRED,
    STATE_SUPPRESSED,
    PackState,
    _env_matches,
)


# ── TPC rule attribution constants ────────────────────────────────────────────

RULE_POLICY_OVERRIDE = "policy_override"
RULE_MANIFEST_FLOOR = "manifest_floor"
RULE_HEURISTIC_UPGRADE = "heuristic_upgrade"
RULE_SAFETY_FLOOR = "safety_floor"
RULE_DEFAULT = "default"


@dataclass(frozen=True)
class ServerResolution:
    """Resolution result for a single MCP server."""

    server: str
    state: PackState
    pack_id: str | None
    tpc_rule: str
    reasons: tuple[str, ...]


@dataclass
class ControllerResult:
    """Aggregated output from ToolPackController.resolve()."""

    pack_decisions: dict[str, PackDecision]
    server_decisions: dict[str, PackDecision]
    server_resolutions: dict[str, ServerResolution]
    # Map server → which resolution rule produced the final state.
    server_tpc_rules: dict[str, str]


class ToolPackController:
    """Deterministic 3-state pack resolution per DS-3 design spec.

    Usage::

        controller = ToolPackController(manifest, context, policy_banned=set())
        result = controller.resolve(prompt=prompt)
        # result.pack_decisions, result.server_decisions, result.server_tpc_rules
    """

    #: Pack IDs that are unconditionally ACTIVE (safety floor).
    SAFETY_FLOOR_PACK_IDS: frozenset[str] = frozenset({"core-coding"})

    def __init__(
        self,
        manifest: PackManifest,
        context: PackContext,
        *,
        policy_banned: frozenset[str] | None = None,
    ) -> None:
        self._manifest = manifest
        self._context = context
        self._policy_banned: frozenset[str] = policy_banned or frozenset()

    # ── Public API ────────────────────────────────────────────────────────────

    def resolve(
        self,
        *,
        prompt: str = "",
        heuristic_server_predicate: Any = None,
    ) -> ControllerResult:
        """Run the full DS-3 resolution pipeline and return a ControllerResult.

        Args:
            prompt: The task prompt text used for heuristic upgrade (Step 3).
            heuristic_server_predicate: Optional callable(server_name, prompt) → bool
                that overrides the default prompt-mention heuristic.  Useful for
                testing.
        """
        pack_decisions: dict[str, PackDecision] = {}
        server_decisions: dict[str, PackDecision] = {}
        server_resolutions: dict[str, ServerResolution] = {}

        for pack in self._manifest.packs:
            state, reasons, tpc_rule = self._resolve_pack(
                pack,
                prompt=prompt,
                heuristic_server_predicate=heuristic_server_predicate,
            )

            decision = PackDecision(
                pack_id=pack.pack_id,
                state=state,
                reasons=tuple(reasons),
                servers=tuple(sorted(pack.servers)),
            )
            pack_decisions[pack.pack_id] = decision

            for server in pack.servers:
                server_decisions[server] = decision
                server_resolutions[server] = ServerResolution(
                    server=server,
                    state=state,
                    pack_id=pack.pack_id,
                    tpc_rule=tpc_rule,
                    reasons=tuple(reasons),
                )

        server_tpc_rules = {s: r.tpc_rule for s, r in server_resolutions.items()}

        return ControllerResult(
            pack_decisions=pack_decisions,
            server_decisions=server_decisions,
            server_resolutions=server_resolutions,
            server_tpc_rules=server_tpc_rules,
        )

    # ── Per-pack resolution steps (DS-3 §5) ──────────────────────────────────

    def _resolve_pack(
        self,
        pack: PackRule,
        *,
        prompt: str,
        heuristic_server_predicate: Any,
    ) -> tuple[PackState, list[str], str]:
        """Apply all 4 resolution steps and return (state, reasons, tpc_rule)."""

        # Step 1 — Policy override (BANNED → SUPPRESSED, hard override)
        if pack.pack_id in self._policy_banned:
            return STATE_SUPPRESSED, [f"policy_banned:{pack.pack_id}"], RULE_POLICY_OVERRIDE

        # Step 2 — Manifest baseline
        state, reasons = self._manifest_baseline(pack)
        tpc_rule = RULE_MANIFEST_FLOOR if state != pack.default_state else RULE_DEFAULT

        # Step 3 — Heuristic upgrade: DEFERRED + prompt trigger → ACTIVE
        # Monotonicity: only upgrades; never downgrades existing ACTIVE/DEFERRED.
        if state == STATE_DEFERRED:
            if self._heuristic_trigger(pack, prompt=prompt, predicate=heuristic_server_predicate):
                reasons.append("heuristic_upgrade")
                return STATE_ACTIVE, reasons, RULE_HEURISTIC_UPGRADE

        # Step 4 — Safety floor: core-coding pack always ACTIVE
        if pack.pack_id in self.SAFETY_FLOOR_PACK_IDS and state != STATE_ACTIVE:
            reasons.append("safety_floor")
            return STATE_ACTIVE, reasons, RULE_SAFETY_FLOOR

        return state, reasons, tpc_rule

    def _manifest_baseline(
        self,
        pack: PackRule,
    ) -> tuple[PackState, list[str]]:
        """Step 2: Manifest-driven baseline state.

        Visibility floor invariant: if a server appears in active_workspaces or
        active_profiles for the current context it must be ≥ DEFERRED.
        """
        context = self._context
        reasons: list[str] = [f"default:{pack.default_state}"]
        state: PackState = pack.default_state

        # Workspace name or path match → ACTIVE
        workspace_match = (
            context.workspace_name in pack.active_workspaces
            or context.workspace_path in pack.active_workspaces
        )
        if workspace_match:
            state = STATE_ACTIVE
            reasons.append(f"workspace:{context.workspace_name}")

        # Profile match → ACTIVE
        if context.profile in pack.active_profiles:
            state = STATE_ACTIVE
            reasons.append(f"profile:{context.profile}")

        # Environment variable match → ACTIVE
        matched_env: list[str] = []
        for key, expected in pack.active_env.items():
            raw = context.env.get(key)
            if _env_matches(expected, raw):
                matched_env.append(f"{key}={raw}")
        if matched_env:
            state = STATE_ACTIVE
            reasons.extend(f"env:{item}" for item in matched_env)

        # Workspace allow-listed servers → minimum DEFERRED (visibility floor)
        if (
            state != STATE_ACTIVE
            and pack.allow_workspace
            and (pack.servers & context.workspace_allowed_servers)
        ):
            state = STATE_DEFERRED
            reasons.append("workspace_allow")

        return state, reasons

    def _heuristic_trigger(
        self,
        pack: PackRule,
        *,
        prompt: str,
        predicate: Any,
    ) -> bool:
        """Return True if any server in this pack is triggered by the prompt."""
        if predicate is not None:
            return any(predicate(server, prompt) for server in pack.servers)
        # Default: check if any server name token appears in the prompt
        prompt_l = prompt.lower()
        for server in pack.servers:
            if _server_mentioned(server, prompt_l):
                return True
        return False


# ── Standalone schema materialization helper (moved from cc_proxy.py) ─────────


def schema_materialization_state(
    tool_name: str,
    *,
    allowed_servers: frozenset[str],
    server_pack_decisions: Mapping[str, Any],
    server_allow_source: Mapping[str, str],
) -> PackState:
    """Classify whether a visible tool should keep full schema or deferred schema.

    Replaces ``_schema_materialization_state`` in cc_proxy.py.  Logic is identical
    to the original; it now lives here so cc_proxy.py stays free of state logic.
    """
    server = _extract_mcp_server(tool_name)
    if server is None:
        return STATE_ACTIVE

    pack_decision = server_pack_decisions.get(server)
    allow_source = server_allow_source.get(server)

    if pack_decision is not None:
        if pack_decision.state != STATE_ACTIVE and allow_source in {
            "workspace_allow",
            "explicit_request",
        }:
            return STATE_DEFERRED
        return pack_decision.state

    if server in allowed_servers:
        return STATE_ACTIVE

    if allow_source in {"workspace_allow", "explicit_request"}:
        return STATE_DEFERRED
    return STATE_SUPPRESSED


# ── Private helpers ───────────────────────────────────────────────────────────


def _extract_mcp_server(tool_name: str) -> str | None:
    """Return the MCP server segment of an ``mcp__<server>__<tool>`` name."""
    if not tool_name.startswith("mcp__"):
        return None
    parts = tool_name.split("__")
    if len(parts) < 2:
        return None
    server = parts[1].strip()
    return server or None


def _server_mentioned(server: str, prompt_l: str) -> bool:
    """True if the server name or any of its human-readable aliases appears in prompt."""
    server_l = server.lower()
    if server_l in prompt_l:
        return True
    # Check hyphen/underscore/colon variants
    for sep in ("-", "_", ":"):
        variant = server_l.replace(sep, " ")
        if variant != server_l and variant in prompt_l:
            return True
    return False
