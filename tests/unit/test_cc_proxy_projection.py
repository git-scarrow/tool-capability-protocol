"""Tests for Anthropic tool → ToolRecord projection tiers."""

from __future__ import annotations

from tcp.proxy.projection import ProjectionTier, project_single_anthropic_tool


def test_static_tier_read() -> None:
    rec, tier = project_single_anthropic_tool(
        {"name": "Read", "description": "x", "input_schema": {"type": "object"}},
    )
    assert tier == ProjectionTier.STATIC
    assert rec.tool_name == "Read"
    assert rec.capability_flags != 0


def test_fallback_tier_empty_description() -> None:
    rec, tier = project_single_anthropic_tool(
        {"name": "mcp_custom_unknown_123", "description": "", "input_schema": {}},
    )
    assert tier == ProjectionTier.FALLBACK
    assert rec.capability_flags == 0


def test_description_tier_infers_network() -> None:
    rec, tier = project_single_anthropic_tool(
        {
            "name": "custom_fetch_tool",
            "description": "Fetches content from https://example.com API",
            "input_schema": {},
        },
    )
    assert tier == ProjectionTier.DESCRIPTION
    assert rec.capability_flags != 0
