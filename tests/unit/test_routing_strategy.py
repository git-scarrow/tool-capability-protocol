"""Tests for the LLM bypass strategy."""

from __future__ import annotations

from tcp.agent.routing_strategy import should_bypass_llm
from tcp.harness.router import RouteConfidence, RouteResult


def _make_route_result(confidence: RouteConfidence, survivor_count: int = 1) -> RouteResult:
    return RouteResult(
        selected_tool=None,
        confidence=confidence,
        survivor_count=survivor_count,
    )


class TestShouldBypassLlm:
    """Default bypass strategy: bypass when DETERMINISTIC."""

    def test_bypass_on_deterministic(self):
        result = _make_route_result(RouteConfidence.DETERMINISTIC, survivor_count=1)
        assert should_bypass_llm(result) is True

    def test_no_bypass_on_ambiguous(self):
        result = _make_route_result(RouteConfidence.AMBIGUOUS, survivor_count=3)
        assert should_bypass_llm(result) is False

    def test_no_bypass_on_no_match(self):
        result = _make_route_result(RouteConfidence.NO_MATCH, survivor_count=0)
        assert should_bypass_llm(result) is False
