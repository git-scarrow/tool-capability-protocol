"""
TCP Test Configuration and Fixtures

This module provides comprehensive test configuration for the Tool Capability Protocol,
supporting unit, integration, performance, and security testing with external validation.
"""

import json

# Import TCP components for testing
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Generator, List
from unittest.mock import MagicMock, Mock

import pytest

# Ensure the repository root is importable so `import tcp` resolves consistently
# in local runs without requiring editable installation first.
sys.path.insert(0, str(Path(__file__).parent.parent))

from tcp.analysis.tcp_generator import TCPDescriptorGenerator
from tcp.core.descriptors import BinaryCapabilityDescriptor, CapabilityDescriptor
from tcp.core.protocol import ToolCapabilityProtocol
from tcp.core.registry import CapabilityRegistry


@pytest.fixture(scope="session")
def test_data_dir() -> Path:
    """Provides path to test data directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def temp_dir() -> Generator[Path, None, None]:
    """Provides temporary directory for test artifacts."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield Path(tmp_dir)


@pytest.fixture
def sample_capability_descriptor() -> CapabilityDescriptor:
    """Provides standard capability descriptor for testing."""
    from tcp.core.descriptors import (
        CommandDescriptor,
        FormatDescriptor,
        FormatType,
        ParameterDescriptor,
        ParameterType,
        PerformanceMetrics,
    )

    return CapabilityDescriptor(
        name="grep",
        description="Search text patterns in files",
        version="3.7",
        commands=[
            CommandDescriptor(
                name="grep",
                description="Search text patterns in files",
                parameters=[
                    ParameterDescriptor(
                        name="pattern",
                        type=ParameterType.STRING,
                        required=True,
                        description="Regular expression pattern to search for",
                    ),
                    ParameterDescriptor(
                        name="file",
                        type=ParameterType.STRING,
                        required=True,
                        description="File to search in",
                    ),
                    ParameterDescriptor(
                        name="ignore_case",
                        type=ParameterType.BOOLEAN,
                        required=False,
                        description="Perform case-insensitive matching",
                    ),
                ],
            )
        ],
        performance=PerformanceMetrics(
            avg_processing_time_ms=436,
            memory_usage_mb=8,
        ),
    )


@pytest.fixture
def sample_binary_descriptor() -> BinaryCapabilityDescriptor:
    """Provides binary descriptor for compression testing."""
    # Create descriptor without checksum first
    descriptor = BinaryCapabilityDescriptor(
        magic=b"TCP\x01",
        checksum=0,  # Will be calculated
        capability_flags=0x0001,
        command_count=1,
        format_count=2,
        reserved=b"\x00\x00",
        max_file_size_mb=100,
        avg_processing_time_ms=436,
    )

    # Calculate correct checksum
    import zlib

    data = descriptor.to_bytes()
    data_without_checksum = data[:4] + data[8:]  # Skip checksum field
    descriptor.checksum = zlib.crc32(data_without_checksum) & 0xFFFFFFFF

    return descriptor


@pytest.fixture
def tcp_protocol() -> ToolCapabilityProtocol:
    """Provides configured TCP protocol instance."""
    return ToolCapabilityProtocol()


@pytest.fixture
def tcp_registry() -> CapabilityRegistry:
    """Provides clean TCP registry for testing."""
    registry = CapabilityRegistry()
    # CapabilityRegistry doesn't have a reset method, just return new instance
    return registry


@pytest.fixture
def tcp_generator() -> TCPDescriptorGenerator:
    """Provides TCP generator for capability testing."""
    return TCPDescriptorGenerator()


@pytest.fixture
def mock_llm_extractor():
    """Mock LLM extractor for testing without external dependencies."""
    mock = Mock()
    mock.extract_capabilities.return_value = {
        "name": "test_tool",
        "description": "Test tool for validation",
        "parameters": [],
        "security_level": "SAFE",
        "security_flags": [],
    }
    return mock


