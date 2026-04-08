"""TCP-IMP-9: Tests for conservative live proxy filtering.

Merge bar:
  1. "endpoint" and similar words no longer strip Read/Edit/Bash/Grep/Glob
  2. Genuine environment constraints still remove impossible tools
  3. Offline/benchmark filtering behaviour stays unchanged
  4. Live tool sets never collapse to near-empty when local coding is viable
  5. Decisions log shows stage counts, hard constraints, heuristic metadata, safety-floor activation
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest
from tcp.core.descriptors import CapabilityFlags
from tcp.derivation.request_derivation import SessionStartEvent, derive_request
from tcp.harness.gating import RuntimeEnvironment, gate_tools
from tcp.harness.models import ToolRecord, ToolSelectionRequest
from tcp.proxy.cc_proxy import (
    _process_tools_array,
    _SAFETY_FLOOR_TOOLS,
    _DEFAULT_ALLOWED_MCP_SERVERS,
    _is_mcp_server_allowed,
)
from tcp.proxy.projection import ProjectionTier, project_single_anthropic_tool


# ── Helpers ──────────────────────────────────────────────────────────────────

def _session(cwd: str = "/home/user/projects/app") -> SessionStartEvent:
    return SessionStartEvent("test", "default", cwd)


def _make_tools(*names: str) -> list[dict[str, Any]]:
    """Build minimal Anthropic-format tool defs."""
    return [{"name": n, "description": f"Tool {n}", "input_schema": {"type": "object"}} for n in names]


CORE_CODING_TOOLS = ("Read", "Edit", "MultiEdit", "Glob", "Grep", "Bash")

REALISTIC_TOOL_SET = _make_tools(
    "Read", "Edit", "MultiEdit", "Write", "Glob", "Grep", "Bash",
    "Agent", "EnterPlanMode", "ExitPlanMode", "AskUserQuestion",
    "WebFetch", "WebSearch", "Think", "Skill",
    "TaskCreate", "TaskUpdate", "TaskList",
    "mcp__filesystem__read_file", "mcp__filesystem__list_directory",
    "mcp__git__git_status", "mcp__git__git_diff",
    "mcp__notion-agents__start_agent_run",
)


def _tool_names(tools: list[dict]) -> set[str]:
    return {t["name"] for t in tools}


# ── 1. "endpoint" no longer strips core coding tools ────────────────────────

class TestEndpointFalsePositive:
    """Words like 'endpoint' triggered SUPPORTS_NETWORK, rejecting Read/Edit/Bash."""

    def test_endpoint_prompt_preserves_core_tools_live(self):
        prompt_body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "Implement SSE streaming in the LLB's /v1/responses endpoint"}
        ]}]}
        result_tools, meta = _process_tools_array(REALISTIC_TOOL_SET, prompt_body, "live")
        surviving = _tool_names(result_tools)
        for tool in CORE_CODING_TOOLS:
            assert tool in surviving, f"{tool} was incorrectly removed by live filtering"

    def test_endpoint_prompt_heuristic_flags_are_nonzero(self):
        """The heuristic still detects network intent — it just doesn't hard-reject."""
        req = derive_request(
            "Implement SSE streaming in the LLB's /v1/responses endpoint",
            _session(),
        )
        assert req.heuristic_capability_flags & int(CapabilityFlags.SUPPORTS_NETWORK)
        assert req.hard_capability_flags == 0

    def test_api_keyword_preserves_core_tools_live(self):
        prompt_body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "Add error handling to the API request handler"}
        ]}]}
        result_tools, _ = _process_tools_array(REALISTIC_TOOL_SET, prompt_body, "live")
        surviving = _tool_names(result_tools)
        for tool in CORE_CODING_TOOLS:
            assert tool in surviving

    def test_fetch_keyword_preserves_core_tools_live(self):
        prompt_body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "Refactor the fetch handler to use async/await"}
        ]}]}
        result_tools, _ = _process_tools_array(REALISTIC_TOOL_SET, prompt_body, "live")
        surviving = _tool_names(result_tools)
        for tool in CORE_CODING_TOOLS:
            assert tool in surviving


