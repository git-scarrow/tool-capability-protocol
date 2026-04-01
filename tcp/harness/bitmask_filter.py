"""Hot-path bitmask filter for TCP tool records.

This module provides O(1)-per-tool filtering using only bitwise operations
on the 32-bit capability_flags field from the 20-byte TCP descriptor.
No strings, no JSON, no parsing — just integer AND.

Three-tier gating:
    deny_mask     → hard reject (environment cannot provide this capability)
    approval_mask → soft gate  (capability available but requires human approval)
    require_mask  → hard require (tool must have these capabilities)

Usage:
    deny     = EnvironmentMask.from_constraints(network=False)
    approval = CapabilityFlags.AUTH_REQUIRED | CapabilityFlags.SUPPORTS_NETWORK
    result   = bitmask_filter(tools, deny_mask=deny, approval_mask=approval)
    prompt_tools = [project_tool(t) for t in result.approved]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from tcp.core.descriptors import CapabilityFlags

from .models import ToolRecord


class EnvironmentMask:
    """Build a 32-bit deny mask from environment constraints.

    Each disabled capability sets the corresponding flag bit.  A tool whose
    capability_flags shares any bit with the deny mask is rejected.
    """

    __slots__ = ("_mask",)

    def __init__(self, mask: int = 0) -> None:
        self._mask = mask & 0xFFFF_FFFF

    @classmethod
    def from_constraints(
        cls,
        *,
        network: bool = True,
        file_access: bool = True,
        stdin: bool = True,
        gpu: bool = True,
    ) -> EnvironmentMask:
        """Construct deny mask from boolean environment constraints.

        A ``False`` value means the capability is unavailable, so tools
        requiring it must be denied.
        """
        mask = 0
        if not network:
            mask |= CapabilityFlags.SUPPORTS_NETWORK
        if not file_access:
            mask |= CapabilityFlags.SUPPORTS_FILES
        if not stdin:
            mask |= CapabilityFlags.SUPPORTS_STDIN
        if not gpu:
            mask |= CapabilityFlags.GPU_ACCELERATION
        return cls(mask)

    @property
    def value(self) -> int:
        return self._mask

    def __int__(self) -> int:
        return self._mask

    def __or__(self, other: EnvironmentMask | int) -> EnvironmentMask:
        other_val = int(other)
        return EnvironmentMask(self._mask | other_val)

    def __repr__(self) -> str:
        return f"EnvironmentMask(0x{self._mask:08X})"


@dataclass(frozen=True)
class BitmaskFilterResult:
    """Result of three-tier hot-path bitmask filtering."""

    approved: tuple[ToolRecord, ...]
    approval_required: tuple[ToolRecord, ...]
    rejected: tuple[ToolRecord, ...]
    deny_mask: int
    approval_mask: int
    require_mask: int
    candidate_count: int

    # --- backwards compat: survivors = approved + approval_required ---

    @property
    def survivors(self) -> tuple[ToolRecord, ...]:
        """All non-rejected tools (approved + approval_required)."""
        return self.approved + self.approval_required

    @property
    def survivor_count(self) -> int:
        return len(self.approved) + len(self.approval_required)

    @property
    def approved_count(self) -> int:
        return len(self.approved)

    @property
    def approval_required_count(self) -> int:
        return len(self.approval_required)

    @property
    def rejection_count(self) -> int:
        return len(self.rejected)


def bitmask_filter(
    tools: Iterable[ToolRecord],
    *,
    deny_mask: EnvironmentMask | int = 0,
    approval_mask: int = 0,
    require_mask: int = 0,
) -> BitmaskFilterResult:
    """Filter tools using only bitwise operations on capability_flags.

    Three-tier hot-path contract (evaluated in order):
      1. **Reject** if ``(flags & deny) != 0``
         — tool needs a capability the environment cannot provide.
      2. **Reject** if ``require != 0 and (flags & require) != require``
         — tool lacks a capability the request demands.
      3. **Approval-required** if ``approval != 0 and (flags & approval) != 0``
         — tool has a capability that needs human sign-off.
      4. **Approved** otherwise.

    The approval_mask MUST NOT overlap with the deny_mask.  Bits in
    both masks are treated as deny (hard reject wins).

    No string comparison, no set operations, no attribute inspection beyond
    the integer ``capability_flags`` field.
    """
    deny = int(deny_mask) & 0xFFFF_FFFF
    approval = approval_mask & 0xFFFF_FFFF
    require = require_mask & 0xFFFF_FFFF

    # Hard deny always wins over soft approval.
    effective_approval = approval & ~deny

    approved: list[ToolRecord] = []
    approval_req: list[ToolRecord] = []
    rejected: list[ToolRecord] = []
    count = 0

    for tool in tools:
        count += 1
        flags = tool.capability_flags

        # Hot path: three integer ANDs, three comparisons.
        if (flags & deny) != 0:
            rejected.append(tool)
        elif require != 0 and (flags & require) != require:
            rejected.append(tool)
        elif effective_approval != 0 and (flags & effective_approval) != 0:
            approval_req.append(tool)
        else:
            approved.append(tool)

    return BitmaskFilterResult(
        approved=tuple(approved),
        approval_required=tuple(approval_req),
        rejected=tuple(rejected),
        deny_mask=deny,
        approval_mask=effective_approval,
        require_mask=require,
        candidate_count=count,
    )


def filter_for_prompt(
    tools: Iterable[ToolRecord],
    *,
    deny_mask: EnvironmentMask | int = 0,
    approval_mask: int = 0,
    require_mask: int = 0,
    include_approval_required: bool = True,
) -> list[dict[str, object]]:
    """Bitmask-filter tools and project survivors for LLM prompt injection.

    This is the single entry point a harness integration should call:
    binary filtering on the hot path, then compact projection on the
    cold path for only the survivors.

    When ``include_approval_required`` is True (default), approval-gated
    tools are included in the prompt with an ``approval_required: true``
    annotation so the LLM can request them with human confirmation.
    """
    from .projection import project_tool

    result = bitmask_filter(
        tools,
        deny_mask=deny_mask,
        approval_mask=approval_mask,
        require_mask=require_mask,
    )

    projected = [project_tool(t) for t in result.approved]
    if include_approval_required:
        for tool in result.approval_required:
            entry = project_tool(tool)
            entry["approval_required"] = True
            projected.append(entry)

    return projected
