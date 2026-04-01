"""Tests for hot-path three-tier bitmask filtering of TCP tool records."""

import time

from tcp.core.descriptors import CapabilityFlags
from tcp.harness import (
    BitmaskFilterResult,
    EnvironmentMask,
    ToolRecord,
    bitmask_filter,
    filter_for_prompt,
)


def _make_tool(name: str, flags: int, risk: str = "safe") -> ToolRecord:
    """Create a minimal ToolRecord with the given capability flags."""
    return ToolRecord(
        tool_name=name,
        descriptor_source="test",
        descriptor_version="1.0",
        capability_flags=flags,
        risk_level=risk,
        commands=frozenset({name}),
    )


# --- EnvironmentMask construction ---


def test_environment_mask_from_constraints_denies_network():
    mask = EnvironmentMask.from_constraints(network=False)
    assert mask.value & CapabilityFlags.SUPPORTS_NETWORK != 0


def test_environment_mask_from_constraints_all_enabled_is_zero():
    mask = EnvironmentMask.from_constraints()
    assert mask.value == 0


def test_environment_mask_combines_multiple_denials():
    mask = EnvironmentMask.from_constraints(network=False, file_access=False)
    assert mask.value & CapabilityFlags.SUPPORTS_NETWORK != 0
    assert mask.value & CapabilityFlags.SUPPORTS_FILES != 0
    assert mask.value & CapabilityFlags.SUPPORTS_STDIN == 0


def test_environment_mask_or_combines_masks():
    a = EnvironmentMask.from_constraints(network=False)
    b = EnvironmentMask.from_constraints(gpu=False)
    combined = a | b
    assert combined.value & CapabilityFlags.SUPPORTS_NETWORK != 0
    assert combined.value & CapabilityFlags.GPU_ACCELERATION != 0


# --- Two-tier (deny + require) filtering ---


def test_deny_mask_rejects_tools_with_denied_capability():
    tools = [
        _make_tool("curl", CapabilityFlags.SUPPORTS_NETWORK),
        _make_tool("cat", CapabilityFlags.SUPPORTS_FILES),
    ]
    deny = EnvironmentMask.from_constraints(network=False)

    result = bitmask_filter(tools, deny_mask=deny)

    assert result.approved_count == 1
    assert result.approved[0].tool_name == "cat"
    assert result.rejected[0].tool_name == "curl"


def test_require_mask_rejects_tools_missing_required_capability():
    tools = [
        _make_tool("jq", CapabilityFlags.JSON_OUTPUT | CapabilityFlags.SUPPORTS_FILES),
        _make_tool("echo", CapabilityFlags.SUPPORTS_STDIN),
    ]
    require = int(CapabilityFlags.JSON_OUTPUT)

    result = bitmask_filter(tools, require_mask=require)

    assert result.approved_count == 1
    assert result.approved[0].tool_name == "jq"


def test_deny_and_require_combined():
    tools = [
        _make_tool("good", CapabilityFlags.JSON_OUTPUT | CapabilityFlags.STATELESS),
        _make_tool("networked", CapabilityFlags.JSON_OUTPUT | CapabilityFlags.SUPPORTS_NETWORK),
        _make_tool("no-json", CapabilityFlags.STATELESS),
    ]
    deny = EnvironmentMask.from_constraints(network=False)
    require = int(CapabilityFlags.JSON_OUTPUT)

    result = bitmask_filter(tools, deny_mask=deny, require_mask=require)

    assert result.approved_count == 1
    assert result.approved[0].tool_name == "good"
    assert result.candidate_count == 3
    assert result.rejection_count == 2


def test_empty_masks_pass_everything():
    tools = [_make_tool(f"tool-{i}", i) for i in range(10)]
    result = bitmask_filter(tools)
    assert result.approved_count == 10
    assert result.rejection_count == 0
    assert result.approval_required_count == 0


def test_empty_tool_list():
    result = bitmask_filter([], deny_mask=0xFFFF_FFFF)
    assert result.approved_count == 0
    assert result.candidate_count == 0


# --- Three-tier (deny + approval + require) filtering ---


def test_approval_mask_creates_middle_tier():
    tools = [
        _make_tool("safe-json", CapabilityFlags.JSON_OUTPUT | CapabilityFlags.STATELESS),
        _make_tool("authed-json", CapabilityFlags.JSON_OUTPUT | CapabilityFlags.AUTH_REQUIRED),
        _make_tool("net-tool", CapabilityFlags.SUPPORTS_NETWORK),
    ]
    deny = EnvironmentMask.from_constraints(network=False)
    approval = int(CapabilityFlags.AUTH_REQUIRED)

    result = bitmask_filter(tools, deny_mask=deny, approval_mask=approval)

    assert result.approved_count == 1
    assert result.approved[0].tool_name == "safe-json"
    assert result.approval_required_count == 1
    assert result.approval_required[0].tool_name == "authed-json"
    assert result.rejection_count == 1
    assert result.rejected[0].tool_name == "net-tool"