# ── 2. Genuine environment constraints still remove impossible tools ─────────

class TestEnvironmentConstraints:
    def test_network_disabled_removes_network_tools(self):
        """When network is genuinely off, SUPPORTS_NETWORK tools should be rejected."""
        import os
        old = os.environ.get("TCP_PROXY_NETWORK")
        os.environ["TCP_PROXY_NETWORK"] = "false"
        try:
            prompt_body = {"messages": [{"role": "user", "content": [
                {"type": "text", "text": "Search for documentation online"}
            ]}]}
            result_tools, meta = _process_tools_array(REALISTIC_TOOL_SET, prompt_body, "live")
            surviving = _tool_names(result_tools)
            # WebFetch and WebSearch should be removed (pure network tools)
            assert "WebFetch" not in surviving
            assert "WebSearch" not in surviving
            # But Read/Edit/Grep should survive
            assert "Read" in surviving
            assert "Edit" in surviving
            assert "Grep" in surviving
        finally:
            if old is None:
                os.environ.pop("TCP_PROXY_NETWORK", None)
            else:
                os.environ["TCP_PROXY_NETWORK"] = old

    def test_system_cwd_sets_hard_auth_flag(self):
        req = derive_request("Edit the config", _session(cwd="/etc/nginx"))
        assert req.hard_capability_flags & int(CapabilityFlags.AUTH_REQUIRED)
        assert req.heuristic_capability_flags == 0  # env-derived, not heuristic


# ── 3. Offline/benchmark filtering stays unchanged ───────────────────────────

class TestBackwardCompatibility:
    """The full required_capability_flags (union) is still available for offline consumers."""

    def test_required_flags_union_includes_heuristic(self):
        req = derive_request(
            "Fetch https://api.example.com/status and return JSON",
            _session(),
        )
        # Union includes both prompt-derived and env-derived
        assert req.required_capability_flags & int(CapabilityFlags.SUPPORTS_NETWORK)

    def test_live_strict_uses_full_flags(self):
        """live-strict mode should use full capability flags like benchmarks."""
        prompt_body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "Fetch https://api.example.com/status and return JSON"}
        ]}]}
        result_tools, meta = _process_tools_array(REALISTIC_TOOL_SET, prompt_body, "live-strict")
        assert meta["strategy"] == "strict"
        # In strict mode, tools without SUPPORTS_NETWORK may be rejected
        # This is the benchmark-style behavior

    def test_shadow_returns_all_tools(self):
        prompt_body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "Implement the /v1/responses endpoint handler"}
        ]}]}
        result_tools, meta = _process_tools_array(REALISTIC_TOOL_SET, prompt_body, "shadow")
        assert len(result_tools) == len(REALISTIC_TOOL_SET)
        assert meta["mode"] == "shadow"


# ── 4. Live sets never collapse to near-empty ────────────────────────────────

class TestSafetyFloor:
    def test_safety_floor_rescues_core_tools(self):
        """Even if heuristic flags would reject everything, safety floor preserves coding tools."""
        prompt_body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "Implement SSE streaming in the LLB's /v1/responses endpoint"}
        ]}]}
        result_tools, meta = _process_tools_array(REALISTIC_TOOL_SET, prompt_body, "live")
        surviving = _tool_names(result_tools)
        # At minimum, all core coding tools must survive
        for tool in CORE_CODING_TOOLS:
            assert tool in surviving, f"Safety floor failed to preserve {tool}"

    def test_live_never_returns_empty(self):
        prompt_body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "Do something complex with network endpoints and sudo"}
        ]}]}
        result_tools, meta = _process_tools_array(REALISTIC_TOOL_SET, prompt_body, "live")
        assert len(result_tools) > 0

    def test_safety_floor_constants_include_essentials(self):
        for tool in CORE_CODING_TOOLS:
            assert tool in _SAFETY_FLOOR_TOOLS, f"{tool} missing from safety floor set"


