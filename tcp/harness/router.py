"""Route task requests through bitmask gating into a single selected tool.

The primary path uses the three-tier bitmask filter (deny → approval → require)
followed by non-bitmask request filters (commands, formats, modes).  The legacy
``gate_tools``-based path is preserved as ``route_tool_legacy``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from tcp.core.descriptors import CapabilityFlags

from .audit import AuditEntry, GatingDecision
from .bitmask_filter import BitmaskFilterResult, EnvironmentMask, bitmask_filter
from .gating import GateResult, RuntimeEnvironment, gate_tools
from .models import ToolRecord, ToolSelectionRequest


@dataclass(frozen=True)
class RouteResult:
    """Result of routing a task to a single tool."""

    selected_tool: ToolRecord | None
    gate_result: GateResult | None = None
    bitmask_result: BitmaskFilterResult | None = None
    approved: tuple[ToolRecord, ...] = field(default_factory=tuple)
    approval_required: tuple[ToolRecord, ...] = field(default_factory=tuple)
    rejected: tuple[ToolRecord, ...] = field(default_factory=tuple)
    audit_log: tuple[AuditEntry, ...] = field(default_factory=tuple)


def route_tool(
    tools: list[ToolRecord],
    request: ToolSelectionRequest,
    environment: RuntimeEnvironment,
    *,
    approval_mask: int = int(CapabilityFlags.AUTH_REQUIRED),
) -> RouteResult:
    """Route a request to the best approved tool using bitmask-first gating.

    Pipeline:
      1. Pre-filter by installed_tools (name lookup, not bitmask-able).
      2. Bitmask hot path: deny_mask / approval_mask / require_mask.
      3. Cold path: non-bitmask request filters (commands, formats, modes).
      4. Select best tool from approved set by preferred_criteria.
    """
    # --- Stage 1: installed-tools pre-filter ---
    audit: list[AuditEntry] = []
    if environment.installed_tools:
        candidates = []
        for tool in tools:
            if tool.tool_name in environment.installed_tools:
                candidates.append(tool)
            else:
                audit.append(AuditEntry(
                    tool_name=tool.tool_name,
                    decision=GatingDecision.REJECTED,
                    reason="tool not installed",
                ))
    else:
        candidates = list(tools)

    # --- Stage 2: bitmask hot path ---
    deny_mask = _environment_to_deny_mask(environment)
    bitmask_result = bitmask_filter(
        candidates,
        deny_mask=deny_mask,
        approval_mask=approval_mask,
        require_mask=request.required_capability_flags,
    )

    for tool in bitmask_result.rejected:
        audit.append(AuditEntry(
            tool_name=tool.tool_name,
            decision=GatingDecision.REJECTED,
            reason="bitmask: denied capability or missing required capability",
            details={"capability_flags": tool.capability_flags, "deny_mask": bitmask_result.deny_mask},
        ))

    # --- Stage 3: non-bitmask request filters on survivors ---
    approved: list[ToolRecord] = []
    approval_required: list[ToolRecord] = []
    rejected: list[ToolRecord] = []

    for tool in bitmask_result.approved:
        if _passes_request_filters(tool, request):
            approved.append(tool)
            audit.append(AuditEntry(
                tool_name=tool.tool_name,
                decision=GatingDecision.APPROVED,
                reason="passed bitmask and request filters",
            ))
        else:
            rejected.append(tool)
            audit.append(AuditEntry(
                tool_name=tool.tool_name,
                decision=GatingDecision.REJECTED,
                reason="failed request filters (commands/formats/modes)",
            ))

    for tool in bitmask_result.approval_required:
        if _passes_request_filters(tool, request):
            if request.require_auto_approval:
                approval_required.append(tool)
                audit.append(AuditEntry(
                    tool_name=tool.tool_name,
                    decision=GatingDecision.APPROVAL_REQUIRED,
                    reason="bitmask: approval-gated capability",
                    details={"approval_mask": bitmask_result.approval_mask},
                ))
            else:
                approved.append(tool)
                audit.append(AuditEntry(
                    tool_name=tool.tool_name,
                    decision=GatingDecision.APPROVED,
                    reason="approval-gated but auto_approval not required",
                ))
        else:
            rejected.append(tool)
            audit.append(AuditEntry(
                tool_name=tool.tool_name,
                decision=GatingDecision.REJECTED,
                reason="failed request filters (commands/formats/modes)",
            ))

    # --- Stage 4: selection ---
    selected = _select_best(approved, request.preferred_criteria)

    return RouteResult(
        selected_tool=selected,
        bitmask_result=bitmask_result,
        approved=tuple(approved),
        approval_required=tuple(approval_required),
        rejected=tuple(rejected + list(bitmask_result.rejected)),
        audit_log=tuple(audit),
    )


def route_tool_legacy(
    tools: list[ToolRecord],
    request: ToolSelectionRequest,
    environment: RuntimeEnvironment,
) -> RouteResult:
    """Route using the original gate_tools path (preserved for comparison)."""
    gate_result = gate_tools(tools, request, environment)
    selected = gate_result.approved_tools[0] if gate_result.approved_tools else None
    return RouteResult(
        selected_tool=selected,
        gate_result=gate_result,
        approved=gate_result.approved_tools,
        approval_required=gate_result.approval_required_tools,
        rejected=gate_result.rejected_tools,
        audit_log=gate_result.audit_log,
    )


def _environment_to_deny_mask(environment: RuntimeEnvironment) -> EnvironmentMask:
    """Translate RuntimeEnvironment booleans into a deny bitmask."""
    return EnvironmentMask.from_constraints(
        network=environment.network_enabled,
        file_access=environment.file_access_enabled,
        stdin=environment.stdin_enabled,
    )


def _passes_request_filters(tool: ToolRecord, request: ToolSelectionRequest) -> bool:
    """Check non-bitmask request filters (commands, formats, modes)."""
    if request.required_commands and not request.required_commands.issubset(tool.commands):
        return False
    if request.required_input_formats and not request.required_input_formats.issubset(
        tool.input_formats
    ):
        return False
    if request.required_output_formats and not request.required_output_formats.issubset(
        tool.output_formats
    ):
        return False
    if request.required_processing_modes and not request.required_processing_modes.issubset(
        tool.processing_modes
    ):
        return False
    return True


def _select_best(tools: list[ToolRecord], criteria: str) -> ToolRecord | None:
    if not tools:
        return None
    if criteria == "memory":
        return min(tools, key=lambda t: t.memory_usage_mb)
    return min(tools, key=lambda t: t.avg_processing_time_ms)