@pytest.fixture
def performance_test_data() -> Dict[str, Any]:
    """Provides performance testing data and thresholds."""
    return {
        "compression_ratio_threshold": 350.0,  # 350:1 minimum
        "decision_time_threshold_ns": 1000000,  # 1ms maximum
        "memory_usage_threshold_bytes": 1048576,  # 1MB maximum
        "benchmark_tools": [
            "grep",
            "sed",
            "awk",
            "cat",
            "ls",
            "find",
            "sort",
            "uniq",
            "wc",
            "head",
        ],
        "expected_performance": {"cpu_ns": 500000, "gpu_ns": 250000, "fpga_ns": 100000},
    }


@pytest.fixture
def security_test_data() -> Dict[str, Any]:
    """Provides security testing scenarios and expected outcomes."""
    return {
        "safe_commands": ["ls", "cat", "grep", "head", "tail"],
        "risky_commands": ["rm", "chmod", "sudo", "dd", "kill"],
        "critical_commands": ["rm -rf", "sudo rm", "dd if=/dev/zero"],
        "security_flags_mapping": {
            "FILE_READ": 0x0001,
            "FILE_WRITE": 0x0002,
            "FILE_DELETE": 0x0004,
            "NETWORK_ACCESS": 0x0008,
            "SYSTEM_MODIFY": 0x0010,
            "REQUIRES_SUDO": 0x0020,
            "DESTRUCTIVE": 0x0040,
            "IRREVERSIBLE": 0x0080,
        },
        "expected_risk_levels": {
            "SAFE": 0,
            "LOW_RISK": 1,
            "MEDIUM_RISK": 2,
            "HIGH_RISK": 3,
            "CRITICAL": 4,
        },
    }


@pytest.fixture
def external_validation_config() -> Dict[str, Any]:
    """Configuration for external validation testing."""
    return {
        "trail_of_bits": {
            "endpoint": "https://api.trailofbits.com/tcp-validation",
            "timeout_seconds": 30,
            "expected_response_format": "json",
        },
        "academic_partners": {
            "stanford": "https://api.stanford.edu/tcp-research",
            "mit": "https://api.mit.edu/tcp-validation",
            "cmu": "https://api.cmu.edu/tcp-research",
            "berkeley": "https://api.berkeley.edu/tcp-validation",
        },
        "commercial_labs": {
            "intel": "https://api.intel.com/tcp-benchmark",
            "aws": "https://api.aws.com/tcp-validation",
            "google": "https://api.google.com/tcp-research",
            "microsoft": "https://api.microsoft.com/tcp-benchmark",
        },
    }


@pytest.fixture
def researcher_validation_frameworks() -> Dict[str, Any]:
    """Validation frameworks for each TCP researcher."""
    return {
        "elena_vasquez": {
            "statistical_significance_threshold": 0.95,
            "sample_size_minimum": 1000,
            "effect_size_threshold": 0.8,
            "test_types": ["t_test", "chi_square", "anova", "regression"],
        },
        "yuki_tanaka": {
            "performance_precision_ns": 100,
            "benchmark_iterations": 10000,
            "hardware_backends": ["cpu", "gpu", "fpga"],
            "latency_targets": {"cpu": 500000, "gpu": 250000, "fpga": 100000},
        },
        "aria_blackwood": {
            "security_scan_tools": ["bandit", "safety", "semgrep"],
            "vulnerability_threshold": "HIGH",
            "penetration_test_scenarios": 50,
            "post_quantum_algorithms": ["kyber", "dilithium", "falcon"],
        },
        "marcus_chen": {
            "node_count_range": [3, 100],
            "byzantine_fault_tolerance": 0.33,
            "consensus_algorithms": ["raft", "pbft", "tendermint"],
            "network_partition_scenarios": 20,
        },
        "alex_rivera": {
            "test_coverage_minimum": 0.95,
            "code_quality_threshold": 9.0,
            "external_validation_partners": 14,
            "reliability_target": 0.99999,
        },
        "sam_mitchell": {
            "hardware_platforms": ["x86_64", "arm64", "risc_v"],
            "deployment_environments": ["bare_metal", "container", "vm"],
            "integration_test_scenarios": 100,
            "infrastructure_uptime_target": 0.9999,
        },
    }