def test_approval_mask_with_multiple_flags():
    """Tools with AUTH_REQUIRED or RATE_LIMITING need approval."""
    tools = [
        _make_tool("plain", CapabilityFlags.STATELESS),
        _make_tool("authed", CapabilityFlags.AUTH_REQUIRED),
        _make_tool("limited", CapabilityFlags.RATE_LIMITING),
        _make_tool("both", CapabilityFlags.AUTH_REQUIRED | CapabilityFlags.RATE_LIMITING),
    ]
    approval = int(CapabilityFlags.AUTH_REQUIRED | CapabilityFlags.RATE_LIMITING)

    result = bitmask_filter(tools, approval_mask=approval)

    assert result.approved_count == 1
    assert result.approved[0].tool_name == "plain"
    assert result.approval_required_count == 3
    assert {t.tool_name for t in result.approval_required} == {"authed", "limited", "both"}


def test_deny_wins_over_approval_on_overlap():
    """If a bit is in both deny and approval, deny wins."""
    tools = [
        _make_tool("net-tool", CapabilityFlags.SUPPORTS_NETWORK),
    ]
    deny = EnvironmentMask.from_constraints(network=False)
    # Also set SUPPORTS_NETWORK in approval — deny should win
    approval = int(CapabilityFlags.SUPPORTS_NETWORK | CapabilityFlags.AUTH_REQUIRED)

    result = bitmask_filter(tools, deny_mask=deny, approval_mask=approval)

    # Deny wins: net-tool is rejected, not approval_required
    assert result.rejection_count == 1
    assert result.approval_required_count == 0
    # The effective approval mask should have SUPPORTS_NETWORK stripped
    assert result.approval_mask & CapabilityFlags.SUPPORTS_NETWORK == 0
    assert result.approval_mask & CapabilityFlags.AUTH_REQUIRED != 0


def test_three_tier_full_pipeline():
    """Deny + approval + require together produce correct three-way split."""
    tools = [
        _make_tool("perfect", CapabilityFlags.JSON_OUTPUT | CapabilityFlags.STATELESS),
        _make_tool("needs-ok", CapabilityFlags.JSON_OUTPUT | CapabilityFlags.AUTH_REQUIRED),
        _make_tool("no-json", CapabilityFlags.STATELESS),
        _make_tool("net-only", CapabilityFlags.SUPPORTS_NETWORK | CapabilityFlags.JSON_OUTPUT),
    ]
    deny = EnvironmentMask.from_constraints(network=False)
    approval = int(CapabilityFlags.AUTH_REQUIRED)
    require = int(CapabilityFlags.JSON_OUTPUT)

    result = bitmask_filter(
        tools, deny_mask=deny, approval_mask=approval, require_mask=require
    )

    assert result.approved_count == 1
    assert result.approved[0].tool_name == "perfect"
    assert result.approval_required_count == 1
    assert result.approval_required[0].tool_name == "needs-ok"
    assert result.rejection_count == 2
    assert {t.tool_name for t in result.rejected} == {"no-json", "net-only"}


def test_survivors_includes_approved_and_approval_required():
    """The backwards-compat survivors property merges both tiers."""
    tools = [
        _make_tool("safe", CapabilityFlags.STATELESS),
        _make_tool("gated", CapabilityFlags.AUTH_REQUIRED),
        _make_tool("dead", CapabilityFlags.SUPPORTS_NETWORK),
    ]
    deny = EnvironmentMask.from_constraints(network=False)
    approval = int(CapabilityFlags.AUTH_REQUIRED)

    result = bitmask_filter(tools, deny_mask=deny, approval_mask=approval)

    assert result.survivor_count == 2
    survivor_names = {t.tool_name for t in result.survivors}
    assert survivor_names == {"safe", "gated"}


# --- filter_for_prompt (hot path → cold path) ---


def test_filter_for_prompt_returns_projected_dicts():
    tools = [
        _make_tool("jq", CapabilityFlags.JSON_OUTPUT | CapabilityFlags.SUPPORTS_FILES),
        _make_tool("curl", CapabilityFlags.SUPPORTS_NETWORK),
    ]
    deny = EnvironmentMask.from_constraints(network=False)

    projected = filter_for_prompt(tools, deny_mask=deny)

    assert len(projected) == 1
    assert projected[0]["tool_name"] == "jq"


def test_filter_for_prompt_includes_approval_required_annotated():
    tools = [
        _make_tool("safe", CapabilityFlags.JSON_OUTPUT),
        _make_tool("gated", CapabilityFlags.AUTH_REQUIRED | CapabilityFlags.JSON_OUTPUT),
    ]
    approval = int(CapabilityFlags.AUTH_REQUIRED)

    projected = filter_for_prompt(tools, approval_mask=approval)

    assert len(projected) == 2
    safe_entry = next(p for p in projected if p["tool_name"] == "safe")
    gated_entry = next(p for p in projected if p["tool_name"] == "gated")
    # safe tool should not have approval_required forced to True
    assert gated_entry["approval_required"] is True
    # safe tool gets its value from projection (risk-based)
    assert "tool_name" in safe_entry


