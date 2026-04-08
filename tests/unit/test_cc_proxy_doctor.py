"""Operator-facing pack manifest doctor surface."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tcp.proxy.pack_manifest import STATE_DEFERRED, inspect_pack_state


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_inspect_pack_state_reports_workspace_allow_reason() -> None:
    inspection = inspect_pack_state(
        cwd="/home/sam/projects/tool-capability-protocol",
        profile="default",
        workspace_allowed_servers=frozenset({"bay-view-graph"}),
        use_cache=False,
    )
    workspace_critical = next(
        decision for decision in inspection.pack_decisions if decision.pack_id == "workspace-critical"
    )
    assert workspace_critical.state == STATE_DEFERRED
    assert "workspace_allow" in workspace_critical.reasons


def test_doctor_cli_json_reports_manifest_profile_and_reasons(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TCP_PROXY_PACK_MANIFEST", raising=False)
    monkeypatch.setenv("TCP_PROXY_CWD", "/home/sam/projects/tool-capability-protocol")
    monkeypatch.setenv("TCP_PROXY_PROFILE", "bay-view")
    monkeypatch.setenv("TCP_PROXY_WORKSPACE_MCP_SERVERS", "bay-view-graph")
    result = subprocess.run(
        [sys.executable, "-m", "tcp.proxy.cc_proxy", "--doctor", "--doctor-format", "json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
        env=os.environ.copy(),
    )
    payload = json.loads(result.stdout)
    assert payload["profile"] == "bay-view"
    assert payload["manifest_source"].endswith(".tcp-proxy-packs.yaml")
    workspace_critical = next(
        pack for pack in payload["packs"] if pack["pack_id"] == "workspace-critical"
    )
    assert workspace_critical["state"] == "active"
    assert "profile:bay-view" in workspace_critical["reasons"]


def test_doctor_cli_fails_directly_for_malformed_explicit_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bad_manifest = tmp_path / "broken-packs.yaml"
    bad_manifest.write_text("version: [\npacks:\n  - pack_id: bad\n", encoding="utf-8")
    monkeypatch.setenv("TCP_PROXY_PACK_MANIFEST", str(bad_manifest))
    result = subprocess.run(
        [sys.executable, "-m", "tcp.proxy.cc_proxy", "--doctor"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert result.returncode == 2
    assert "explicit pack manifest is invalid" in result.stderr
    assert str(bad_manifest) in result.stderr
