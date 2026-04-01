"""TCP harness primitives for descriptor-native tool routing."""

from .audit import AuditEntry, GatingDecision
from .benchmark import (
    BenchmarkComparison,
    BenchmarkSuiteResult,
    BenchmarkTask,
    benchmark_exposure_paths,
    benchmark_exposure_suite,
    build_mt2_fixture_set,
    summarize_comparisons,
)
from .gating import GateResult, RuntimeEnvironment, gate_tools
from .models import ToolRecord, ToolSelectionRequest
from .normalize import (
    normalize_binary_descriptor,
    normalize_capability_descriptor,
    normalize_legacy_tcp_descriptor,
)
from .projection import project_tool, project_tools
from .router import route_tool

__all__ = [
    "AuditEntry",
    "BenchmarkComparison",
    "BenchmarkSuiteResult",
    "BenchmarkTask",
    "GatingDecision",
    "GateResult",
    "RuntimeEnvironment",
    "ToolRecord",
    "ToolSelectionRequest",
    "normalize_binary_descriptor",
    "normalize_capability_descriptor",
    "normalize_legacy_tcp_descriptor",
    "project_tool",
    "project_tools",
    "gate_tools",
    "route_tool",
    "benchmark_exposure_paths",
    "benchmark_exposure_suite",
    "build_mt2_fixture_set",
    "summarize_comparisons",
]
