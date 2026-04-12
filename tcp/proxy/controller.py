"""ToolPackController: deterministic per-server state resolution per TCP-DS-3.

Implements the 4-step resolution order (highest priority first):
  1. Safety floor  — core-coding pack always ACTIVE; cannot be overridden by policy
  2. Policy        — hard_allow_override suppresses non-allowed servers unless
                     the visibility floor protects them
  3. Manifest      — workspace/profile/env match → ACTIVE; workspace_allow → DEFERRED;
                     default_state otherwise
  4. Heuristic     — prompt mention rescues SUPPRESSED → DEFERRED (never downgrades)

Invariants (DS-3 §4.2):
  Visibility floor: any server listed in active_workspaces / active_profiles for the
  current context must be at least DEFERRED; policy cannot suppress it.

  Monotonicity: heuristics may upgrade DEFERRED → ACTIVE but MUST NOT downgrade
  ACTIVE/DEFERRED → SUPPRESSED.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from tcp.proxy.pack_manifest import (
    PackContext,
    PackDecision,
    PackManifest,
    PackState,
    STATE_ACTIVE,
    STATE_DEFERRED,
    STATE_SUPPRESSED,
    resolve_pack_decisions,
)

# TPC rule attribution labels (DS-3 §6.4)
TPC_RULE_SAFETY_FLOOR = "safety_floor"
TPC_RULE_POLICY_OVERRIDE = "policy_override"
TPC_RULE_MANIFEST_FLOOR = "manifest_floor"
TPC_RULE_HEURISTIC_UPGRADE = "heuristic_upgrade"
TPC_RULE_DEFAULT = "default"

# Pack that is always ACTIVE and cannot be suppressed by policy
_SAFETY_FLOOR_PACK_ID = "core-coding"


@dataclass(frozen=True)
class ControllerDecision:
    """Per-server state decision produced by ToolPackController."""

    server: str
    pack_id: str | None
    state: PackState
    tpc_rule: str
    reasons: tuple[str, ...]
    legacy_allow_source: str  # backward-compat value for decisions.jsonl server_allow_source


@dataclass(frozen=True)
class ControllerResult:
    """Result of a bulk ToolPackController resolution pass."""

    server_decisions: dict[str, ControllerDecision]
    pack_decisions: dict[str, PackDecision]  # preserved for telemetry


# ── Prompt-matching helpers ────────────────────────────────────────────────────


def _server_alias_tokens(server_l: str) -> set[str]:
    """Generate human-typed aliases for wrapper-prefixed MCP server names."""
    tokens = {
        server_l,
        server_l.replace("-", " "),
        server_l.replace("_", " "),
        server_l.replace(":", " "),
    }
    parts = tuple(part for part in re.split(r"[^a-z0-9]+", server_l) if part)
    unique_parts = tuple(dict.fromkeys(parts))
    if len(unique_parts) >= 2:
        for idx in range(len(unique_parts) - 1):
            pair = " ".join(unique_parts[idx : idx + 2])
            tokens.add(pair)
            tokens.add(" ".join(reversed(unique_parts[idx : idx + 2])))
        tokens.add(" ".join(unique_parts))

    wrapper_parts = {"plugin", "claude", "ai", "mcp"}
    informative_parts = tuple(
        part for part in unique_parts if part not in wrapper_parts
    )
    if informative_parts:
        tokens.add(" ".join(informative_parts))
        if len(informative_parts) == 1:
            informative = informative_parts[0]
            tokens.add(informative)
            if "plugin" in unique_parts:
                tokens.add(f"{informative} plugin")
                tokens.add(f"plugin {informative}")
                # Claude often refers to plugin-backed Notion access as
                # "notion api"/"notionApi" rather than the raw plugin server id.
                tokens.add(f"{informative} api")
                tokens.add(f"{informative}api")
            if "claude" in unique_parts:
                tokens.add(f"claude {informative}")
                tokens.add(f"{informative} claude")
        elif len(informative_parts) >= 2:
            for idx in range(len(informative_parts) - 1):
                phrase = " ".join(informative_parts[idx : idx + 2])
                tokens.add(phrase)
    return {token for token in tokens if token}


def _prompt_mentions_server_name(prompt: str, server: str) -> bool:
    """Return True if *prompt* contains *server* or a recognised alias."""
    if not prompt:
        return False
    prompt_l = prompt.lower()
    server_l = server.lower()
    if server_l and server_l in prompt_l:
        return True
    return any(token in prompt_l for token in _server_alias_tokens(server_l))


# ── Controller ─────────────────────────────────────────────────────────────────


class ToolPackController:
    """Deterministic MCP-server state resolver (TCP-DS-3).

    Resolves every visible server to ACTIVE | DEFERRED | SUPPRESSED following
    the 4-step priority chain defined in DS-3 §5.  Each decision carries a
    ``tpc_rule`` label recording which step determined the final state.
    """

    def __init__(
        self,
        manifest: PackManifest,
        context: PackContext,
        *,
        allowed_servers: frozenset[str],
        hard_allow_override: bool,
    ) -> None:
        self._manifest = manifest
        self._context = context
        self._allowed_servers = allowed_servers
        self._hard_allow_override = hard_allow_override
        _pack, _server = resolve_pack_decisions(manifest, context)
        self._pack_decisions: dict[str, PackDecision] = _pack
        self._server_pack_decisions: Mapping[str, PackDecision] = _server

    @property
    def pack_decisions(self) -> dict[str, PackDecision]:
        """Manifest-derived pack decisions (backward compat for telemetry)."""
        return self._pack_decisions

    def server_state(self, server: str, *, prompt: str = "") -> ControllerDecision:
        """Resolve state for *server* using the DS-3 4-step priority chain.

        Resolution order (highest priority first):

        1. Safety floor — core-coding pack: always ACTIVE, cannot be overridden.
        2. Policy       — hard_allow_override suppresses non-allowed servers,
                          UNLESS the visibility floor protects them.
        3. Manifest     — workspace/profile/env → ACTIVE; workspace_allow → DEFERRED;
                          default_state drives the remaining cases.
        4. Heuristic    — prompt mention rescues SUPPRESSED → DEFERRED.
                          Never downgrades (monotonicity).
        """
        pack_decision = self._server_pack_decisions.get(server)

        # ── Step 1: Safety floor (absolute — overrides policy) ───────────────
        if pack_decision is not None and pack_decision.pack_id == _SAFETY_FLOOR_PACK_ID:
            return ControllerDecision(
                server=server,
                pack_id=pack_decision.pack_id,
                state=STATE_ACTIVE,
                tpc_rule=TPC_RULE_SAFETY_FLOOR,
                reasons=pack_decision.reasons,
                legacy_allow_source="hard_allow",
            )

        # Determine manifest-derived state and whether the visibility floor applies.
        # Visibility floor: workspace/profile/env match → server can never be SUPPRESSED.
        if pack_decision is not None:
            manifest_state = pack_decision.state
            manifest_reasons = pack_decision.reasons
            has_visibility_floor = any(
                r.startswith(("workspace:", "profile:", "env:"))
                for r in manifest_reasons
            ) or "workspace_allow" in manifest_reasons
        elif server in self._allowed_servers:
            manifest_state = STATE_ACTIVE
            manifest_reasons = ("hard_allow",)
            has_visibility_floor = False
        else:
            manifest_state = STATE_SUPPRESSED
            manifest_reasons = ()
            has_visibility_floor = False

        # ── Step 2: Policy (hard_allow_override) ─────────────────────────────
        if self._hard_allow_override and server not in self._allowed_servers:
            if has_visibility_floor:
                # Visibility floor wins: policy cannot suppress a workspace-listed server.
                return ControllerDecision(
                    server=server,
                    pack_id=pack_decision.pack_id if pack_decision else None,
                    state=STATE_DEFERRED,
                    tpc_rule=TPC_RULE_MANIFEST_FLOOR,
                    reasons=manifest_reasons,
                    legacy_allow_source="workspace_allow",
                )
            return ControllerDecision(
                server=server,
                pack_id=pack_decision.pack_id if pack_decision else None,
                state=STATE_SUPPRESSED,
                tpc_rule=TPC_RULE_POLICY_OVERRIDE,
                reasons=("hard_allow_override",),
                legacy_allow_source="",
            )

        # ── Step 3: Manifest state ────────────────────────────────────────────
        state = manifest_state

        if has_visibility_floor:
            tpc_rule = TPC_RULE_MANIFEST_FLOOR
            legacy_allow_source = (
                "workspace_allow" if state == STATE_DEFERRED else "pack_active"
            )
        elif state == STATE_ACTIVE and server in self._allowed_servers:
            tpc_rule = TPC_RULE_DEFAULT
            legacy_allow_source = "hard_allow"
        elif state == STATE_ACTIVE:
            tpc_rule = TPC_RULE_DEFAULT
            legacy_allow_source = "pack_active"
        else:
            tpc_rule = TPC_RULE_DEFAULT
            legacy_allow_source = ""

        # ── Step 4: Heuristic (prompt rescue, monotonicity enforced) ─────────
        # Only SUPPRESSED → DEFERRED; ACTIVE/DEFERRED are never downgraded.
        if state == STATE_SUPPRESSED and _prompt_mentions_server_name(prompt, server):
            state = STATE_DEFERRED
            tpc_rule = TPC_RULE_HEURISTIC_UPGRADE
            legacy_allow_source = "explicit_request"

        return ControllerDecision(
            server=server,
            pack_id=pack_decision.pack_id if pack_decision else None,
            state=state,
            tpc_rule=tpc_rule,
            reasons=manifest_reasons,
            legacy_allow_source=legacy_allow_source,
        )

    def bulk_resolve(
        self, servers: frozenset[str], *, prompt: str = ""
    ) -> dict[str, ControllerDecision]:
        """Resolve all *servers* in one pass. Returns server → ControllerDecision."""
        return {s: self.server_state(s, prompt=prompt) for s in servers}