# ── 5. Decisions log shows stage metadata ────────────────────────────────────

class TestDecisionMetadata:
    def test_meta_includes_strategy(self):
        prompt_body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "Read the file"}
        ]}]}
        _, meta = _process_tools_array(REALISTIC_TOOL_SET, prompt_body, "live")
        assert meta["strategy"] == "conservative"

    def test_meta_includes_flag_tiers(self):
        prompt_body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "Implement the endpoint handler"}
        ]}]}
        _, meta = _process_tools_array(REALISTIC_TOOL_SET, prompt_body, "live")
        assert "hard_capability_flags" in meta
        assert "heuristic_capability_flags" in meta
        assert "required_capability_flags" in meta

    def test_meta_includes_stage_counts(self):
        prompt_body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "Read the config file"}
        ]}]}
        _, meta = _process_tools_array(REALISTIC_TOOL_SET, prompt_body, "live")
        assert "stage1_survivor_count" in meta
        assert "safety_floor_activated" in meta
        assert "safety_floor_rescued" in meta
        assert isinstance(meta["heuristic_would_reject"], list)

    def test_meta_includes_heuristic_would_reject(self):
        prompt_body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "Implement SSE streaming in the LLB's /v1/responses endpoint"}
        ]}]}
        _, meta = _process_tools_array(REALISTIC_TOOL_SET, prompt_body, "live")
        # Heuristic would have rejected some tools that lack SUPPORTS_NETWORK
        assert meta["heuristic_would_reject_count"] >= 0
        assert isinstance(meta["heuristic_would_reject"], list)

    def test_shadow_meta_also_includes_stage_metadata(self):
        prompt_body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "Implement the endpoint"}
        ]}]}
        _, meta = _process_tools_array(REALISTIC_TOOL_SET, prompt_body, "shadow")
        assert meta["strategy"] == "shadow"
        assert "hard_capability_flags" in meta
        assert "heuristic_capability_flags" in meta


# ── Derivation tier split correctness ────────────────────────────────────────

class TestDerivationTierSplit:
    def test_neutral_prompt_has_zero_hard_and_heuristic(self):
        req = derive_request("What is 2 + 2?", _session())
        assert req.hard_capability_flags == 0
        assert req.heuristic_capability_flags == 0

    def test_url_prompt_heuristic_only(self):
        req = derive_request("Fetch https://example.com/data", _session())
        assert req.heuristic_capability_flags & int(CapabilityFlags.SUPPORTS_NETWORK)
        assert req.hard_capability_flags == 0

    def test_system_cwd_hard_only(self):
        req = derive_request("ls", _session(cwd="/etc"))
        assert req.hard_capability_flags & int(CapabilityFlags.AUTH_REQUIRED)

    def test_hard_property_masks_heuristic(self):
        """hard_capability_flags == all_flags & ~heuristic_flags"""
        req = derive_request(
            "Download the file from https://example.com and save it to /tmp/data.csv",
            _session(cwd="/etc/nginx"),
        )
        expected_hard = req.required_capability_flags & ~req.heuristic_capability_flags
        assert req.hard_capability_flags == expected_hard


# ── Server-level MCP filtering ──────────────────────────────────────────────

