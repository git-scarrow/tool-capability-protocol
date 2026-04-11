"""Unit tests for ToolPackController (TCP-IMP-13 / DS-3).

Covers:
- Visibility floor invariant: manifest-listed server is never SUPPRESSED
- Monotonicity invariant: heuristic upgrade cannot downgrade ACTIVE/DEFERRED
- Regression: bay-view-graph listed in workspace profile → always ≥ DEFERRED
- Policy override → SUPPRESSED (hard override)
- Safety floor: core-coding pack always ACTIVE
- tpc_rule attribution in ControllerResult
"""

from __future__ import annotations

import pytest

from tcp.proxy.controller import (
    RULE_DEFAULT,
    RULE_HEURISTIC_UPGRADE,
    RULE_MANIFEST_FLOOR,
    RULE_POLICY_OVERRIDE,
    RULE_SAFETY_FLOOR,
    ToolPackController,
)
from tcp.proxy.pack_manifest import (
    STATE_ACTIVE,
    STATE_DEFERRED,
    STATE_SUPPRESSED,
    PackContext,
    PackManifest,
    PackRule,
    default_manifest_path,
    load_pack_manifest,
    pack_context_from_env,
)


# ── Fixtures / helpers ────────────────────────────────────────────────────────


def _make_manifest(
    *rules: PackRule,
    source_path: str = "<test>",
) -> PackManifest:
    return PackManifest(version=1, source_path=source_path, packs=tuple(rules))


def _make_context(
    workspace_name: str = "test-ws",
    workspace_path: str = "/home/user/test-ws",
    profile: str = "default",
    workspace_allowed_servers: frozenset[str] | None = None,
) -> PackContext:
    return PackContext(
        workspace_name=workspace_name,
        workspace_path=workspace_path,
        profile=profile,
        workspace_allowed_servers=workspace_allowed_servers or frozenset(),
        env={},
    )


def _core_coding_rule() -> PackRule:
    return PackRule(
        pack_id="core-coding",
        servers=frozenset({"filesystem", "git"}),
        default_state=STATE_ACTIVE,
    )


def _workspace_critical_rule() -> PackRule:
    return PackRule(
        pack_id="workspace-critical",
        servers=frozenset({"bay-view-graph"}),
        default_state=STATE_SUPPRESSED,
        allow_workspace=True,
        active_workspaces=frozenset({"bay-view"}),
        active_profiles=frozenset({"bay-view"}),
    )


# ── DS-3 invariant: Visibility floor ─────────────────────────────────────────


def test_visibility_floor_workspace_name_match_gives_at_least_deferred() -> None:
    """A server listed in active_workspaces with allow_workspace=True must be ≥ DEFERRED."""
    manifest = _make_manifest(_workspace_critical_rule())
    context = _make_context(
        workspace_allowed_servers=frozenset({"bay-view-graph"}),
    )
    result = ToolPackController(manifest, context).resolve()
    state = result.server_decisions["bay-view-graph"].state
    assert state in (STATE_DEFERRED, STATE_ACTIVE), (
        f"visibility floor violated: bay-view-graph is {state!r}"
    )


def test_visibility_floor_profile_match_gives_active() -> None:
    """A pack with active_profiles matching the current profile → ACTIVE."""
    manifest = _make_manifest(_workspace_critical_rule())
    context = _make_context(profile="bay-view")
    result = ToolPackController(manifest, context).resolve()
    assert result.server_decisions["bay-view-graph"].state == STATE_ACTIVE


def test_visibility_floor_no_match_gives_suppressed() -> None:
    """Without workspace allow or profile match, default SUPPRESSED pack stays SUPPRESSED."""
    manifest = _make_manifest(_workspace_critical_rule())
    context = _make_context()
    result = ToolPackController(manifest, context).resolve()
    assert result.server_decisions["bay-view-graph"].state == STATE_SUPPRESSED


def test_visibility_floor_explicit_workspace_path_match() -> None:
    """active_workspaces can match by workspace_path, not just workspace_name."""
    rule = PackRule(
        pack_id="path-pack",
        servers=frozenset({"path-server"}),
        default_state=STATE_SUPPRESSED,
        active_workspaces=frozenset({"/home/user/specific-project"}),
    )
    manifest = _make_manifest(rule)
    context = _make_context(
        workspace_path="/home/user/specific-project",
    )
    result = ToolPackController(manifest, context).resolve()
    assert result.server_decisions["path-server"].state == STATE_ACTIVE


# ── DS-3 invariant: Monotonicity ─────────────────────────────────────────────


