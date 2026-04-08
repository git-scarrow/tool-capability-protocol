"""Manifest-driven pack activation for proxy Stage 2."""

from __future__ import annotations

from pathlib import Path

import pytest

from tcp.proxy.pack_manifest import (
    DEFAULT_PROFILE,
    MANIFEST_VERSION,
    STATE_ACTIVE,
    STATE_DEFERRED,
    _load_manifest_from_cache_key,
    default_manifest_path,
    inspect_pack_state,
    load_pack_manifest,
    pack_context_from_env,
    resolve_pack_decisions,
)


def test_load_pack_manifest_prefers_workspace_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TCP_PROXY_PACK_MANIFEST", raising=False)
    monkeypatch.delenv("TCP_PROXY_CWD", raising=False)
    monkeypatch.delenv("TCP_PROXY_WORKSPACE_PROFILE", raising=False)
    monkeypatch.delenv("TCP_PROXY_PROFILE", raising=False)
    manifest = load_pack_manifest(use_cache=False)
    assert Path(manifest.source_path).resolve() == default_manifest_path().resolve()
    assert manifest.version == MANIFEST_VERSION
    assert any(pack.pack_id == "workspace-critical" for pack in manifest.packs)


def test_workspace_allow_moves_workspace_critical_pack_to_deferred() -> None:
    manifest = load_pack_manifest()
    context = pack_context_from_env(
        cwd="/home/sam/projects/tool-capability-protocol",
        profile="default",
        workspace_allowed_servers=frozenset({"bay-view-graph"}),
    )
    pack_decisions, server_decisions = resolve_pack_decisions(manifest, context)
    assert pack_decisions["workspace-critical"].state == STATE_DEFERRED
    assert "workspace_allow" in pack_decisions["workspace-critical"].reasons
    assert server_decisions["bay-view-graph"].state == STATE_DEFERRED


def test_profile_activation_promotes_workspace_critical_pack_to_active() -> None:
    manifest = load_pack_manifest()
    context = pack_context_from_env(
        cwd="/home/sam/projects/tool-capability-protocol",
        profile="bay-view",
        workspace_allowed_servers=frozenset(),
    )
    pack_decisions, server_decisions = resolve_pack_decisions(manifest, context)
    assert pack_decisions["workspace-critical"].state == STATE_ACTIVE
    assert "profile:bay-view" in pack_decisions["workspace-critical"].reasons
    assert server_decisions["bay-view-graph"].state == STATE_ACTIVE


def test_pack_context_defaults_to_default_profile(monkeypatch) -> None:
    monkeypatch.delenv("TCP_PROXY_WORKSPACE_PROFILE", raising=False)
    monkeypatch.delenv("TCP_PROXY_PROFILE", raising=False)
    context = pack_context_from_env(cwd="/home/sam/projects/tool-capability-protocol")
    assert context.profile == DEFAULT_PROFILE


def test_pack_context_uses_tcp_proxy_profile_when_workspace_profile_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TCP_PROXY_WORKSPACE_PROFILE", raising=False)
    monkeypatch.setenv("TCP_PROXY_PROFILE", "bay-view")
    context = pack_context_from_env(cwd="/home/sam/projects/tool-capability-protocol")
    assert context.profile == "bay-view"


def test_malformed_explicit_manifest_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bad_manifest = tmp_path / ".tcp-proxy-packs.yaml"
    bad_manifest.write_text("version: [\npacks:\n  - pack_id: bad\n", encoding="utf-8")
    monkeypatch.setenv("TCP_PROXY_PACK_MANIFEST", str(bad_manifest))
    manifest = load_pack_manifest(use_cache=False)
    assert Path(manifest.source_path).resolve() == default_manifest_path().resolve()
    assert any(pack.pack_id == "workspace-critical" for pack in manifest.packs)


def test_manifest_cache_refreshes_when_file_changes(tmp_path: Path) -> None:
    manifest_path = tmp_path / "packs.yaml"
    manifest_path.write_text(
        "version: 1\npacks:\n"
        "  - pack_id: cache-test\n"
        "    default_state: suppressed\n"
        "    servers:\n"
        "      - cache-server\n",
        encoding="utf-8",
    )
    first_stat = manifest_path.stat()
    first = _load_manifest_from_cache_key(str(manifest_path), first_stat.st_mtime_ns)
    assert first.packs[0].pack_id == "cache-test"

    manifest_path.write_text(
        "version: 1\npacks:\n"
        "  - pack_id: cache-test-updated\n"
        "    default_state: suppressed\n"
        "    servers:\n"
        "      - cache-server\n",
        encoding="utf-8",
    )
    second_stat = manifest_path.stat()
    second = _load_manifest_from_cache_key(str(manifest_path), second_stat.st_mtime_ns)
    assert second.packs[0].pack_id == "cache-test-updated"


def test_inspect_pack_state_reports_manifest_source_and_pack_reasons() -> None:
    inspection = inspect_pack_state(
        cwd="/home/sam/projects/tool-capability-protocol",
        profile="bay-view",
        workspace_allowed_servers=frozenset(),
        use_cache=False,
    )
    assert Path(inspection.manifest_source).resolve() == default_manifest_path().resolve()
    assert inspection.profile == "bay-view"
    assert inspection.pack_decisions
    workspace_critical = next(
        decision for decision in inspection.pack_decisions if decision.pack_id == "workspace-critical"
    )
    assert workspace_critical.state == STATE_ACTIVE
    assert "profile:bay-view" in workspace_critical.reasons
