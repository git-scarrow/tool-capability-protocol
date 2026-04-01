"""Benchmark the schema-heavy exposure path against the TCP harness path."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from tcp.core.descriptors import CapabilityDescriptor, FormatType, ProcessingMode

from .gating import RuntimeEnvironment, gate_tools
from .models import ToolRecord, ToolSelectionRequest
from .normalize import normalize_capability_descriptor
from .projection import project_tools
from .router import route_tool


@dataclass(frozen=True)
class BenchmarkTask:
    """Task definition used for the local exposure-path benchmark."""

    name: str
    request: ToolSelectionRequest
    expected_tool_names: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class ExposureMetrics:
    """Metrics for one representation path."""

    exposure_name: str
    prompt_bytes: int
    prompt_chars: int
    approved_tool_count: int
    rejected_tool_count: int
    approval_required_tool_count: int
    gating_latency_ms: float
    selection_latency_ms: float
    selected_tool_name: str | None
    task_satisfied: bool


@dataclass(frozen=True)
class BenchmarkComparison:
    """Side-by-side exposure metrics for a single task."""

    task_name: str
    schema_heavy: ExposureMetrics
    tcp_projection: ExposureMetrics

    @property
    def prompt_bytes_reduction(self) -> int:
        """Return prompt-byte reduction from schema-heavy to TCP projection."""
        return self.schema_heavy.prompt_bytes - self.tcp_projection.prompt_bytes

    @property
    def gating_latency_delta_ms(self) -> float:
        """Return latency reduction from schema-heavy to TCP projection."""
        return self.schema_heavy.gating_latency_ms - self.tcp_projection.gating_latency_ms


def benchmark_exposure_paths(
    descriptors: Sequence[CapabilityDescriptor],
    tasks: Sequence[BenchmarkTask],
    environment: RuntimeEnvironment,
) -> list[BenchmarkComparison]:
    """Benchmark the schema-heavy and TCP projection exposure paths."""
    comparisons: list[BenchmarkComparison] = []
    normalized_records = [normalize_capability_descriptor(descriptor) for descriptor in descriptors]

    for task in tasks:
        schema_metrics = _benchmark_schema_heavy(descriptors, task, environment)
        tcp_metrics = _benchmark_tcp_projection(normalized_records, task, environment)
        comparisons.append(
            BenchmarkComparison(
                task_name=task.name,
                schema_heavy=schema_metrics,
                tcp_projection=tcp_metrics,
            )
        )

    return comparisons


def summarize_comparisons(comparisons: Sequence[BenchmarkComparison]) -> dict[str, float | int]:
    """Summarize results across benchmark tasks."""
    if not comparisons:
        return {
            "task_count": 0,
            "mean_prompt_bytes_reduction": 0,
            "mean_gating_latency_delta_ms": 0.0,
            "tcp_tasks_satisfied": 0,
            "schema_tasks_satisfied": 0,
        }

    return {
        "task_count": len(comparisons),
        "mean_prompt_bytes_reduction": int(
            sum(item.prompt_bytes_reduction for item in comparisons) / len(comparisons)
        ),
        "mean_gating_latency_delta_ms": (
            sum(item.gating_latency_delta_ms for item in comparisons) / len(comparisons)
        ),
        "tcp_tasks_satisfied": sum(
            1 for item in comparisons if item.tcp_projection.task_satisfied
        ),
        "schema_tasks_satisfied": sum(
            1 for item in comparisons if item.schema_heavy.task_satisfied
        ),
    }


def _benchmark_schema_heavy(
    descriptors: Sequence[CapabilityDescriptor],
    task: BenchmarkTask,
    environment: RuntimeEnvironment,
) -> ExposureMetrics:
    """Benchmark the schema-heavy path by serializing and reparsing JSON."""
    start = time.perf_counter()
    payload = json.dumps(
        [_schema_snapshot(descriptor) for descriptor in descriptors],
        separators=(",", ":"),
    )
    parsed_payload = json.loads(payload)
    approved: list[dict] = []
    rejected = 0
    approval_required = 0

    for item in parsed_payload:
        decision = _gate_schema_heavy_item(item, task.request, environment)
        if decision == "approved":
            approved.append(item)
        elif decision == "approval_required":
            approval_required += 1
        else:
            rejected += 1

    gating_latency_ms = (time.perf_counter() - start) * 1000

    selection_start = time.perf_counter()
    selected = _select_schema_heavy_tool(approved, task.request)
    selection_latency_ms = (time.perf_counter() - selection_start) * 1000
    selected_name = selected["name"] if selected else None

    return ExposureMetrics(
        exposure_name="schema-heavy",
        prompt_bytes=len(payload.encode("utf-8")),
        prompt_chars=len(payload),
        approved_tool_count=len(approved),
        rejected_tool_count=rejected,
        approval_required_tool_count=approval_required,
        gating_latency_ms=gating_latency_ms,
        selection_latency_ms=selection_latency_ms,
        selected_tool_name=selected_name,
        task_satisfied=_task_satisfied(selected_name, task.expected_tool_names),
    )


def _benchmark_tcp_projection(
    records: Sequence[ToolRecord],
    task: BenchmarkTask,
    environment: RuntimeEnvironment,
) -> ExposureMetrics:
    """Benchmark the TCP path with normalization already off the hot path."""
    start = time.perf_counter()
    gate_result = gate_tools(records, task.request, environment)
    projected = json.dumps(project_tools(gate_result.approved_tools), separators=(",", ":"))
    gating_latency_ms = (time.perf_counter() - start) * 1000

    selection_start = time.perf_counter()
    routed = route_tool(list(records), task.request, environment)
    selection_latency_ms = (time.perf_counter() - selection_start) * 1000
    selected_name = routed.selected_tool.tool_name if routed.selected_tool else None

    return ExposureMetrics(
        exposure_name="tcp-projection",
        prompt_bytes=len(projected.encode("utf-8")),
        prompt_chars=len(projected),
        approved_tool_count=len(gate_result.approved_tools),
        rejected_tool_count=len(gate_result.rejected_tools),
        approval_required_tool_count=len(gate_result.approval_required_tools),
        gating_latency_ms=gating_latency_ms,
        selection_latency_ms=selection_latency_ms,
        selected_tool_name=selected_name,
        task_satisfied=_task_satisfied(selected_name, task.expected_tool_names),
    )


def _gate_schema_heavy_item(
    item: dict,
    request: ToolSelectionRequest,
    environment: RuntimeEnvironment,
) -> str:
    """Apply the benchmark's schema-heavy gate using parsed JSON content."""
    name = item["name"]
    if environment.installed_tools and name not in environment.installed_tools:
        return "rejected"

    commands = {command["name"] for command in item.get("commands", [])}
    if request.required_commands and not request.required_commands.issubset(commands):
        return "rejected"

    input_formats = _schema_format_names(item.get("input_formats", []))
    if request.required_input_formats and not request.required_input_formats.issubset(
        input_formats
    ):
        return "rejected"

    output_formats = _schema_format_names(item.get("output_formats", []))
    if request.required_output_formats and not request.required_output_formats.issubset(
        output_formats
    ):
        return "rejected"

    processing_modes = {
        _processing_mode_name(mode) for mode in item.get("processing_modes", [])
    }
    if request.required_processing_modes and not request.required_processing_modes.issubset(
        processing_modes
    ):
        return "rejected"

    capability_flags = item.get("capability_flags", 0)
    if request.required_capability_flags and (
        capability_flags & request.required_capability_flags
    ) != request.required_capability_flags:
        return "rejected"

    if environment.file_access_enabled is False and _has_file_input(item):
        return "rejected"

    if (
        environment.network_enabled is False
        and capability_flags
        and capability_flags & (1 << 2)
    ):
        return "rejected"

    if request.require_auto_approval and _schema_requires_approval(item):
        return "approval_required"

    return "approved"