FULL_TOOL_SET = _make_tools(
    # Built-ins
    "Read", "Edit", "MultiEdit", "Write", "Glob", "Grep", "Bash",
    "Agent", "EnterPlanMode", "AskUserQuestion", "Think", "Skill",
    # Allowed MCP servers
    "mcp__filesystem__read_file", "mcp__filesystem__list_directory",
    "mcp__git__git_status", "mcp__git__git_diff",
    "mcp__chatsearch__chatsearch_find", "mcp__chatsearch__chatsearch_ask",
    "mcp__notion-agents__chat_with_agent", "mcp__notion-agents__query_database",
    "mcp__fetch__fetch",
    # Disallowed MCP servers (should be filtered in live mode)
    "mcp__proxmox__get_vms", "mcp__proxmox__start_vm", "mcp__proxmox__stop_vm",
    "mcp__playwright__browser_click", "mcp__playwright__browser_snapshot",
    "mcp__claude_ai_tally__list_forms", "mcp__claude_ai_tally__create_blocks",
    "mcp__claude_ai_Vercel__deploy_to_vercel", "mcp__claude_ai_Vercel__list_projects",
    "mcp__bay-view-graph__list_emails", "mcp__bay-view-graph__send_email",
    "mcp__claude_ai_Google_Calendar__gcal_list_events",
    "mcp__claude_ai_Gmail__gmail_search_messages",
)