@pytest.fixture
def reliability_test_scenarios() -> List[Dict[str, Any]]:
    """99.999% reliability testing scenarios."""
    return [
        {
            "name": "high_concurrency_load",
            "description": "Test TCP under high concurrent access",
            "concurrent_users": 10000,
            "duration_seconds": 3600,
            "expected_success_rate": 0.99999,
        },
        {
            "name": "memory_pressure_test",
            "description": "Test TCP under memory pressure",
            "memory_limit_mb": 128,
            "tool_count": 100000,
            "expected_degradation_threshold": 0.05,
        },
        {
            "name": "network_partition_recovery",
            "description": "Test TCP recovery from network partitions",
            "partition_duration_seconds": 300,
            "recovery_time_threshold_seconds": 60,
            "data_consistency_check": True,
        },
        {
            "name": "hardware_failure_simulation",
            "description": "Test TCP behavior during hardware failures",
            "failure_types": ["cpu", "memory", "disk", "network"],
            "graceful_degradation_required": True,
            "recovery_validation": True,
        },
        {
            "name": "byzantine_attack_resistance",
            "description": "Test TCP resistance to Byzantine attacks",
            "malicious_node_percentage": 0.33,
            "attack_vectors": [
                "data_corruption",
                "timing_attacks",
                "resource_exhaustion",
            ],
            "security_threshold_maintained": True,
        },
    ]


@pytest.fixture(autouse=True)
def setup_test_environment(tmp_path):
    """Automatically sets up clean test environment for each test."""
    # Create temporary test directories
    (tmp_path / "test_data").mkdir(exist_ok=True)
    (tmp_path / "test_output").mkdir(exist_ok=True)
    (tmp_path / "test_cache").mkdir(exist_ok=True)

    # Set environment variables for testing
    import os

    os.environ["TCP_TEST_MODE"] = "true"
    os.environ["TCP_TEST_DATA_DIR"] = str(tmp_path / "test_data")
    os.environ["TCP_TEST_OUTPUT_DIR"] = str(tmp_path / "test_output")
    os.environ["TCP_TEST_CACHE_DIR"] = str(tmp_path / "test_cache")

    yield

    # Cleanup
    os.environ.pop("TCP_TEST_MODE", None)
    os.environ.pop("TCP_TEST_DATA_DIR", None)
    os.environ.pop("TCP_TEST_OUTPUT_DIR", None)
    os.environ.pop("TCP_TEST_CACHE_DIR", None)


# Performance testing utilities
def performance_benchmark(func, iterations: int = 1000, warmup: int = 100):
    """Utility function for performance benchmarking."""
    import time

    # Warmup
    for _ in range(warmup):
        func()

    # Actual timing
    start_time = time.perf_counter_ns()
    for _ in range(iterations):
        func()
    end_time = time.perf_counter_ns()

    return (end_time - start_time) / iterations


# Security testing utilities
def security_scan_result(command: str, expected_risk: str) -> Dict[str, Any]:
    """Utility function for security testing validation."""
    return {
        "command": command,
        "expected_risk": expected_risk,
        "timestamp": "2025-07-05T22:00:00Z",
        "scan_version": "1.0.0",
    }


# External validation utilities
def mock_external_validation_response(success: bool = True, data: Any = None):
    """Mock external validation API responses."""
    return {
        "success": success,
        "data": data or {},
        "timestamp": "2025-07-05T22:00:00Z",
        "validation_id": "test-validation-12345",
    }


# Test markers for pytest
# Use: @pytest.mark.external_validation
pytestmark = [
    pytest.mark.unit,
    pytest.mark.integration,
    pytest.mark.performance,
    pytest.mark.security,
    pytest.mark.external_validation,
    pytest.mark.reliability_99999,
]
