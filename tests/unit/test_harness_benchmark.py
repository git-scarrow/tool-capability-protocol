"""Tests for the first harness measurement track."""

from tcp.core.descriptors import (
    CapabilityDescriptor,
    CapabilityFlags,
    CommandDescriptor,
    FormatDescriptor,
    FormatType,
    PerformanceMetrics,
    ProcessingMode,
)
from tcp.harness.benchmark import BenchmarkTask, benchmark_exposure_paths, summarize_comparisons
from tcp.harness.gating import RuntimeEnvironment
from tcp.harness.models import ToolSelectionRequest


def _descriptor(
    *,
    name: str,
    command: str,
    input_format: str = "json",
    output_format: str = "json",
    capability_flags: int = 0,
    latency_ms: int = 10,
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        name=name,
        version="1.0",
        commands=[CommandDescriptor(name=command)],
        input_formats=[FormatDescriptor(name=input_format, type=FormatType.JSON)],
        output_formats=[FormatDescriptor(name=output_format, type=FormatType.JSON)],
        processing_modes=[ProcessingMode.SYNC],
        capability_flags=capability_flags,
        performance=PerformanceMetrics(
            avg_processing_time_ms=latency_ms,
            memory_usage_mb=8,
        ),
    )


def test_benchmark_exposure_paths_reduces_prompt_surface():
    descriptors = [
        _descriptor(name="fast-json", command="transform", latency_ms=5),
        _descriptor(name="slow-json", command="transform", latency_ms=50),
        _descriptor(
            name="curl-json",
            command="fetch",
            capability_flags=int(CapabilityFlags.SUPPORTS_NETWORK),
            latency_ms=15,
        ),
    ]
    tasks = [
        BenchmarkTask(
            name="json transform",
            request=ToolSelectionRequest.from_kwargs(
                required_commands={"transform"},
                required_input_formats={"json"},
                preferred_criteria="speed",
                require_auto_approval=False,
            ),
            expected_tool_names=frozenset({"fast-json"}),
        )
    ]

    comparisons = benchmark_exposure_paths(
        descriptors,
        tasks,
        RuntimeEnvironment(installed_tools=frozenset({"fast-json", "slow-json", "curl-json"})),
    )

    comparison = comparisons[0]
    assert comparison.tcp_projection.prompt_bytes < comparison.schema_heavy.prompt_bytes
    assert comparison.tcp_projection.task_satisfied is True
    assert comparison.schema_heavy.task_satisfied is True
    assert comparison.tcp_projection.selected_tool_name == "fast-json"


def test_benchmark_exposure_paths_preserves_environment_filtering():
    descriptors = [
        _descriptor(
            name="curl-json",
            command="fetch",
            capability_flags=int(CapabilityFlags.SUPPORTS_NETWORK),
            latency_ms=15,
        ),
        _descriptor(name="local-json", command="transform", latency_ms=12),
    ]
    tasks = [
        BenchmarkTask(
            name="offline transform",
            request=ToolSelectionRequest.from_kwargs(
                required_commands={"transform"},
                require_auto_approval=False,
            ),
            expected_tool_names=frozenset({"local-json"}),
        )
    ]

    comparisons = benchmark_exposure_paths(
        descriptors,
        tasks,
        RuntimeEnvironment(
            network_enabled=False,
            installed_tools=frozenset({"curl-json", "local-json"}),
        ),
    )

    comparison = comparisons[0]
    assert comparison.tcp_projection.approved_tool_count == 1
    assert comparison.schema_heavy.approved_tool_count == 1
    assert comparison.tcp_projection.selected_tool_name == "local-json"
    assert comparison.schema_heavy.selected_tool_name == "local-json"


def test_summarize_comparisons_counts_satisfied_tasks():
    descriptors = [_descriptor(name="fast-json", command="transform", latency_ms=5)]
    tasks = [
        BenchmarkTask(
            name="json transform",
            request=ToolSelectionRequest.from_kwargs(
                required_commands={"transform"},
                require_auto_approval=False,
            ),
            expected_tool_names=frozenset({"fast-json"}),
        )
    ]

    comparisons = benchmark_exposure_paths(
        descriptors,
        tasks,
        RuntimeEnvironment(installed_tools=frozenset({"fast-json"})),
    )
    summary = summarize_comparisons(comparisons)

    assert summary["task_count"] == 1
    assert summary["tcp_tasks_satisfied"] == 1
    assert summary["schema_tasks_satisfied"] == 1