def test_monotonicity_heuristic_cannot_downgrade_active_pack() -> None:
    """Heuristic upgrade MUST NOT downgrade a pack that is already ACTIVE."""
    rule = PackRule(
        pack_id="already-active",
        servers=frozenset({"active-server"}),
        default_state=STATE_ACTIVE,
    )
    manifest = _make_manifest(rule)
    context = _make_context()

    # A predicate that would return False (could naively "downgrade" to DEFERRED)
    def never_trigger(server: str, prompt: str) -> bool:
        return False

    result = ToolPackController(manifest, context).resolve(
        prompt="irrelevant",
        heuristic_server_predicate=never_trigger,
    )
    assert result.server_decisions["active-server"].state == STATE_ACTIVE


def test_monotonicity_heuristic_upgrades_deferred_to_active() -> None:
    """Heuristic upgrade promotes DEFERRED → ACTIVE when trigger fires."""
    rule = PackRule(
        pack_id="deferred-pack",
        servers=frozenset({"bay-view-graph"}),
        default_state=STATE_SUPPRESSED,
        allow_workspace=True,
    )
    manifest = _make_manifest(rule)
    context = _make_context(
        workspace_allowed_servers=frozenset({"bay-view-graph"}),
    )

    def always_trigger(server: str, prompt: str) -> bool:
        return True

    result = ToolPackController(manifest, context).resolve(
        prompt="use bay-view-graph",
        heuristic_server_predicate=always_trigger,
    )
    assert result.server_decisions["bay-view-graph"].state == STATE_ACTIVE
    assert result.server_tpc_rules["bay-view-graph"] == RULE_HEURISTIC_UPGRADE


def test_monotonicity_heuristic_does_not_affect_suppressed_pack() -> None:
    """Heuristic upgrade only fires when pack is DEFERRED, not SUPPRESSED."""
    rule = PackRule(
        pack_id="suppressed-pack",
        servers=frozenset({"secret-server"}),
        default_state=STATE_SUPPRESSED,
    )
    manifest = _make_manifest(rule)
    context = _make_context()

    def always_trigger(server: str, prompt: str) -> bool:
        return True

    result = ToolPackController(manifest, context).resolve(
        prompt="use secret-server please",
        heuristic_server_predicate=always_trigger,
    )
    assert result.server_decisions["secret-server"].state == STATE_SUPPRESSED


# ── DS-3 regression: bay-view-graph ──────────────────────────────────────────


def test_bay_view_graph_with_workspace_profile_is_at_least_deferred() -> None:
    """Regression: bay-view-graph must never be SUPPRESSED when workspace profile matches."""
    manifest = load_pack_manifest(use_cache=False)
    context = pack_context_from_env(
        cwd="/home/sam/projects/tool-capability-protocol",
        profile="bay-view",
        workspace_allowed_servers=frozenset(),
    )
    result = ToolPackController(manifest, context).resolve()
    state = result.server_decisions.get("bay-view-graph")
    assert state is not None, "bay-view-graph not found in server_decisions"
    assert state.state in (STATE_DEFERRED, STATE_ACTIVE), (
        f"bay-view-graph was {state.state!r}; expected ≥ DEFERRED"
    )


def test_bay_view_graph_with_workspace_allow_is_deferred() -> None:
    """Regression: bay-view-graph listed in workspace_allowed_servers → at least DEFERRED."""
    manifest = load_pack_manifest(use_cache=False)
    context = pack_context_from_env(
        cwd="/home/sam/projects/tool-capability-protocol",
        profile="default",
        workspace_allowed_servers=frozenset({"bay-view-graph"}),
    )
    result = ToolPackController(manifest, context).resolve()
    state = result.server_decisions.get("bay-view-graph")
    assert state is not None
    assert state.state in (STATE_DEFERRED, STATE_ACTIVE), (
        f"bay-view-graph was {state.state!r}; expected ≥ DEFERRED"
    )


def test_bay_view_graph_without_any_context_is_suppressed() -> None:
    """Regression: without workspace allow or profile, bay-view-graph stays SUPPRESSED."""
    manifest = load_pack_manifest(use_cache=False)
    context = pack_context_from_env(
        cwd="/home/sam/projects/other-project",
        profile="default",
        workspace_allowed_servers=frozenset(),
    )
    result = ToolPackController(manifest, context).resolve()
    state = result.server_decisions.get("bay-view-graph")
    assert state is not None
    assert state.state == STATE_SUPPRESSED


# ── Policy override ───────────────────────────────────────────────────────────


def test_policy_override_suppresses_banned_pack() -> None:
    """BANNED pack → SUPPRESSED regardless of manifest configuration."""
    rule = PackRule(
        pack_id="dangerous-pack",
        servers=frozenset({"dangerous-server"}),
        default_state=STATE_ACTIVE,
    )
    manifest = _make_manifest(rule)
    context = _make_context()
    result = ToolPackController(
        manifest, context, policy_banned=frozenset({"dangerous-pack"})
    ).resolve()
    assert result.server_decisions["dangerous-server"].state == STATE_SUPPRESSED
    assert result.server_tpc_rules["dangerous-server"] == RULE_POLICY_OVERRIDE


