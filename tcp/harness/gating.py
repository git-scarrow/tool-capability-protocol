"""Deterministic pre-prompt gating for TCP tool records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from tcp.core.descriptors import CapabilityFlags

from .audit import AuditEntry, GatingDecision
from .models import ToolRecord, ToolSelectionRequest


@dataclass(frozen=True)
class RuntimeEnvironment:
    """Execution environment constraints used before prompt construction."""

    network_enabled: bool = False
    file_access_enabled: bool = True
    stdin_enabled: bool = True
    permitted_permission_levels: frozenset[str] = frozenset(
        {"read_only", "execute_safe", "execute_full", "unknown"}
    )
    installed_tools: frozenset[str] = frozenset()


@dataclass(frozen=True)
class GateResult:
    """The filtered tool set plus the audit trail."""

    candidate_tools: tuple[ToolRecord, ...]
    approved_tools: tuple[ToolRecord, ...]
    rejected_tools: tuple[ToolRecord, ...]
    approval_required_tools: tuple[ToolRecord, ...]
    audit_log: tuple[AuditEntry, ...] = field(default_factory=tuple)


def gate_tools(
    tools: Iterable[ToolRecord],
    request: ToolSelectionRequest,
    environment: RuntimeEnvironment,
) -> GateResult:
    """Apply deterministic gating to the available tool set."""
    candidates: list[ToolRecord] = []
    approved: list[ToolRecord] = []
    rejected: list[ToolRecord] = []
    approval_required: list[ToolRecord] = []
    audit_log: list[AuditEntry] = []

    for tool in tools:
        candidates.append(tool)
        decision, reason = _gate_single_tool(tool, request, environment)
        audit_log.append(
            AuditEntry(
                tool_name=tool.tool_name,
                decision=decision,
                reason=reason,
                details={
                    "risk_level": tool.risk_level,
                    "permission_level": tool.permission_level,
                },
            )
        )

        if decision is GatingDecision.APPROVED:
            approved.append(tool)
        elif decision is GatingDecision.APPROVAL_REQUIRED:
            approval_required.append(tool)
        else:
            rejected.append(tool)

    return GateResult(
        candidate_tools=tuple(candidates),
        approved_tools=tuple(_sort_tools(approved, request.preferred_criteria)),
        rejected_tools=tuple(rejected),
        approval_required_tools=tuple(approval_required),
        audit_log=tuple(audit_log),
    )


def _gate_single_tool(
    tool: ToolRecord,
    request: ToolSelectionRequest,
    environment: RuntimeEnvironment,
) -> tuple[GatingDecision, str]:
    if environment.installed_tools and tool.tool_name not in environment.installed_tools:
        return GatingDecision.REJECTED, "tool not installed"

    if tool.permission_level not in environment.permitted_permission_levels:
        return GatingDecision.REJECTED, "permission level not allowed in environment"

    if (
        tool.capability_flags & CapabilityFlags.SUPPORTS_NETWORK
        and not environment.network_enabled
    ):
        return GatingDecision.REJECTED, "network access disabled"

    if (
        tool.capability_flags & CapabilityFlags.SUPPORTS_FILES
        and not environment.file_access_enabled
    ):
        return GatingDecision.REJECTED, "file access disabled"

    if (
        tool.capability_flags & CapabilityFlags.SUPPORTS_STDIN
        and not environment.stdin_enabled
    ):
        return GatingDecision.REJECTED, "stdin disabled"

    if request.required_commands and not request.required_commands.issubset(tool.commands):
        return GatingDecision.REJECTED, "required commands unavailable"

    if request.required_input_formats and not request.required_input_formats.issubset(
        tool.input_formats
    ):
        return GatingDecision.REJECTED, "required input formats unavailable"

    if request.required_output_formats and not request.required_output_formats.issubset(
        tool.output_formats
    ):
        return GatingDecision.REJECTED, "required output formats unavailable"

    if request.required_processing_modes and not request.required_processing_modes.issubset(
        tool.processing_modes
    ):
        return GatingDecision.REJECTED, "required processing modes unavailable"

    if request.required_capability_flags and (
        tool.capability_flags & request.required_capability_flags
    ) != request.required_capability_flags:
        return GatingDecision.REJECTED, "required capability flags unavailable"

    if tool.risk_level in {"critical", "high_risk", "approval_required", "unknown"}:
        if request.require_auto_approval:
            return GatingDecision.APPROVAL_REQUIRED, "tool requires explicit approval"

    return GatingDecision.APPROVED, "tool passed deterministic gating"


def _sort_tools(tools: list[ToolRecord], criteria: str) -> list[ToolRecord]:
    if criteria == "memory":
        return sorted(tools, key=lambda tool: tool.memory_usage_mb)
    return sorted(tools, key=lambda tool: tool.avg_processing_time_ms)
