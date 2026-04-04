"""Tests for RouteConfidence and the layered router split."""

from __future__ import annotations

import pytest

from tcp.harness.router import RouteConfidence, RouteResult, route_tool
from tcp.harness.gating import RuntimeEnvironment
from tcp.harness.models import ToolRecord, ToolSelectionRequest


def _make_record(name: str, commands: frozenset[str] = frozenset()) -> ToolRecord:
    return ToolRecord(
        tool_name=name,
        descriptor_source="test",
        descriptor_version="1.0",
        capability_flags=0,
        risk_level="safe",
        commands=commands,
    )


def _make_env() -> RuntimeEnvironment:
    return RuntimeEnvironment(
        network_enabled=False,
        file_access_enabled=True,
        stdin_enabled=True,
        installed_tools=frozenset(),
    )


class TestRouteConfidence:
    """RouteConfidence enum values."""

    def test_enum_values(self):
        assert RouteConfidence.DETERMINISTIC.value == "deterministic"
        assert RouteConfidence.AMBIGUOUS.value == "ambiguous"
        assert RouteConfidence.NO_MATCH.value == "no_match"


class TestRouteResultConfidence:
    """route_tool sets confidence based on survivor count."""

    def test_deterministic_when_one_survivor(self):
        tools = [_make_record("only-tool", commands=frozenset({"do_thing"}))]
        request = ToolSelectionRequest.from_kwargs(
            required_commands={"do_thing"},
            require_auto_approval=False,
        )
        result = route_tool(tools, request, _make_env())
        assert result.confidence == RouteConfidence.DETERMINISTIC
        assert result.survivor_count == 1
        assert result.selected_tool is not None

    def test_ambiguous_when_multiple_survivors(self):
        tools = [
            _make_record("tool-a"),
            _make_record("tool-b"),
        ]
        request = ToolSelectionRequest.from_kwargs(
            preferred_criteria="speed",
            require_auto_approval=False,
        )
        result = route_tool(tools, request, _make_env())
        assert result.confidence == RouteConfidence.AMBIGUOUS
        assert result.survivor_count == 2

    def test_no_match_when_zero_survivors(self):
        tools = [_make_record("tool-a", commands=frozenset({"x"}))]
        request = ToolSelectionRequest.from_kwargs(
            required_commands={"nonexistent"},
            require_auto_approval=False,
        )
        result = route_tool(tools, request, _make_env())
        assert result.confidence == RouteConfidence.NO_MATCH
        assert result.survivor_count == 0

    def test_candidate_scores_none_by_default(self):
        tools = [_make_record("t")]
        request = ToolSelectionRequest.from_kwargs(require_auto_approval=False)
        result = route_tool(tools, request, _make_env())
        assert result.candidate_scores is None
        assert result.score_gap is None
