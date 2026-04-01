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
from .telemetry import record_hist
from .signing_surface import canonical_bytes_without_evidence

# The TLV helpers depend on optional CBOR support. Keep the rest of the TCP
# package importable even when that extra dependency is absent.
try:
    from .tlv_evidence import EvidenceTLV, compute_evidence_id
    from .tlv_policy import PolicyTLV
except ModuleNotFoundError as exc:
    if exc.name != "cbor2":
        raise
    EvidenceTLV = None
    PolicyTLV = None
    compute_evidence_id = None

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
    "record_hist",
    "canonical_bytes_without_evidence",
]

if EvidenceTLV is not None:
    __all__.extend(["EvidenceTLV", "PolicyTLV", "compute_evidence_id"])