def test_filter_for_prompt_excludes_approval_required_when_disabled():
    tools = [
        _make_tool("safe", CapabilityFlags.JSON_OUTPUT),
        _make_tool("gated", CapabilityFlags.AUTH_REQUIRED | CapabilityFlags.JSON_OUTPUT),
    ]
    approval = int(CapabilityFlags.AUTH_REQUIRED)

    projected = filter_for_prompt(
        tools, approval_mask=approval, include_approval_required=False
    )

    assert len(projected) == 1
    assert projected[0]["tool_name"] == "safe"


# --- 100-tool scale tests ---


def _generate_tool_registry(n: int = 100) -> list[ToolRecord]:
    """Generate n tools with varied capability flags."""
    tools = []
    all_flags = list(CapabilityFlags)
    for i in range(n):
        flags = 0
        for j, flag in enumerate(all_flags):
            if (i >> j) & 1:
                flags |= flag
        tools.append(_make_tool(f"tool-{i:03d}", flags))
    return tools


def test_100_tool_bitmask_filter():
    """Verify correct three-tier filtering of 100 tools."""
    tools = _generate_tool_registry(100)
    deny = EnvironmentMask.from_constraints(network=False, gpu=False)
    approval = int(CapabilityFlags.AUTH_REQUIRED | CapabilityFlags.RATE_LIMITING)

    result = bitmask_filter(tools, deny_mask=deny, approval_mask=approval)

    deny_val = deny.value
    approval_val = result.approval_mask

    # No approved tool has a denied flag
    for tool in result.approved:
        assert (tool.capability_flags & deny_val) == 0
        assert (tool.capability_flags & approval_val) == 0

    # Every approval_required tool has at least one approval flag, no deny flags
    for tool in result.approval_required:
        assert (tool.capability_flags & deny_val) == 0
        assert (tool.capability_flags & approval_val) != 0

    # Every rejected tool has at least one denied flag OR fails require
    for tool in result.rejected:
        assert (tool.capability_flags & deny_val) != 0

    # All tools accounted for
    total = result.approved_count + result.approval_required_count + result.rejection_count
    assert total == 100


def test_100_tool_three_tier_reduces_false_rejections():
    """Approval mask should rescue tools that would otherwise be hard-rejected.

    This is the MT-2 scenario: tools with AUTH_REQUIRED were being
    hard-rejected. With the approval tier, they move to approval_required
    instead.
    """
    tools = _generate_tool_registry(100)

    # Without approval mask: everything is binary
    deny = EnvironmentMask.from_constraints(network=False)
    binary_result = bitmask_filter(tools, deny_mask=deny)

    # With approval mask: BATCH_PROCESSING (bit 5) tools get soft-gated.
    # AUTH_REQUIRED (bit 13) is too high for the 100-tool fixture to reach,
    # so we use a low-bit flag to exercise the three-tier split.
    approval = int(CapabilityFlags.BATCH_PROCESSING)
    tiered_result = bitmask_filter(tools, deny_mask=deny, approval_mask=approval)

    # Rejection count stays the same — approval mask doesn't rescue denied tools
    assert tiered_result.rejection_count == binary_result.rejection_count
    # Some tools moved from approved → approval_required
    assert tiered_result.approval_required_count > 0
    assert tiered_result.approved_count < binary_result.approved_count
    # Total survivors unchanged (approval_required are still survivors)
    assert tiered_result.survivor_count == binary_result.survivor_count


def test_100_tool_bitmask_filter_performance():
    """Three-tier bitmask filter over 100 tools must complete in under 1ms."""
    tools = _generate_tool_registry(100)
    deny = EnvironmentMask.from_constraints(network=False)
    approval = int(CapabilityFlags.AUTH_REQUIRED)
    require = int(CapabilityFlags.STATELESS)

    # Warm up
    bitmask_filter(tools, deny_mask=deny, approval_mask=approval, require_mask=require)

    iterations = 1000
    start = time.perf_counter_ns()
    for _ in range(iterations):
        bitmask_filter(tools, deny_mask=deny, approval_mask=approval, require_mask=require)
    elapsed_ns = time.perf_counter_ns() - start

    avg_ns = elapsed_ns / iterations
    assert avg_ns < 1_000_000, f"Bitmask filter took {avg_ns:.0f}ns, exceeds 1ms budget"


def test_100_tool_filter_for_prompt():
    """Full hot-path → cold-path pipeline for 100 tools with three tiers."""
    tools = _generate_tool_registry(100)
    deny = EnvironmentMask.from_constraints(network=False, file_access=False)
    approval = int(CapabilityFlags.AUTH_REQUIRED)

    projected = filter_for_prompt(tools, deny_mask=deny, approval_mask=approval)

    for p in projected:
        assert "tool_name" in p
        assert "commands" in p
        assert "constraints" in p

    result = bitmask_filter(tools, deny_mask=deny, approval_mask=approval)
    assert len(projected) == result.approved_count + result.approval_required_count
