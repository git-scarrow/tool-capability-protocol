"""Core TCP protocol implementation."""

from .descriptors import (
    BinaryCapabilityDescriptor,
    CapabilityDescriptor,
    CommandDescriptor,
    FormatDescriptor,
    ParameterDescriptor,
)
from .discovery import DiscoveryService
from .protocol import ToolCapabilityProtocol
from .registry import CapabilityRegistry
from .snf import SNFCanonicalizer, SNFError
from .tlv_evidence import EvidenceTLV, compute_evidence_id
from .tlv_policy import PolicyTLV
from .telemetry import record_hist
from .signing_surface import canonical_bytes_without_evidence

__all__ = [
    "ToolCapabilityProtocol",
    "CapabilityDescriptor",
    "BinaryCapabilityDescriptor",
    "CommandDescriptor",
    "ParameterDescriptor",
    "FormatDescriptor",
    "CapabilityRegistry",
    "DiscoveryService",
    "SNFCanonicalizer",
    "SNFError",
    "EvidenceTLV",
    "PolicyTLV",
    "compute_evidence_id",
    "record_hist",
    "canonical_bytes_without_evidence",
]
