"""Benchmark the schema-heavy exposure path against the TCP harness path."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from tcp.core.descriptors import (
    CapabilityDescriptor,
    CommandDescriptor,
    FormatDescriptor,
    FormatType,
    PerformanceMetrics,
    ProcessingMode,
)

from .bitmask_filter import EnvironmentMask, bitmask_filter
from .gating import RuntimeEnvironment, gate_tools
from .models import ToolRecord, ToolSelectionRequest
from .normalize import normalize_capability_descriptor
from .projection import project_tools
from .router import route_tool, route_tool_legacy


@dataclass(frozen=True)
class BenchmarkTask:
    """Task definition used for the local exposure-path benchmark."""

    name: str
    request: ToolSelectionRequest
    expected_tool_names: frozenset[str] = field(default_factory=frozenset)
    expected_approved_tool_names: frozenset[str] = field(default_factory=frozenset)
    expected_approval_required_tool_names: frozenset[str] = field(default_factory=frozenset)
    expected_rejected_tool_names: frozenset[str] = field(default_factory=frozenset)


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
    false_allow_count: int
    false_rejection_count: int


@dataclass(frozen=True)
class BenchmarkComparison:
    """Side-by-side exposure metrics for a single task."""

    task_name: str
    schema_heavy: ExposureMetrics
    tcp_projection: ExposureMetrics
    tcp_bitmask: ExposureMetrics | None = None

    @property
    def prompt_bytes_reduction(self) -> int:
        """Return prompt-byte reduction from schema-heavy to TCP projection."""
        return self.schema_heavy.prompt_bytes - self.tcp_projection.prompt_bytes

    @property
    def gating_latency_delta_ms(self) -> float:
        """Return latency reduction from schema-heavy to TCP projection."""
        return self.schema_heavy.gating_latency_ms - self.tcp_projection.gating_latency_ms


@dataclass(frozen=True)
class BenchmarkSuiteResult:
    """Aggregate result for a repeated benchmark suite."""

    comparisons: tuple[BenchmarkComparison, ...]
    summary: dict[str, float | int]


def benchmark_exposure_paths(
    descriptors: Sequence[CapabilityDescriptor],
    tasks: Sequence[BenchmarkTask],
    environment: RuntimeEnvironment,
) -> list[BenchmarkComparison]:
    """Benchmark the schema-heavy, TCP projection, and TCP bitmask exposure paths."""
    comparisons: list[BenchmarkComparison] = []
    normalized_records = [normalize_capability_descriptor(descriptor) for descriptor in descriptors]

    for task in tasks:
        schema_metrics = _benchmark_schema_heavy(descriptors, task, environment)
        tcp_metrics = _benchmark_tcp_projection(normalized_records, task, environment)
        bitmask_metrics = _benchmark_bitmask_path(normalized_records, task, environment)
        comparisons.append(
            BenchmarkComparison(
                task_name=task.name,
                schema_heavy=schema_metrics,
                tcp_projection=tcp_metrics,
                tcp_bitmask=bitmask_metrics,
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
            "bitmask_tasks_satisfied": 0,
            "tcp_false_allows": 0,
            "schema_false_allows": 0,
            "bitmask_false_allows": 0,
            "tcp_false_rejections": 0,
            "schema_false_rejections": 0,
            "bitmask_false_rejections": 0,
        }

    bitmask_items = [item for item in comparisons if item.tcp_bitmask is not None]

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
        "bitmask_tasks_satisfied": sum(
            1 for item in bitmask_items if item.tcp_bitmask.task_satisfied  # type: ignore[union-attr]
        ),
        "tcp_false_allows": sum(item.tcp_projection.false_allow_count for item in comparisons),
        "schema_false_allows": sum(item.schema_heavy.false_allow_count for item in comparisons),
        "bitmask_false_allows": sum(
            item.tcp_bitmask.false_allow_count for item in bitmask_items  # type: ignore[union-attr]
        ),
        "tcp_false_rejections": sum(
            item.tcp_projection.false_rejection_count for item in comparisons
        ),
        "schema_false_rejections": sum(
            item.schema_heavy.false_rejection_count for item in comparisons
        ),
        "bitmask_false_rejections": sum(
            item.tcp_bitmask.false_rejection_count for item in bitmask_items  # type: ignore[union-attr]
        ),
    }


def benchmark_exposure_suite(
    descriptors: Sequence[CapabilityDescriptor],
    tasks: Sequence[BenchmarkTask],
    environment: RuntimeEnvironment,
    *,
    repetitions: int = 5,
) -> BenchmarkSuiteResult:
    """Run repeated exposure comparisons and return an aggregate summary."""
    all_comparisons: list[BenchmarkComparison] = []
    for _ in range(repetitions):
        all_comparisons.extend(benchmark_exposure_paths(descriptors, tasks, environment))
    return BenchmarkSuiteResult(
        comparisons=tuple(all_comparisons),
        summary=summarize_comparisons(all_comparisons),
    )


def build_mt2_fixture_set() -> tuple[list[CapabilityDescriptor], list[BenchmarkTask], RuntimeEnvironment]:
    """Build the broader fixture set used by TCP-MT-2."""
    descriptors = [
        _make_descriptor(
            name="fast-json",
            command="transform",
            latency_ms=5,
            input_format="json",
            output_format="json",
        ),
        _make_descriptor(
            name="slow-json",
            command="transform",
            latency_ms=50,
            input_format="json",
            output_format="json",
        ),
        _make_descriptor(
            name="stream-json",
            command="transform",
            latency_ms=15,
            input_format="json",
            output_format="json",
            processing_mode=ProcessingMode.STREAM,
        ),
        _make_descriptor(
            name="file-convert",
            command="convert",
            latency_ms=12,
            input_format="blob",
            output_format="json",
            capability_flags=1 << 0,
        ),
        _make_descriptor(
            name="net-fetch",
            command="fetch",
            latency_ms=8,
            input_format="json",
            output_format="json",
            capability_flags=1 << 2,
        ),
        _make_descriptor(
            name="priv-admin",
            command="transform",
            latency_ms=4,
            input_format="json",
            output_format="json",
            dependencies=["sudo"],
        ),
    ]

    tasks = [
        BenchmarkTask(
            name="fast local transform",
            request=ToolSelectionRequest.from_kwargs(
                required_commands={"transform"},
                required_input_formats={"json"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
            expected_tool_names=frozenset({"priv-admin"}),
            expected_approved_tool_names=frozenset({"fast-json", "slow-json", "stream-json", "priv-admin"}),
            expected_rejected_tool_names=frozenset({"file-convert", "net-fetch"}),
        ),
        BenchmarkTask(
            name="offline stream transform",
            request=ToolSelectionRequest.from_kwargs(
                required_commands={"transform"},
                required_processing_modes={"stream"},
                require_auto_approval=False,
            ),
            expected_tool_names=frozenset({"stream-json"}),
            expected_approved_tool_names=frozenset({"stream-json"}),
            expected_rejected_tool_names=frozenset(
                {"fast-json", "slow-json", "file-convert", "net-fetch", "priv-admin"}
            ),
        ),
        BenchmarkTask(
            name="binary file convert",
            request=ToolSelectionRequest.from_kwargs(
                required_commands={"convert"},
                required_input_formats={"blob"},
                require_auto_approval=False,
            ),
            expected_tool_names=frozenset({"file-convert"}),
            expected_approved_tool_names=frozenset({"file-convert"}),
            expected_rejected_tool_names=frozenset(
                {"fast-json", "slow-json", "stream-json", "net-fetch", "priv-admin"}
            ),
        ),
        BenchmarkTask(
            name="auto approval guarded",
            request=ToolSelectionRequest.from_kwargs(
                required_commands={"transform"},
                required_input_formats={"json"},
                preferred_criteria="speed",
                require_auto_approval=True,
            ),
            expected_tool_names=frozenset({"fast-json"}),
            expected_approved_tool_names=frozenset({"fast-json", "slow-json", "stream-json"}),
            expected_approval_required_tool_names=frozenset({"priv-admin"}),
            expected_rejected_tool_names=frozenset({"file-convert", "net-fetch"}),
        ),
    ]

    environment = RuntimeEnvironment(
        network_enabled=False,
        file_access_enabled=True,
        stdin_enabled=True,
        installed_tools=frozenset(
            {
                "fast-json",
                "slow-json",
                "stream-json",
                "file-convert",
                "net-fetch",
                "priv-admin",
            }
        ),
    )
    return descriptors, tasks, environment


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
    approval_required_items: list[dict] = []
    rejected = 0
    approval_required = 0

    for item in parsed_payload:
        decision = _gate_schema_heavy_item(item, task.request, environment)
        if decision == "approved":
            approved.append(item)
        elif decision == "approval_required":
            approval_required_items.append(item)
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
        false_allow_count=_false_allow_count(
            approved_names={item["name"] for item in approved},
            approval_required_names={item["name"] for item in approval_required_items},
            task=task,
        ),
        false_rejection_count=_false_rejection_count(
            approved_names={item["name"] for item in approved},
            approval_required_names={item["name"] for item in approval_required_items},
            task=task,
        ),
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
    routed = route_tool_legacy(list(records), task.request, environment)
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
        false_allow_count=_false_allow_count(
            approved_names={tool.tool_name for tool in gate_result.approved_tools},
            approval_required_names={
                tool.tool_name for tool in gate_result.approval_required_tools
            },
            task=task,
        ),
        false_rejection_count=_false_rejection_count(
            approved_names={tool.tool_name for tool in gate_result.approved_tools},
            approval_required_names={
                tool.tool_name for tool in gate_result.approval_required_tools
            },
            task=task,
        ),
    )


def _benchmark_bitmask_path(
    records: Sequence[ToolRecord],
    task: BenchmarkTask,
    environment: RuntimeEnvironment,
) -> ExposureMetrics:
    """Benchmark the three-tier bitmask path."""
    from tcp.core.descriptors import CapabilityFlags as CF

    deny_mask = _environment_to_deny_mask(environment)
    approval_mask = int(CF.AUTH_REQUIRED)

    start = time.perf_counter()
    result = bitmask_filter(
        records,
        deny_mask=deny_mask,
        approval_mask=approval_mask,
        require_mask=task.request.required_capability_flags,
    )

    # Second-pass: apply non-bitmask filters (commands, formats, modes) on survivors
    approved_final: list[ToolRecord] = []
    approval_required_final: list[ToolRecord] = []
    rejected_count = result.rejection_count

    for tool in result.approved:
        if _passes_request_filters(tool, task.request):
            approved_final.append(tool)
        else:
            rejected_count += 1

    for tool in result.approval_required:
        if _passes_request_filters(tool, task.request):
            if task.request.require_auto_approval:
                approval_required_final.append(tool)
            else:
                approved_final.append(tool)
        else:
            rejected_count += 1

    projected = json.dumps(project_tools(approved_final), separators=(",", ":"))
    gating_latency_ms = (time.perf_counter() - start) * 1000

    # Selection: pick fastest approved tool
    selection_start = time.perf_counter()
    selected: ToolRecord | None = None
    if approved_final:
        if task.request.preferred_criteria == "memory":
            selected = min(approved_final, key=lambda t: t.memory_usage_mb)
        else:
            selected = min(approved_final, key=lambda t: t.avg_processing_time_ms)
    selection_latency_ms = (time.perf_counter() - selection_start) * 1000
    selected_name = selected.tool_name if selected else None

    return ExposureMetrics(
        exposure_name="tcp-bitmask",
        prompt_bytes=len(projected.encode("utf-8")),
        prompt_chars=len(projected),
        approved_tool_count=len(approved_final),
        rejected_tool_count=rejected_count,
        approval_required_tool_count=len(approval_required_final),
        gating_latency_ms=gating_latency_ms,
        selection_latency_ms=selection_latency_ms,
        selected_tool_name=selected_name,
        task_satisfied=_task_satisfied(selected_name, task.expected_tool_names),
        false_allow_count=_false_allow_count(
            approved_names={t.tool_name for t in approved_final},
            approval_required_names={t.tool_name for t in approval_required_final},
            task=task,
        ),
        false_rejection_count=_false_rejection_count(
            approved_names={t.tool_name for t in approved_final},
            approval_required_names={t.tool_name for t in approval_required_final},
            task=task,
        ),
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


def _false_allow_count(
    *, approved_names: set[str], approval_required_names: set[str], task: BenchmarkTask
) -> int:
    false_allow = 0

    for name in approved_names:
        if name not in task.expected_approved_tool_names:
            false_allow += 1

    for name in approval_required_names:
        if name in task.expected_rejected_tool_names:
            false_allow += 1

    return false_allow


def _false_rejection_count(
    *, approved_names: set[str], approval_required_names: set[str], task: BenchmarkTask
) -> int:
    false_rejection = 0

    for name in task.expected_approved_tool_names:
        if name not in approved_names:
            false_rejection += 1

    for name in task.expected_approval_required_tool_names:
        if name not in approval_required_names:
            false_rejection += 1

    for name in approval_required_names:
        if name in task.expected_approved_tool_names:
            false_rejection += 1

    return false_rejection


def _make_descriptor(
    *,
    name: str,
    command: str,
    latency_ms: int,
    input_format: str,
    output_format: str,
    capability_flags: int = 0,
    processing_mode: ProcessingMode = ProcessingMode.SYNC,
    dependencies: Sequence[str] = (),
) -> CapabilityDescriptor:
    format_type_map = {
        "json": FormatType.JSON,
        "blob": FormatType.BINARY,
        "text": FormatType.TEXT,
    }
    return CapabilityDescriptor(
        name=name,
        version="1.0",
        commands=[CommandDescriptor(name=command)],
        input_formats=[
            FormatDescriptor(name=input_format, type=format_type_map.get(input_format, FormatType.TEXT))
        ],
        output_formats=[
            FormatDescriptor(name=output_format, type=format_type_map.get(output_format, FormatType.TEXT))
        ],
        processing_modes=[processing_mode],
        dependencies=list(dependencies),
        capability_flags=capability_flags,
        performance=PerformanceMetrics(
            avg_processing_time_ms=latency_ms,
            memory_usage_mb=8,
        ),
    )


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
