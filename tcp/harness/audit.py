"""Audit models for harness gating decisions."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class GatingDecision(str, Enum):
    """Gating decision labels."""

    APPROVED = "approved"
    APPROVAL_REQUIRED = "approval_required"
    REJECTED = "rejected"


@dataclass(frozen=True)
class AuditEntry:
    """Why the harness approved or rejected a tool."""

    tool_name: str
    decision: GatingDecision
    reason: str
    details: Mapping[str, object] = field(default_factory=dict)
