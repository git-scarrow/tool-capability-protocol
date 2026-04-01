"""Route task requests through gating into a single selected tool."""

from __future__ import annotations

from dataclasses import dataclass

from .gating import GateResult, RuntimeEnvironment, gate_tools
from .models import ToolRecord, ToolSelectionRequest


@dataclass(frozen=True)
class RouteResult:
    """Result of routing a task to a single tool."""

    selected_tool: ToolRecord | None
    gate_result: GateResult


def route_tool(
    tools: list[ToolRecord],
    request: ToolSelectionRequest,
    environment: RuntimeEnvironment,
) -> RouteResult:
    """Route a request to the best approved tool after deterministic gating."""
    gate_result = gate_tools(tools, request, environment)
    selected = gate_result.approved_tools[0] if gate_result.approved_tools else None
    return RouteResult(selected_tool=selected, gate_result=gate_result)