def test_policy_override_wins_over_profile_activation() -> None:
    """Policy ban overrides profile match — SUPPRESSED even if profile matches."""
    rule = PackRule(
        pack_id="banned-but-profile-matches",
        servers=frozenset({"risky-server"}),
        default_state=STATE_SUPPRESSED,
        active_profiles=frozenset({"bay-view"}),
    )
    manifest = _make_manifest(rule)
    context = _make_context(profile="bay-view")
    result = ToolPackController(
        manifest, context, policy_banned=frozenset({"banned-but-profile-matches"})
    ).resolve()
    assert result.server_decisions["risky-server"].state == STATE_SUPPRESSED
    assert result.server_tpc_rules["risky-server"] == RULE_POLICY_OVERRIDE


# ── Safety floor ─────────────────────────────────────────────────────────────


def test_safety_floor_keeps_core_coding_active() -> None:
    """core-coding pack must always be ACTIVE (safety floor step 4)."""
    manifest = load_pack_manifest(use_cache=False)
    context = _make_context()
    result = ToolPackController(manifest, context).resolve()
    assert result.pack_decisions["core-coding"].state == STATE_ACTIVE


def test_safety_floor_overrides_suppressed_default_for_core_coding_pack() -> None:
    """Even if core-coding has default_state=suppressed, safety floor forces ACTIVE."""
    rule = PackRule(
        pack_id="core-coding",
        servers=frozenset({"filesystem"}),
        default_state=STATE_SUPPRESSED,
    )
    manifest = _make_manifest(rule)
    context = _make_context()
    result = ToolPackController(manifest, context).resolve()
    assert result.pack_decisions["core-coding"].state == STATE_ACTIVE
    assert result.server_tpc_rules["filesystem"] == RULE_SAFETY_FLOOR


# ── tpc_rule attribution ──────────────────────────────────────────────────────


def test_tpc_rule_default_for_unmatched_active_pack() -> None:
    """Pack with default_state=active and no special match → tpc_rule=default."""
    rule = PackRule(
        pack_id="simple-active",
        servers=frozenset({"simple-server"}),
        default_state=STATE_ACTIVE,
    )
    manifest = _make_manifest(rule)
    context = _make_context()
    result = ToolPackController(manifest, context).resolve()
    assert result.server_tpc_rules["simple-server"] == RULE_DEFAULT


def test_tpc_rule_manifest_floor_for_profile_activation() -> None:
    """Profile-activated pack gets tpc_rule=manifest_floor."""
    rule = PackRule(
        pack_id="profile-activated",
        servers=frozenset({"profile-server"}),
        default_state=STATE_SUPPRESSED,
        active_profiles=frozenset({"my-profile"}),
    )
    manifest = _make_manifest(rule)
    context = _make_context(profile="my-profile")
    result = ToolPackController(manifest, context).resolve()
    assert result.pack_decisions["profile-activated"].state == STATE_ACTIVE
    assert result.server_tpc_rules["profile-server"] == RULE_MANIFEST_FLOOR


def test_tpc_rule_is_included_in_server_resolutions() -> None:
    """server_tpc_rules dict maps every server in the manifest."""
    manifest = load_pack_manifest(use_cache=False)
    context = _make_context()
    result = ToolPackController(manifest, context).resolve()
    for pack in manifest.packs:
        for server in pack.servers:
            assert server in result.server_tpc_rules, (
                f"tpc_rule missing for server {server!r}"
            )


# ── decisions.jsonl tpc_rule field ───────────────────────────────────────────


def test_process_tools_array_meta_includes_server_tpc_rules(monkeypatch) -> None:
    """_process_tools_array meta dict includes server_tpc_rules for decisions.jsonl."""
    from tcp.proxy.cc_proxy import _process_tools_array

    monkeypatch.delenv("TCP_PROXY_WORKSPACE_MCP_SERVERS", raising=False)
    monkeypatch.delenv("TCP_PROXY_WORKSPACE_PROFILE", raising=False)
    monkeypatch.delenv("TCP_PROXY_PROFILE", raising=False)

    tools = [
        {
            "name": "mcp__filesystem__read_file",
            "description": "Read a file.",
            "input_schema": {"type": "object"},
        }
    ]
    body = {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "Read config."}]}
        ]
    }
    _, meta = _process_tools_array(tools, body, "live")
    assert "server_tpc_rules" in meta
    assert isinstance(meta["server_tpc_rules"], dict)
    # filesystem server should be in core-coding pack → some tpc_rule assigned
    assert "filesystem" in meta["server_tpc_rules"]
