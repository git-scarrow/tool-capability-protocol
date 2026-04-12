"""Unit tests for ToolPackController (TCP-IMP-13).

Covers all four DS-3 resolution steps, invariants, rule attribution, and
the bay-view-graph regression (workspace-listed server never SUPPRESSED).
"""

from __future__ import annotations

import pytest

from tcp.proxy.controller import (
    TPC_RULE_DEFAULT,
    TPC_RULE_HEURISTIC_UPGRADE,
    TPC_RULE_MANIFEST_FLOOR,
    TPC_RULE_POLICY_OVERRIDE,
    TPC_RULE_SAFETY_FLOOR,
    ToolPackController,
)
from tcp.proxy.pack_manifest import (
    PackContext,
    PackManifest,
    PackRule,
    STATE_ACTIVE,
    STATE_DEFERRED,
    STATE_SUPPRESSED,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _ctx(
    *,
    workspace_name: str = "test-ws",
    workspace_path: str = "/home/test/projects/myapp",
    profile: str = "",
    workspace_allowed_servers: frozenset[str] = frozenset(),
    env: dict[str, str] | None = None,
) -> PackContext:
    return PackContext(
        workspace_name=workspace_name,
        workspace_path=workspace_path,
        profile=profile,
        workspace_allowed_servers=workspace_allowed_servers,
        env=env or {},
    )


def _manifest(*packs: PackRule) -> PackManifest:
    return PackManifest(version=1, source_path="test", packs=tuple(packs))


def _controller(
    *packs: PackRule,
    context: PackContext | None = None,
    allowed_servers: frozenset[str] = frozenset(),
    hard_allow_override: bool = False,
) -> ToolPackController:
    return ToolPackController(
        _manifest(*packs),
        context or _ctx(),
        allowed_servers=allowed_servers,
        hard_allow_override=hard_allow_override,
    )


# ── Canonical pack fixtures ───────────────────────────────────────────────────

_SAFETY_PACK = PackRule(
    pack_id="core-coding",
    servers=frozenset(["filesystem"]),
    default_state=STATE_ACTIVE,
)

_SUPPRESSED_PACK = PackRule(
    pack_id="suppressed-pack",
    servers=frozenset(["orphan-server"]),
    default_state=STATE_SUPPRESSED,
)

_ACTIVE_DEFAULT_PACK = PackRule(
    pack_id="always-active-pack",
    servers=frozenset(["coding-server"]),
    default_state=STATE_ACTIVE,
)

_WORKSPACE_ACTIVE_PACK = PackRule(
    pack_id="workspace-active-pack",
    servers=frozenset(["bay-view-graph"]),
    default_state=STATE_SUPPRESSED,
    active_workspaces=frozenset(["test-ws"]),
)

_PROFILE_ACTIVE_PACK = PackRule(
    pack_id="profile-active-pack",
    servers=frozenset(["bay-view-graph"]),
    default_state=STATE_SUPPRESSED,
    active_profiles=frozenset(["bay-view"]),
)

_WORKSPACE_ALLOW_PACK = PackRule(
    pack_id="workspace-allow-pack",
    servers=frozenset(["bay-view-graph"]),
    default_state=STATE_SUPPRESSED,
    allow_workspace=True,
)


# ── Step 1: Safety floor ──────────────────────────────────────────────────────


def test_safety_floor_core_coding_always_active() -> None:
    tpc = _controller(_SAFETY_PACK)
    d = tpc.server_state("filesystem")
    assert d.state == STATE_ACTIVE
    assert d.tpc_rule == TPC_RULE_SAFETY_FLOOR


def test_safety_floor_overrides_hard_allow_policy() -> None:
    """core-coding server must be ACTIVE even when policy would suppress it."""
    tpc = _controller(
        _SAFETY_PACK,
        allowed_servers=frozenset(),   # filesystem NOT in the allow list
        hard_allow_override=True,
    )
    d = tpc.server_state("filesystem")
    assert d.state == STATE_ACTIVE
    assert d.tpc_rule == TPC_RULE_SAFETY_FLOOR


def test_safety_floor_not_triggered_for_non_core_server() -> None:
    """A non-core-coding server in a suppressed pack is not rescued by the floor."""
    tpc = _controller(_SUPPRESSED_PACK)
    d = tpc.server_state("orphan-server")
    assert d.state == STATE_SUPPRESSED


# ── Step 2: Policy (hard_allow_override) ─────────────────────────────────────


def test_policy_override_suppresses_server_not_in_allow_list() -> None:
    tpc = _controller(
        _ACTIVE_DEFAULT_PACK,
        allowed_servers=frozenset(),
        hard_allow_override=True,
    )
    d = tpc.server_state("coding-server")
    assert d.state == STATE_SUPPRESSED
    assert d.tpc_rule == TPC_RULE_POLICY_OVERRIDE


def test_policy_override_does_not_suppress_server_in_allow_list() -> None:
    tpc = _controller(
        _ACTIVE_DEFAULT_PACK,
        allowed_servers=frozenset(["coding-server"]),
        hard_allow_override=True,
    )
    d = tpc.server_state("coding-server")
    assert d.state != STATE_SUPPRESSED


def test_policy_override_inactive_when_flag_false() -> None:
    """With hard_allow_override=False, non-listed servers pass through normally."""
    tpc = _controller(
        _ACTIVE_DEFAULT_PACK,
        allowed_servers=frozenset(),
        hard_allow_override=False,
    )
    d = tpc.server_state("coding-server")
    assert d.state == STATE_ACTIVE


# ── Visibility floor: workspace/profile/env match beats policy ───────────────


def test_visibility_floor_workspace_match_survives_policy() -> None:
    """A workspace-matched server must be at least DEFERRED under hard_allow_override."""
    tpc = _controller(
        _WORKSPACE_ACTIVE_PACK,
        context=_ctx(workspace_name="test-ws"),
        allowed_servers=frozenset(),
        hard_allow_override=True,
    )
    d = tpc.server_state("bay-view-graph")
    assert d.state != STATE_SUPPRESSED
    assert d.tpc_rule == TPC_RULE_MANIFEST_FLOOR


def test_visibility_floor_profile_match_survives_policy() -> None:
    """A profile-matched server must be at least DEFERRED under hard_allow_override."""
    tpc = _controller(
        _PROFILE_ACTIVE_PACK,
        context=_ctx(profile="bay-view"),
        allowed_servers=frozenset(),
        hard_allow_override=True,
    )
    d = tpc.server_state("bay-view-graph")
    assert d.state != STATE_SUPPRESSED
    assert d.tpc_rule == TPC_RULE_MANIFEST_FLOOR


def test_visibility_floor_workspace_allow_survives_policy() -> None:
    """A workspace_allow-listed server must be at least DEFERRED under policy."""
    tpc = _controller(
        _WORKSPACE_ALLOW_PACK,
        context=_ctx(workspace_allowed_servers=frozenset(["bay-view-graph"])),
        allowed_servers=frozenset(),
        hard_allow_override=True,
    )
    d = tpc.server_state("bay-view-graph")
    assert d.state != STATE_SUPPRESSED
    assert d.tpc_rule == TPC_RULE_MANIFEST_FLOOR


# ── Step 3: Manifest state ────────────────────────────────────────────────────


def test_manifest_active_default_produces_active() -> None:
    tpc = _controller(_ACTIVE_DEFAULT_PACK)
    d = tpc.server_state("coding-server")
    assert d.state == STATE_ACTIVE
    assert d.tpc_rule == TPC_RULE_DEFAULT


def test_manifest_suppressed_default_produces_suppressed() -> None:
    tpc = _controller(_SUPPRESSED_PACK)
    d = tpc.server_state("orphan-server")
    assert d.state == STATE_SUPPRESSED
    assert d.tpc_rule == TPC_RULE_DEFAULT


def test_manifest_workspace_allow_produces_deferred() -> None:
    """A pack with allow_workspace=True and a matching server → DEFERRED."""
    tpc = _controller(
        _WORKSPACE_ALLOW_PACK,
        context=_ctx(workspace_allowed_servers=frozenset(["bay-view-graph"])),
    )
    d = tpc.server_state("bay-view-graph")
    assert d.state == STATE_DEFERRED
    assert d.tpc_rule == TPC_RULE_MANIFEST_FLOOR


def test_manifest_workspace_allow_not_triggered_without_listing() -> None:
    """workspace_allow pack with server absent from workspace_allowed_servers → SUPPRESSED."""
    tpc = _controller(
        _WORKSPACE_ALLOW_PACK,
        context=_ctx(workspace_allowed_servers=frozenset()),  # bay-view-graph NOT listed
    )
    d = tpc.server_state("bay-view-graph")
    assert d.state == STATE_SUPPRESSED


def test_manifest_workspace_match_produces_active() -> None:
    tpc = _controller(
        _WORKSPACE_ACTIVE_PACK,
        context=_ctx(workspace_name="test-ws"),
    )
    d = tpc.server_state("bay-view-graph")
    assert d.state == STATE_ACTIVE
    assert d.tpc_rule == TPC_RULE_MANIFEST_FLOOR


def test_manifest_profile_match_produces_active() -> None:
    tpc = _controller(
        _PROFILE_ACTIVE_PACK,
        context=_ctx(profile="bay-view"),
    )
    d = tpc.server_state("bay-view-graph")
    assert d.state == STATE_ACTIVE
    assert d.tpc_rule == TPC_RULE_MANIFEST_FLOOR


# ── Step 4: Heuristic upgrade ─────────────────────────────────────────────────


def test_heuristic_upgrades_suppressed_to_deferred_on_name_mention() -> None:
    tpc = _controller(_SUPPRESSED_PACK)
    d = tpc.server_state("orphan-server", prompt="use orphan-server to list items")
    assert d.state == STATE_DEFERRED
    assert d.tpc_rule == TPC_RULE_HEURISTIC_UPGRADE


def test_heuristic_does_not_trigger_without_prompt() -> None:
    tpc = _controller(_SUPPRESSED_PACK)
    d = tpc.server_state("orphan-server", prompt="")
    assert d.state == STATE_SUPPRESSED


def test_heuristic_does_not_trigger_with_unrelated_prompt() -> None:
    tpc = _controller(_SUPPRESSED_PACK)
    d = tpc.server_state("orphan-server", prompt="read the config file and fix the bug")
    assert d.state == STATE_SUPPRESSED


# ── Monotonicity invariant ────────────────────────────────────────────────────


def test_monotonicity_heuristic_cannot_downgrade_active() -> None:
    """An ACTIVE server must remain ACTIVE regardless of prompt content."""
    tpc = _controller(_ACTIVE_DEFAULT_PACK)
    for prompt in ("", "do not use coding-server", "suppress coding-server", "🚫"):
        d = tpc.server_state("coding-server", prompt=prompt)
        assert d.state == STATE_ACTIVE, f"downgraded with prompt={prompt!r}"


def test_monotonicity_heuristic_cannot_downgrade_deferred() -> None:
    """A DEFERRED server must remain at least DEFERRED regardless of prompt."""
    tpc = _controller(
        _WORKSPACE_ALLOW_PACK,
        context=_ctx(workspace_allowed_servers=frozenset(["bay-view-graph"])),
    )
    for prompt in ("", "ignore bay-view-graph", "suppress bay view graph"):
        d = tpc.server_state("bay-view-graph", prompt=prompt)
        assert d.state in (STATE_DEFERRED, STATE_ACTIVE), (
            f"downgraded to SUPPRESSED with prompt={prompt!r}"
        )


# ── Rule attribution completeness ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "pack, server, context_kwargs, controller_kwargs, expected_rule",
    [
        (
            _SAFETY_PACK,
            "filesystem",
            {},
            {},
            TPC_RULE_SAFETY_FLOOR,
        ),
        (
            _ACTIVE_DEFAULT_PACK,
            "coding-server",
            {},
            {"allowed_servers": frozenset(), "hard_allow_override": True},
            TPC_RULE_POLICY_OVERRIDE,
        ),
        (
            _WORKSPACE_ALLOW_PACK,
            "bay-view-graph",
            {"workspace_allowed_servers": frozenset(["bay-view-graph"])},
            {},
            TPC_RULE_MANIFEST_FLOOR,
        ),
        (
            _SUPPRESSED_PACK,
            "orphan-server",
            {},
            {},
            TPC_RULE_DEFAULT,
        ),
    ],
)
def test_rule_attribution(
    pack: PackRule,
    server: str,
    context_kwargs: dict,
    controller_kwargs: dict,
    expected_rule: str,
) -> None:
    tpc = _controller(pack, context=_ctx(**context_kwargs), **controller_kwargs)
    d = tpc.server_state(server)
    assert d.tpc_rule == expected_rule


