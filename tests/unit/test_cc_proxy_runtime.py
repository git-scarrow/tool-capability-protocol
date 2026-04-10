"""Proxy RuntimeEnvironment defaults must not reject common Claude Code tools."""

from __future__ import annotations

import pytest

from tcp.core.descriptors import CapabilityFlags
from tcp.harness.gating import RuntimeEnvironment, gate_tools
from tcp.harness.models import ToolRecord, ToolSelectionRequest
from tcp.proxy.cc_proxy import _runtime_from_env


def test_proxy_runtime_defaults_network_and_files_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TCP_PROXY_NETWORK", raising=False)
    monkeypatch.delenv("TCP_PROXY_FILE_ACCESS", raising=False)
    monkeypatch.delenv("TCP_PROXY_STDIN", raising=False)
    env = _runtime_from_env()
    assert env.network_enabled is True
    assert env.file_access_enabled is True
    assert env.stdin_enabled is True


def test_bash_survives_gating_with_default_proxy_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bash carries SUPPORTS_NETWORK; network_enabled=False used to reject it everywhere."""
    monkeypatch.delenv("TCP_PROXY_NETWORK", raising=False)
    env = _runtime_from_env()
    bash = ToolRecord(
        tool_name="Bash",
        descriptor_source="test",
        descriptor_version="1",
        capability_flags=int(CapabilityFlags.SUPPORTS_FILES)
        | int(CapabilityFlags.SUPPORTS_NETWORK),
        risk_level="safe",
    )
    req = ToolSelectionRequest.from_kwargs(required_capability_flags=0)
    result = gate_tools([bash], req, env)
    assert len(result.approved_tools) == 1


def test_explicit_network_off_rejects_bash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TCP_PROXY_NETWORK", "0")
    env = _runtime_from_env()
    assert env.network_enabled is False
    bash = ToolRecord(
        tool_name="Bash",
        descriptor_source="test",
        descriptor_version="1",
        capability_flags=int(CapabilityFlags.SUPPORTS_FILES)
        | int(CapabilityFlags.SUPPORTS_NETWORK),
        risk_level="safe",
    )
    req = ToolSelectionRequest.from_kwargs(required_capability_flags=0)
    result = gate_tools([bash], req, env)
    assert len(result.approved_tools) == 0
    assert len(result.rejected_tools) == 1
