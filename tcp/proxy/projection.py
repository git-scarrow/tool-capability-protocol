"""Anthropic tool defs → ToolRecord with static / description / fallback tiers."""

from __future__ import annotations

from enum import IntEnum
from typing import Any, Mapping

from tcp.derivation.request_derivation import derive_capability_flags_from_description
from tcp.harness.models import ToolRecord
from tcp.proxy.tool_flag_map import STATIC_FLAG_BY_NAME


class ProjectionTier(IntEnum):
    STATIC = 1
    DESCRIPTION = 2
    FALLBACK = 3


def _tool_description(tool: Mapping[str, Any]) -> str:
    d = tool.get("description")
    if isinstance(d, str):
        return d
    return ""


def _tool_name(tool: Mapping[str, Any]) -> str:
    n = tool.get("name")
    return str(n) if n is not None else ""


def project_single_anthropic_tool(
    tool: Mapping[str, Any],
) -> tuple[ToolRecord, ProjectionTier]:
    name = _tool_name(tool)
    desc = _tool_description(tool)

    if name in STATIC_FLAG_BY_NAME:
        flags = STATIC_FLAG_BY_NAME[name]
        tier = ProjectionTier.STATIC
    else:
        inferred = derive_capability_flags_from_description(desc)
        if inferred:
            flags = inferred
            tier = ProjectionTier.DESCRIPTION
        else:
            flags = 0
            tier = ProjectionTier.FALLBACK

    return (
        ToolRecord(
            tool_name=name or "unknown",
            descriptor_source="tcp_cc_proxy_v1",
            descriptor_version="1",
            capability_flags=flags,
            risk_level="safe",
            rich_metadata={"projection_tier": tier.name},
        ),
        tier,
    )


def project_anthropic_tools(
    tools: list[Mapping[str, Any]] | None,
) -> tuple[list[ToolRecord], list[ProjectionTier]]:
    if not tools:
        return [], []
    records: list[ToolRecord] = []
    tiers: list[ProjectionTier] = []
    for t in tools:
        if not isinstance(t, Mapping):
            continue
        rec, tier = project_single_anthropic_tool(t)
        records.append(rec)
        tiers.append(tier)
    return records, tiers
