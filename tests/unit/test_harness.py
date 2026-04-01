"""Unit tests for the initial TCP harness slice."""

from tcp.core.descriptors import (
    BinaryCapabilityDescriptor,
    CapabilityDescriptor,
    CapabilityFlags,
    CommandDescriptor,
    FormatDescriptor,
    FormatType,
    PerformanceMetrics,
    ProcessingMode,
)
from tcp.harness import (
    RuntimeEnvironment,
    ToolSelectionRequest,
    gate_tools,
    normalize_binary_descriptor,
    normalize_capability_descriptor,
    normalize_legacy_tcp_descriptor,
    project_tool,
    route_tool,
)


def test_normalize_capability_descriptor_collects_hot_path_fields():
    descriptor = CapabilityDescriptor(
        name="jq",
        version="1.7",
        description="JSON processor",
        commands=[CommandDescriptor(name="jq")],
        input_formats=[FormatDescriptor(name="json", type=FormatType.JSON)],
        output_formats=[FormatDescriptor(name="json", type=FormatType.JSON)],
        processing_modes=[ProcessingMode.SYNC],
        performance=PerformanceMetrics(avg_processing_time_ms=25, memory_usage_mb=16),
    )

    record = normalize_capability_descriptor(descriptor, permission_level="read_only")

    assert record.tool_name == "jq"
    assert "jq" in record.commands
    assert "json" in record.input_formats
    assert "sync" in record.processing_modes
    assert record.avg_processing_time_ms == 25.0
    assert record.risk_level == "unknown"


def test_normalize_legacy_tcp_descriptor_derives_risk_level():
    descriptor_bytes = (
        b"TCP\x02"
        + b"\x00\x02"
        + b"\x12\x34\x56\x78"
        + (1 << 4).to_bytes(4, "big")
        + (500).to_bytes(4, "big")
        + (32).to_bytes(2, "big")
        + (4).to_bytes(2, "big")
        + b"\x00\x00"
    )

    record = normalize_legacy_tcp_descriptor("rm", descriptor_bytes)

    assert record.tool_name == "rm"
    assert record.risk_level == "critical"
    assert record.avg_processing_time_ms == 500.0
    assert record.memory_usage_mb == 32.0


def test_gate_tools_rejects_network_when_environment_disallows_it():
    curl = normalize_capability_descriptor(
        CapabilityDescriptor(
            name="curl",
            version="8.0",
            capability_flags=int(CapabilityFlags.SUPPORTS_NETWORK),
        ),
        permission_level="read_only",
    )

    result = gate_tools(
        [curl],
        ToolSelectionRequest.from_kwargs(),
        RuntimeEnvironment(network_enabled=False),
    )

    assert not result.approved_tools
    assert result.rejected_tools[0].tool_name == "curl"
    assert result.audit_log[0].reason == "network access disabled"


def test_gate_tools_approves_safe_matching_tool_and_router_selects_fastest():
    fast_tool = normalize_capability_descriptor(
        CapabilityDescriptor(
            name="fast-json",
            version="1.0",
            commands=[CommandDescriptor(name="format")],
            input_formats=[FormatDescriptor(name="json", type=FormatType.JSON)],
            output_formats=[FormatDescriptor(name="json", type=FormatType.JSON)],
            processing_modes=[ProcessingMode.SYNC],
            performance=PerformanceMetrics(avg_processing_time_ms=10, memory_usage_mb=8),
        ),
        permission_level="read_only",
    )
    slow_tool = normalize_capability_descriptor(
        CapabilityDescriptor(
            name="slow-json",
            version="1.0",
            commands=[CommandDescriptor(name="format")],
            input_formats=[FormatDescriptor(name="json", type=FormatType.JSON)],
            output_formats=[FormatDescriptor(name="json", type=FormatType.JSON)],
            processing_modes=[ProcessingMode.SYNC],
            performance=PerformanceMetrics(avg_processing_time_ms=50, memory_usage_mb=4),
        ),
        permission_level="read_only",
    )

    request = ToolSelectionRequest.from_kwargs(
        required_commands={"format"},
        required_input_formats={"json"},
        preferred_criteria="speed",
        require_auto_approval=False,
    )

    routed = route_tool(
        [slow_tool, fast_tool],
        request,
        RuntimeEnvironment(installed_tools=frozenset({"fast-json", "slow-json"})),
    )

    assert routed.selected_tool is not None
    assert routed.selected_tool.tool_name == "fast-json"
    assert len(routed.approved) == 2


def test_unknown_risk_requires_approval_when_auto_approval_enabled():
    descriptor = CapabilityDescriptor(
        name="mystery",
        version="1.0",
        commands=[CommandDescriptor(name="run")],
    )
    record = normalize_capability_descriptor(descriptor, permission_level="read_only")

    result = gate_tools(
        [record],
        ToolSelectionRequest.from_kwargs(
            required_commands={"run"},
            require_auto_approval=True,
        ),
        RuntimeEnvironment(installed_tools=frozenset({"mystery"})),
    )

    assert not result.approved_tools
    assert result.approval_required_tools[0].tool_name == "mystery"


def test_project_tool_keeps_prompt_surface_compact():
    binary = BinaryCapabilityDescriptor(
        capability_flags=int(CapabilityFlags.SUPPORTS_FILES | CapabilityFlags.JSON_OUTPUT),
        command_count=1,
        format_count=1,
        max_file_size_mb=10,
        avg_processing_time_ms=5,
    )
    binary.checksum = 0
    record = normalize_binary_descriptor("mini", binary, permission_level="read_only")

    projected = project_tool(record)

    assert projected["tool_name"] == "mini"
    assert projected["approval_required"] is True
    assert "risk:unknown" in projected["constraints"]
