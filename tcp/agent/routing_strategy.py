"""Pluggable routing strategy for LLM bypass decisions.

The default strategy bypasses the LLM when the router resolves
deterministically (exactly 1 survivor).  C3 can swap in a
scoring-aware strategy without touching loop internals.
"""

from __future__ import annotations

from tcp.harness.router import RouteConfidence, RouteResult


def should_bypass_llm(result: RouteResult) -> bool:
    """Default strategy: bypass when exactly 1 survivor."""
    return result.confidence == RouteConfidence.DETERMINISTIC
