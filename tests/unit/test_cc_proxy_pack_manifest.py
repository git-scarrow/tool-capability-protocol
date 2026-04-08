"""Manifest-driven pack activation for proxy Stage 2."""

from __future__ import annotations

from tcp.proxy.pack_manifest import (
    default_manifest_path,
    load_pack_manifest,
    pack_context_from_env,
    resolve_pack_decisions,
)


def test_load_pack_manifest_prefers_workspace_file() -> None:
    manifest = load_pack_manifest()
    assert manifest.source_path == str(default_manifest_path())
    assert any(pack.pack_id == "workspace-critical" for pack in manifest.packs)


def test_workspace_allow_moves_workspace_critical_pack_to_deferred() -> None:
    manifest = load_pack_manifest()
    context = pack_context_from_env(
        cwd="/home/sam/projects/tool-capability-protocol",
        profile="default",
        workspace_allowed_servers=frozenset({"bay-view-graph"}),
    )
    pack_decisions, server_decisions = resolve_pack_decisions(manifest, context)
    assert pack_decisions["workspace-critical"].state == "deferred"
    assert "workspace_allow" in pack_decisions["workspace-critical"].reasons
    assert server_decisions["bay-view-graph"].state == "deferred"


def test_profile_activation_promotes_workspace_critical_pack_to_active() -> None:
    manifest = load_pack_manifest()
    context = pack_context_from_env(
        cwd="/home/sam/projects/tool-capability-protocol",
        profile="bay-view",
        workspace_allowed_servers=frozenset(),
    )
    pack_decisions, server_decisions = resolve_pack_decisions(manifest, context)
    assert pack_decisions["workspace-critical"].state == "active"
    assert "profile:bay-view" in pack_decisions["workspace-critical"].reasons
    assert server_decisions["bay-view-graph"].state == "active"
