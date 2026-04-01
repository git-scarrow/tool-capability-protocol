"""Normalize existing TCP descriptor shapes into canonical tool records."""

from __future__ import annotations

import struct
from typing import Any, Iterable, Mapping, Optional

from tcp.core.descriptors import (
    BinaryCapabilityDescriptor,
    CapabilityDescriptor,
    CapabilityFlags,
    FormatDescriptor,
    ProcessingMode,
)

from .models import ToolRecord


_PRIVILEGED_DEPENDENCIES = frozenset({"sudo", "ssh", "su", "doas", "pkexec"})


def normalize_capability_descriptor(
    descriptor: CapabilityDescriptor,
    *,
    permission_level: str = "unknown",
    policy_tlvs: Optional[Iterable[bytes]] = None,
    evidence_tlvs: Optional[Iterable[bytes]] = None,
    rich_metadata: Optional[Mapping[str, Any]] = None,
) -> ToolRecord:
    """Normalize a structured capability descriptor into a ToolRecord."""
    capability_flags = descriptor.capability_flags or descriptor.get_capability_flags()

    # Encode privileged dependencies into capability_flags so the bitmask
    # hot path can gate on them without inspecting string metadata.
    dep_names = {d.lower() for d in descriptor.dependencies}
    if dep_names & _PRIVILEGED_DEPENDENCIES:
        capability_flags |= CapabilityFlags.AUTH_REQUIRED

    commands = frozenset(command.name for command in descriptor.commands)
    input_formats = _format_names(descriptor.input_formats)
    output_formats = _format_names(descriptor.output_formats)
    processing_modes = frozenset(
        _processing_mode_name(mode) for mode in descriptor.processing_modes
    )

    metadata = {
        "fingerprint": descriptor.get_fingerprint(),
        "schema_version": descriptor.schema_version,
        "description_present": bool(descriptor.description),
    }
    if rich_metadata:
        metadata.update(rich_metadata)

    return ToolRecord(
        tool_name=descriptor.name,
        descriptor_source="capability_descriptor",
        descriptor_version=descriptor.version,
        capability_flags=capability_flags,
        risk_level=_derive_structured_risk_level(
            capability_flags=capability_flags, permission_level=permission_level
        ),
        commands=commands,
        input_formats=input_formats,
        output_formats=output_formats,
        processing_modes=processing_modes,
        permission_level=permission_level,
        avg_processing_time_ms=float(descriptor.performance.avg_processing_time_ms),
        memory_usage_mb=float(descriptor.performance.memory_usage_mb),
        policy_tlvs=tuple(policy_tlvs or ()),
        evidence_tlvs=tuple(evidence_tlvs or ()),
        rich_metadata=metadata,
    )


def normalize_binary_descriptor(
    tool_name: str,
    binary: BinaryCapabilityDescriptor,
    *,
    permission_level: str = "unknown",
    rich_metadata: Optional[Mapping[str, Any]] = None,
) -> ToolRecord:
    """Normalize the repo's compact 20-byte binary descriptor shape."""
    metadata = {
        "command_count": binary.command_count,
        "format_count": binary.format_count,
    }
    if rich_metadata:
        metadata.update(rich_metadata)

    return ToolRecord(
        tool_name=tool_name,
        descriptor_source="binary_capability_descriptor",
        descriptor_version=binary.magic.decode("latin1"),
        capability_flags=binary.capability_flags,
        risk_level=_derive_structured_risk_level(
            capability_flags=binary.capability_flags, permission_level=permission_level
        ),
        permission_level=permission_level,
        avg_processing_time_ms=float(binary.avg_processing_time_ms),
        memory_usage_mb=0.0,
        rich_metadata=metadata,
    )


def normalize_legacy_tcp_descriptor(
    tool_name: str,
    data: bytes,
    *,
    permission_level: str = "unknown",
    rich_metadata: Optional[Mapping[str, Any]] = None,
) -> ToolRecord:
    """Normalize the 24-byte legacy TCP v2 descriptor used in research artifacts."""
    if len(data) != 24:
        raise ValueError(f"Expected 24-byte legacy TCP descriptor, got {len(data)}")

    magic = data[:4]
    if magic != b"TCP\x02":
        raise ValueError(f"Unsupported legacy TCP magic: {magic!r}")

    security_flags = struct.unpack(">I", data[10:14])[0]
    exec_time_ms, memory_mb, output_kb = struct.unpack(">IHH", data[14:22])

    metadata = {"output_kb": output_kb}
    if rich_metadata:
        metadata.update(rich_metadata)

    return ToolRecord(
        tool_name=tool_name,
        descriptor_source="legacy_tcp_descriptor",
        descriptor_version="2.0",
        capability_flags=security_flags,
        risk_level=_derive_legacy_risk_level(security_flags),
        permission_level=permission_level,
        avg_processing_time_ms=float(exec_time_ms),
        memory_usage_mb=float(memory_mb),
        rich_metadata=metadata,
    )


def _format_names(formats: Iterable[FormatDescriptor]) -> frozenset[str]:
    names = set()
    for format_descriptor in formats:
        names.add(format_descriptor.name.lower())
        names.update(extension.lower() for extension in format_descriptor.extensions)
    return frozenset(names)


def _processing_mode_name(mode: ProcessingMode | int) -> str:
    if isinstance(mode, ProcessingMode):
        return mode.name.lower()
    return ProcessingMode(mode).name.lower()


def _derive_structured_risk_level(*, capability_flags: int, permission_level: str) -> str:
    """Derive a conservative risk label from the structured descriptor surface."""
    if permission_level in {"denied", "execute_full"}:
        return "approval_required"
    if capability_flags & CapabilityFlags.SUPPORTS_NETWORK:
        return "approval_required"
    if capability_flags & CapabilityFlags.AUTH_REQUIRED:
        return "approval_required"
    return "unknown"


def _derive_legacy_risk_level(security_flags: int) -> str:
    """Derive the risk label from the v2 security bit field."""
    risk_bits = [
        (4, "critical"),
        (3, "high_risk"),
        (2, "medium_risk"),
        (1, "low_risk"),
        (0, "safe"),
    ]
    for bit, label in risk_bits:
        if security_flags & (1 << bit):
            return label
    return "unknown"
