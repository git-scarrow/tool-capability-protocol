"""Regression tests for descriptor serialization."""

from tcp.core.descriptors import (
    CapabilityDescriptor,
    CommandDescriptor,
    FormatDescriptor,
    FormatType,
    ProcessingMode,
)


def test_capability_descriptor_to_dict_serializes_enums_without_recursion():
    descriptor = CapabilityDescriptor(
        name="jq",
        version="1.0",
        commands=[CommandDescriptor(name="jq", processing_modes=[ProcessingMode.SYNC])],
        input_formats=[FormatDescriptor(name="json", type=FormatType.JSON)],
        output_formats=[FormatDescriptor(name="json", type=FormatType.JSON)],
        processing_modes=[ProcessingMode.SYNC],
    )

    payload = descriptor.to_dict()

    assert payload["input_formats"][0]["type"] == FormatType.JSON.value
    assert payload["processing_modes"][0] == ProcessingMode.SYNC.value
    assert payload["commands"][0]["processing_modes"][0] == ProcessingMode.SYNC.value
