"""Tests for the Anthropic schema bridge."""

from __future__ import annotations

import pytest

from tcp.core.descriptors import (
    CapabilityDescriptor,
    CapabilityFlags,
    CommandDescriptor,
    FormatDescriptor,
    FormatType,
    ParameterDescriptor,
    ParameterType,
    PerformanceMetrics,
    ProcessingMode,
)
from tcp.harness.corpus import CorpusEntry
from tcp.harness.models import ToolRecord
from tcp.harness.schema_bridge import (
    corpus_to_anthropic_schemas,
    tool_record_to_anthropic_schema,
)


def _make_descriptor(
    name: str,
    *,
    commands: list[str] | None = None,
    flags: int = 0,
    description: str = "",
    parameters: list[ParameterDescriptor] | None = None,
) -> CapabilityDescriptor:
    """Test helper to build a minimal descriptor."""
    cmds = []
    for c in (commands or [name]):
        cmd = CommandDescriptor(name=c, parameters=parameters or [])
        cmds.append(cmd)
    return CapabilityDescriptor(
        name=name,
        version="1.0",
        description=description,
        commands=cmds,
        input_formats=[FormatDescriptor(name="json", type=FormatType.JSON)],
        output_formats=[FormatDescriptor(name="json", type=FormatType.JSON)],
        processing_modes=[ProcessingMode.SYNC],
        capability_flags=flags,
        performance=PerformanceMetrics(avg_processing_time_ms=100, memory_usage_mb=16),
    )


def _make_entry(
    descriptor: CapabilityDescriptor,
    source: str = "test",
    category: str = "test",
) -> CorpusEntry:
    return CorpusEntry(descriptor=descriptor, source=source, category=category)


class TestToolRecordToAnthropicSchema:
    """Tests for tool_record_to_anthropic_schema."""

    def test_basic_structure(self):
        record = ToolRecord(
            tool_name="fs-read-file",
            descriptor_source="test",
            descriptor_version="1.0",
            capability_flags=0,
            risk_level="safe",
            commands=frozenset({"read_file"}),
            input_formats=frozenset({"text"}),
            output_formats=frozenset({"text"}),
        )
        schema = tool_record_to_anthropic_schema(record)

        assert schema["name"] == "fs-read-file"
        assert isinstance(schema["description"], str)
        assert len(schema["description"]) > 0
        assert "input_schema" in schema
        assert schema["input_schema"]["type"] == "object"
        assert "properties" in schema["input_schema"]

    def test_auth_required_annotation(self):
        record = ToolRecord(
            tool_name="chmod",
            descriptor_source="test",
            descriptor_version="1.0",
            capability_flags=int(CapabilityFlags.AUTH_REQUIRED),
            risk_level="approval_required",
            commands=frozenset({"chmod"}),
        )
        schema = tool_record_to_anthropic_schema(record)
        assert "[APPROVAL REQUIRED]" in schema["description"]

    def test_no_auth_annotation_when_not_required(self):
        record = ToolRecord(
            tool_name="jq",
            descriptor_source="test",
            descriptor_version="1.0",
            capability_flags=0,
            risk_level="safe",
            commands=frozenset({"jq"}),
        )
        schema = tool_record_to_anthropic_schema(record)
        assert "[APPROVAL REQUIRED]" not in schema["description"]


class TestCorpusToAnthropicSchemas:
    """Tests for corpus_to_anthropic_schemas."""

    def test_returns_list_of_dicts(self):
        entries = [
            _make_entry(_make_descriptor("tool-a", description="Tool A")),
            _make_entry(_make_descriptor("tool-b", description="Tool B")),
        ]
        schemas = corpus_to_anthropic_schemas(entries)
        assert len(schemas) == 2
        assert all(isinstance(s, dict) for s in schemas)

    def test_schema_structure(self):
        entries = [
            _make_entry(_make_descriptor("tool-a", description="Does A")),
        ]
        schema = corpus_to_anthropic_schemas(entries)[0]
        assert schema["name"] == "tool-a"
        assert "Does A" in schema["description"]
        assert schema["input_schema"]["type"] == "object"

    def test_auth_required_annotation(self):
        entries = [
            _make_entry(
                _make_descriptor(
                    "secure-tool",
                    flags=int(CapabilityFlags.AUTH_REQUIRED),
                    description="Secure operation",
                )
            ),
        ]
        schema = corpus_to_anthropic_schemas(entries)[0]
        assert "[APPROVAL REQUIRED]" in schema["description"]

    def test_parameters_mapped_to_input_schema(self):
        params = [
            ParameterDescriptor(
                name="path",
                type=ParameterType.STRING,
                required=True,
                description="File path",
            ),
            ParameterDescriptor(
                name="encoding",
                type=ParameterType.STRING,
                required=False,
                description="File encoding",
            ),
        ]
        entries = [
            _make_entry(
                _make_descriptor("reader", description="Read", parameters=params)
            ),
        ]
        schema = corpus_to_anthropic_schemas(entries)[0]
        props = schema["input_schema"]["properties"]
        assert "path" in props
        assert props["path"]["type"] == "string"
        assert "encoding" in props
        required = schema["input_schema"].get("required", [])
        assert "path" in required
        assert "encoding" not in required

    def test_schema_parity_invariant(self):
        """Both arms must use schemas from the same generation."""
        entries = [
            _make_entry(_make_descriptor("tool-a", description="A")),
            _make_entry(_make_descriptor("tool-b", description="B")),
            _make_entry(_make_descriptor("tool-c", description="C")),
        ]
        all_schemas = corpus_to_anthropic_schemas(entries)
        # Simulate filtered arm: subset by name
        filtered_names = {"tool-a", "tool-c"}
        filtered = [s for s in all_schemas if s["name"] in filtered_names]
        # Verify schemas are identical objects from the same list
        assert filtered[0] is all_schemas[0]
        assert filtered[1] is all_schemas[2]

    def test_mt3_corpus_coverage(self):
        """At least 80% of MT-3 corpus (72+ of 90) should produce valid schemas."""
        from tcp.harness.corpus import build_mcp_corpus

        entries = build_mcp_corpus()
        schemas = corpus_to_anthropic_schemas(entries)
        valid = [
            s
            for s in schemas
            if s.get("name")
            and s.get("description")
            and isinstance(s.get("input_schema"), dict)
            and s["input_schema"].get("type") == "object"
        ]
        coverage = len(valid) / len(entries) if entries else 0
        assert coverage >= 0.80, (
            f"Schema bridge coverage {coverage:.1%} below 80% threshold "
            f"({len(valid)}/{len(entries)})"
        )