def test_rule_attribution_heuristic_upgrade() -> None:
    tpc = _controller(_SUPPRESSED_PACK)
    d = tpc.server_state("orphan-server", prompt="please use orphan-server")
    assert d.tpc_rule == TPC_RULE_HEURISTIC_UPGRADE


# ── bay-view-graph regression (TCP-IMP-9 false-reject) ───────────────────────


def test_bay_view_graph_workspace_profile_never_suppressed() -> None:
    """Regression: bay-view-graph listed via profile must be ≥ DEFERRED."""
    tpc = _controller(
        _PROFILE_ACTIVE_PACK,
        context=_ctx(profile="bay-view"),
    )
    d = tpc.server_state("bay-view-graph")
    assert d.state != STATE_SUPPRESSED


def test_bay_view_graph_workspace_allow_never_suppressed() -> None:
    """Regression: bay-view-graph listed via workspace_allow must be ≥ DEFERRED."""
    tpc = _controller(
        _WORKSPACE_ALLOW_PACK,
        context=_ctx(workspace_allowed_servers=frozenset(["bay-view-graph"])),
    )
    d = tpc.server_state("bay-view-graph")
    assert d.state != STATE_SUPPRESSED


def test_bay_view_graph_workspace_active_never_suppressed() -> None:
    """Regression: bay-view-graph matched by workspace name must be ≥ DEFERRED."""
    tpc = _controller(
        _WORKSPACE_ACTIVE_PACK,
        context=_ctx(workspace_name="test-ws"),
    )
    d = tpc.server_state("bay-view-graph")
    assert d.state != STATE_SUPPRESSED