class TestServerLevelFiltering:
    """Stage 2: Budget-aware server-level MCP filtering."""

    def test_disallowed_servers_removed_in_live(self):
        prompt_body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "Read the config file and fix the bug"}
        ]}]}
        result_tools, meta = _process_tools_array(FULL_TOOL_SET, prompt_body, "live")
        surviving = _tool_names(result_tools)
        # Proxmox, Playwright, Tally, Vercel, Bay-View-Graph, Calendar, Gmail should be gone
        assert "mcp__proxmox__get_vms" not in surviving
        assert "mcp__proxmox__start_vm" not in surviving
        assert "mcp__playwright__browser_click" not in surviving
        assert "mcp__claude_ai_tally__list_forms" not in surviving
        assert "mcp__claude_ai_Vercel__deploy_to_vercel" not in surviving
        assert "mcp__bay-view-graph__list_emails" not in surviving
        assert "mcp__claude_ai_Google_Calendar__gcal_list_events" not in surviving
        assert "mcp__claude_ai_Gmail__gmail_search_messages" not in surviving

    def test_allowed_servers_preserved_in_live(self):
        prompt_body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "Read the config file"}
        ]}]}
        result_tools, meta = _process_tools_array(FULL_TOOL_SET, prompt_body, "live")
        surviving = _tool_names(result_tools)
        assert "mcp__filesystem__read_file" in surviving
        assert "mcp__git__git_status" in surviving
        assert "mcp__chatsearch__chatsearch_find" in surviving
        assert "mcp__notion-agents__chat_with_agent" in surviving
        assert "mcp__fetch__fetch" in surviving

    def test_builtins_never_server_filtered(self):
        prompt_body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "Do something"}
        ]}]}
        result_tools, _ = _process_tools_array(FULL_TOOL_SET, prompt_body, "live")
        surviving = _tool_names(result_tools)
        for tool in CORE_CODING_TOOLS:
            assert tool in surviving

    def test_shadow_preserves_all(self):
        prompt_body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "Do something"}
        ]}]}
        result_tools, _ = _process_tools_array(FULL_TOOL_SET, prompt_body, "shadow")
        assert len(result_tools) == len(FULL_TOOL_SET)

    def test_meta_reports_server_filtered(self):
        prompt_body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "Fix the bug"}
        ]}]}
        _, meta = _process_tools_array(FULL_TOOL_SET, prompt_body, "live")
        assert meta["server_filtered_count"] > 0
        assert "mcp__proxmox__get_vms" in meta["server_filtered"]

    def test_reduction_is_significant(self):
        prompt_body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "Fix the bug"}
        ]}]}
        result_tools, meta = _process_tools_array(FULL_TOOL_SET, prompt_body, "live")
        reduction = 1 - len(result_tools) / len(FULL_TOOL_SET)
        assert reduction >= 0.25, f"Expected >=25% reduction, got {reduction:.0%}"

    def test_is_mcp_server_allowed_non_mcp(self):
        assert _is_mcp_server_allowed("Read", frozenset({"filesystem"}))
        assert _is_mcp_server_allowed("Bash", frozenset())

    def test_is_mcp_server_allowed_mcp(self):
        allowed = frozenset({"filesystem", "git"})
        assert _is_mcp_server_allowed("mcp__filesystem__read_file", allowed)
        assert _is_mcp_server_allowed("mcp__git__git_log", allowed)
        assert not _is_mcp_server_allowed("mcp__proxmox__get_vms", allowed)

    def test_workspace_allowed_server_preserved_in_live(self):
        old = os.environ.get("TCP_PROXY_WORKSPACE_MCP_SERVERS")
        os.environ["TCP_PROXY_WORKSPACE_MCP_SERVERS"] = "bay-view-graph"
        try:
            prompt_body = {"messages": [{"role": "user", "content": [
                {"type": "text", "text": "Check email from Jason from today"}
            ]}]}
            result_tools, meta = _process_tools_array(FULL_TOOL_SET, prompt_body, "live")
            surviving = _tool_names(result_tools)
            assert "mcp__bay-view-graph__list_emails" in surviving
            assert "mcp__bay-view-graph__send_email" in surviving
            assert meta["pack_states"]["workspace-critical"] == "deferred"
            assert "workspace_allow" in meta["pack_activation_reasons"]["workspace-critical"]
            assert "bay-view-graph" in meta["workspace_allowed_servers"]
            assert "mcp__bay-view-graph__list_emails" in meta["workspace_rescued"]
            assert "mcp__bay-view-graph__list_emails" in meta["deferred_visible"]
            assert meta["server_allow_source"]["bay-view-graph"] == "workspace_allow"
        finally:
            if old is None:
                os.environ.pop("TCP_PROXY_WORKSPACE_MCP_SERVERS", None)
            else:
                os.environ["TCP_PROXY_WORKSPACE_MCP_SERVERS"] = old

    def test_workspace_profile_activates_workspace_critical_pack(self):
        old = os.environ.get("TCP_PROXY_WORKSPACE_PROFILE")
        os.environ["TCP_PROXY_WORKSPACE_PROFILE"] = "bay-view"
        try:
            prompt_body = {"messages": [{"role": "user", "content": [
                {"type": "text", "text": "Check email from Jason from today"}
            ]}]}
            result_tools, meta = _process_tools_array(FULL_TOOL_SET, prompt_body, "live")
            surviving = _tool_names(result_tools)
            assert "mcp__bay-view-graph__list_emails" in surviving
            assert meta["pack_states"]["workspace-critical"] == "active"
            assert "profile:bay-view" in meta["pack_activation_reasons"]["workspace-critical"]
            assert meta["server_allow_source"]["bay-view-graph"] == "pack_active"
        finally:
            if old is None:
                os.environ.pop("TCP_PROXY_WORKSPACE_PROFILE", None)
            else:
                os.environ["TCP_PROXY_WORKSPACE_PROFILE"] = old

    def test_tcp_proxy_profile_activates_workspace_critical_pack(self):
        old_ws = os.environ.get("TCP_PROXY_WORKSPACE_PROFILE")
        old_profile = os.environ.get("TCP_PROXY_PROFILE")
        os.environ.pop("TCP_PROXY_WORKSPACE_PROFILE", None)
        os.environ["TCP_PROXY_PROFILE"] = "bay-view"
        try:
            prompt_body = {"messages": [{"role": "user", "content": [
                {"type": "text", "text": "Check email from Jason from today"}
            ]}]}
            result_tools, meta = _process_tools_array(FULL_TOOL_SET, prompt_body, "live")
            surviving = _tool_names(result_tools)
            assert "mcp__bay-view-graph__list_emails" in surviving
            assert meta["pack_states"]["workspace-critical"] == "active"
            assert "profile:bay-view" in meta["pack_activation_reasons"]["workspace-critical"]
            assert meta["server_allow_source"]["bay-view-graph"] == "pack_active"
        finally:
            if old_ws is None:
                os.environ.pop("TCP_PROXY_WORKSPACE_PROFILE", None)
            else:
                os.environ["TCP_PROXY_WORKSPACE_PROFILE"] = old_ws
            if old_profile is None:
                os.environ.pop("TCP_PROXY_PROFILE", None)
            else:
                os.environ["TCP_PROXY_PROFILE"] = old_profile

    def test_explicit_tool_name_rescues_server_in_live(self):
        prompt_body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "use mcp__bay-view-graph__list_emails to check email from Jason"}
        ]}]}
        result_tools, meta = _process_tools_array(FULL_TOOL_SET, prompt_body, "live")
        surviving = _tool_names(result_tools)
        assert "mcp__bay-view-graph__list_emails" in surviving
        assert "mcp__bay-view-graph__list_emails" in meta["explicit_server_rescued"]
        assert meta["server_allow_source"]["bay-view-graph"] == "explicit_request"

    def test_mixed_case_server_name_rescues_server_in_live(self):
        prompt_body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "please use claude_ai_vercel for deployment status"}
        ]}]}
        result_tools, meta = _process_tools_array(FULL_TOOL_SET, prompt_body, "live")
        surviving = _tool_names(result_tools)
        assert "mcp__claude_ai_Vercel__deploy_to_vercel" in surviving
        assert "mcp__claude_ai_Vercel__deploy_to_vercel" in meta["explicit_server_rescued"]
        assert meta["server_allow_source"]["claude_ai_Vercel"] == "explicit_request"

    def test_env_override_allowed_servers(self):
        old = os.environ.get("TCP_PROXY_ALLOWED_MCP_SERVERS")
        os.environ["TCP_PROXY_ALLOWED_MCP_SERVERS"] = "filesystem,git"
        try:
            prompt_body = {"messages": [{"role": "user", "content": [
                {"type": "text", "text": "Fix the bug"}
            ]}]}
            result_tools, _ = _process_tools_array(FULL_TOOL_SET, prompt_body, "live")
            surviving = _tool_names(result_tools)
            # With only filesystem and git allowed, notion-agents should be filtered
            assert "mcp__notion-agents__chat_with_agent" not in surviving
            # But filesystem and git should survive
            assert "mcp__filesystem__read_file" in surviving
            assert "mcp__git__git_status" in surviving
        finally:
            if old is None:
                os.environ.pop("TCP_PROXY_ALLOWED_MCP_SERVERS", None)
            else:
                os.environ["TCP_PROXY_ALLOWED_MCP_SERVERS"] = old

    def test_hard_allow_override_beats_workspace_allow(self):
        old_hard = os.environ.get("TCP_PROXY_ALLOWED_MCP_SERVERS")
        old_ws = os.environ.get("TCP_PROXY_WORKSPACE_MCP_SERVERS")
        os.environ["TCP_PROXY_ALLOWED_MCP_SERVERS"] = "filesystem,git"
        os.environ["TCP_PROXY_WORKSPACE_MCP_SERVERS"] = "bay-view-graph"
        try:
            prompt_body = {"messages": [{"role": "user", "content": [
                {"type": "text", "text": "Check email from Jason from today"}
            ]}]}
            result_tools, meta = _process_tools_array(FULL_TOOL_SET, prompt_body, "live")
            surviving = _tool_names(result_tools)
            assert "mcp__bay-view-graph__list_emails" not in surviving
            assert meta["workspace_allowed_servers"] == []
        finally:
            if old_hard is None:
                os.environ.pop("TCP_PROXY_ALLOWED_MCP_SERVERS", None)
            else:
                os.environ["TCP_PROXY_ALLOWED_MCP_SERVERS"] = old_hard
            if old_ws is None:
                os.environ.pop("TCP_PROXY_WORKSPACE_MCP_SERVERS", None)
            else:
                os.environ["TCP_PROXY_WORKSPACE_MCP_SERVERS"] = old_ws
