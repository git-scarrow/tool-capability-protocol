"""Compact model-visible projections for approved tools."""

from __future__ import annotations

from typing import Iterable

from tcp.core.descriptors import CapabilityFlags

from .models import ToolRecord


def project_tool(tool: ToolRecord) -> dict[str, object]:
    """Project a ToolRecord into a compact prompt-facing representation."""
    constraints = []
    if tool.permission_level not in {"unknown", "read_only"}:
        constraints.append(f"permission:{tool.permission_level}")
    if tool.risk_level not in {"safe", "low_risk"}:
        constraints.append(f"risk:{tool.risk_level}")
    if tool.capability_flags & CapabilityFlags.SUPPORTS_NETWORK:
        constraints.append("network")
    if tool.capability_flags & CapabilityFlags.SUPPORTS_FILES:
        constraints.append("files")

    summary_parts = []
    if tool.commands:
        summary_parts.append(f"commands={','.join(sorted(tool.commands))}")
    if tool.input_formats:
        summary_parts.append(f"in={','.join(sorted(tool.input_formats))}")
    if tool.output_formats:
        summary_parts.append(f"out={','.join(sorted(tool.output_formats))}")

    return {
        "tool_name": tool.tool_name,
        "summary": "; ".join(summary_parts) if summary_parts else "descriptor-only tool",
        "commands": sorted(tool.commands),
        "input_formats": sorted(tool.input_formats),
        "output_formats": sorted(tool.output_formats),
        "processing_modes": sorted(tool.processing_modes),
        "constraints": constraints,
        "approval_required": tool.risk_level
        in {"critical", "high_risk", "approval_required", "unknown"},
    }


def project_tools(tools: Iterable[ToolRecord]) -> list[dict[str, object]]:
    """Project a sequence of tools for prompt construction."""
    return [project_tool(tool) for tool in tools]