def test_bay_view_graph_workspace_allow_survives_policy() -> None:
    """Regression: bay-view-graph must survive hard_allow_override if workspace-listed."""
    tpc = _controller(
        _WORKSPACE_ALLOW_PACK,
        context=_ctx(workspace_allowed_servers=frozenset(["bay-view-graph"])),
        allowed_servers=frozenset(),
        hard_allow_override=True,
    )
    d = tpc.server_state("bay-view-graph")
    assert d.state != STATE_SUPPRESSED


# ── bulk_resolve ──────────────────────────────────────────────────────────────


def test_bulk_resolve_returns_decision_for_every_server() -> None:
    tpc = _controller(_ACTIVE_DEFAULT_PACK, _SUPPRESSED_PACK)
    servers = frozenset(["coding-server", "orphan-server"])
    results = tpc.bulk_resolve(servers)
    assert set(results.keys()) == servers


def test_bulk_resolve_passes_prompt_to_all_servers() -> None:
    """Heuristic upgrade via bulk_resolve rescues suppressed server named in prompt."""
    tpc = _controller(_SUPPRESSED_PACK)
    results = tpc.bulk_resolve(
        frozenset(["orphan-server"]),
        prompt="call orphan-server for this task",
    )
    assert results["orphan-server"].state == STATE_DEFERRED
    assert results["orphan-server"].tpc_rule == TPC_RULE_HEURISTIC_UPGRADE


def test_bulk_resolve_empty_servers() -> None:
    tpc = _controller(_ACTIVE_DEFAULT_PACK)
    results = tpc.bulk_resolve(frozenset())
    assert results == {}


# ── Unknown server (not in any pack) ─────────────────────────────────────────


def test_unknown_server_not_in_allow_list_is_suppressed() -> None:
    """A server not in any pack and not in allowed_servers → SUPPRESSED."""
    tpc = _controller(_ACTIVE_DEFAULT_PACK)
    d = tpc.server_state("mystery-server")
    assert d.state == STATE_SUPPRESSED


def test_unknown_server_in_allow_list_is_active() -> None:
    """A server not in any pack but listed in allowed_servers → ACTIVE."""
    tpc = _controller(
        _ACTIVE_DEFAULT_PACK,
        allowed_servers=frozenset(["mystery-server"]),
    )
    d = tpc.server_state("mystery-server")
    assert d.state == STATE_ACTIVE
