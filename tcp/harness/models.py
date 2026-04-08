"""Canonical harness data models."""

from dataclasses import dataclass, field
from typing import Any, FrozenSet, Mapping, Optional, Tuple


@dataclass(frozen=True)
class ToolRecord:
    """Canonical in-memory representation of a tool for harness routing."""

    tool_name: str
    descriptor_source: str
    descriptor_version: str
    capability_flags: int
    risk_level: str
    commands: FrozenSet[str] = field(default_factory=frozenset)
    input_formats: FrozenSet[str] = field(default_factory=frozenset)
    output_formats: FrozenSet[str] = field(default_factory=frozenset)
    processing_modes: FrozenSet[str] = field(default_factory=frozenset)
    permission_level: str = "unknown"
    avg_processing_time_ms: float = 1000.0
    memory_usage_mb: float = 512.0
    policy_tlvs: Tuple[bytes, ...] = field(default_factory=tuple)
    evidence_tlvs: Tuple[bytes, ...] = field(default_factory=tuple)
    rich_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolSelectionRequest:
    """Selection requirements for a task.

    ``required_capability_flags`` carries the *union* of hard + heuristic bits
    for backward-compatibility with benchmark/offline consumers that treat
    everything as a hard gate.

    ``heuristic_capability_flags`` holds the prompt-derived bits that are useful
    for scoring/audit but unsafe as live hard requirements (e.g. SUPPORTS_NETWORK
    inferred from the word "endpoint").  Live-conservative mode should NOT use
    these for rejection.
    """

    required_commands: FrozenSet[str] = field(default_factory=frozenset)
    required_input_formats: FrozenSet[str] = field(default_factory=frozenset)
    required_output_formats: FrozenSet[str] = field(default_factory=frozenset)
    required_processing_modes: FrozenSet[str] = field(default_factory=frozenset)
    required_capability_flags: int = 0
    heuristic_capability_flags: int = 0
    preferred_criteria: str = "speed"
    require_auto_approval: bool = True
    task_metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def hard_capability_flags(self) -> int:
        """Capability flags safe for live rejection (environment-derived only)."""
        return self.required_capability_flags & ~self.heuristic_capability_flags

    @classmethod
    def from_kwargs(
        cls,
        *,
        required_commands: Optional[set[str]] = None,
        required_input_formats: Optional[set[str]] = None,
        required_output_formats: Optional[set[str]] = None,
        required_processing_modes: Optional[set[str]] = None,
        required_capability_flags: int = 0,
        heuristic_capability_flags: int = 0,
        preferred_criteria: str = "speed",
        require_auto_approval: bool = True,
        task_metadata: Optional[Mapping[str, Any]] = None,
    ) -> "ToolSelectionRequest":
        """Helper to create immutable requests from common mutable inputs."""
        return cls(
            required_commands=frozenset(required_commands or ()),
            required_input_formats=frozenset(required_input_formats or ()),
            required_output_formats=frozenset(required_output_formats or ()),
            required_processing_modes=frozenset(required_processing_modes or ()),
            required_capability_flags=required_capability_flags,
            heuristic_capability_flags=heuristic_capability_flags,
            preferred_criteria=preferred_criteria,
            require_auto_approval=require_auto_approval,
            task_metadata=dict(task_metadata or {}),
        )