def _select_schema_heavy_tool(
    approved: Iterable[dict], request: ToolSelectionRequest
) -> dict | None:
    """Select the best schema-heavy candidate under the benchmark rules."""
    approved_list = list(approved)
    if not approved_list:
        return None

    if request.preferred_criteria == "memory":
        return min(
            approved_list,
            key=lambda item: item.get("performance", {}).get("memory_usage_mb", 512),
        )

    return min(
        approved_list,
        key=lambda item: item.get("performance", {}).get("avg_processing_time_ms", 1000),
    )


def _schema_format_names(formats: Iterable[dict]) -> set[str]:
    names: set[str] = set()
    for item in formats:
        name = item.get("name")
        if name:
            names.add(str(name).lower())
        for extension in item.get("extensions", []):
            names.add(str(extension).lower())
    return names


def _processing_mode_name(mode: int | str) -> str:
    if isinstance(mode, str):
        return mode.lower()
    try:
        return ProcessingMode(mode).name.lower()
    except ValueError:
        return str(mode).lower()


def _has_file_input(item: dict) -> bool:
    return any(
        format_item.get("type") == FormatType.BINARY.value for format_item in item.get("input_formats", [])
    )


def _schema_requires_approval(item: dict) -> bool:
    capability_flags = item.get("capability_flags", 0)
    if capability_flags & (1 << 2):
        return True

    dependency_names = {str(dep).lower() for dep in item.get("dependencies", [])}
    if {"sudo", "ssh"} & dependency_names:
        return True

    return False


def _task_satisfied(selected_name: str | None, expected_tool_names: frozenset[str]) -> bool:
    if not expected_tool_names:
        return selected_name is not None
    return selected_name in expected_tool_names


def _schema_snapshot(descriptor: CapabilityDescriptor) -> dict[str, object]:
    """Materialize a stable schema-heavy representation for benchmarking."""
    return {
        "name": descriptor.name,
        "version": descriptor.version,
        "description": descriptor.description,
        "commands": [
            {
                "name": command.name,
                "description": command.description,
                "parameters": [
                    {
                        "name": parameter.name,
                        "type": int(parameter.type),
                        "required": parameter.required,
                    }
                    for parameter in command.parameters
                ],
            }
            for command in descriptor.commands
        ],
        "input_formats": [
            {
                "name": format_item.name,
                "type": int(format_item.type),
                "extensions": list(format_item.extensions),
            }
            for format_item in descriptor.input_formats
        ],
        "output_formats": [
            {
                "name": format_item.name,
                "type": int(format_item.type),
                "extensions": list(format_item.extensions),
            }
            for format_item in descriptor.output_formats
        ],
        "processing_modes": [int(mode) for mode in descriptor.processing_modes],
        "dependencies": list(descriptor.dependencies),
        "capability_flags": descriptor.capability_flags or descriptor.get_capability_flags(),
        "performance": {
            "avg_processing_time_ms": descriptor.performance.avg_processing_time_ms,
            "memory_usage_mb": descriptor.performance.memory_usage_mb,
        },
    }
