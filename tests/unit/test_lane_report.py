"""Tests for three-lane benchmark reporting."""

from __future__ import annotations

import pytest

from tcp.agent.lane_report import LaneReport, build_lane_report
from tcp.agent.loop import LoopMetrics


def _make_metrics(
    task_name: str = "test",
    correct: bool = True,
    input_tokens: int = 500,
    bypassed: bool = False,
    confidence: str = "deterministic",
    survivor_count: int = 1,
) -> LoopMetrics:
    return LoopMetrics(
        task_name=task_name,
        tool_count=10,
        turns=0 if bypassed else 2,
        first_token_latency_ms=0.0 if bypassed else 100.0,
        total_response_time_ms=1.0 if bypassed else 200.0,
        input_tokens=0 if bypassed else input_tokens,
        output_tokens=0 if bypassed else 50,
        tools_called=("tool-a",),
        selected_tool_correct=correct,
        error=None,
        llm_bypassed=bypassed,
        route_confidence=confidence,
        survivor_count=survivor_count,
    )


class TestBuildLaneReport:
    """Lane report splits metrics by confidence."""

    def test_deterministic_lane(self):
        metrics = [
            _make_metrics(confidence="deterministic", bypassed=True, correct=True),
            _make_metrics(confidence="deterministic", bypassed=True, correct=True),
        ]
        report = build_lane_report(metrics)
        assert report.deterministic_count == 2
        assert report.deterministic_correct_rate == pytest.approx(1.0)

    def test_ambiguous_lane(self):
        metrics = [
            _make_metrics(confidence="ambiguous", bypassed=False, correct=True, survivor_count=3),
            _make_metrics(confidence="ambiguous", bypassed=False, correct=False, survivor_count=3),
        ]
        report = build_lane_report(metrics)
        assert report.ambiguous_count == 2
        assert report.ambiguous_correct_rate == pytest.approx(0.5)

    def test_no_match_lane(self):
        metrics = [
            _make_metrics(confidence="no_match", bypassed=False, correct=True, survivor_count=0),
        ]
        report = build_lane_report(metrics)
        assert report.no_match_count == 1

    def test_bypass_ratio(self):
        metrics = [
            _make_metrics(confidence="deterministic", bypassed=True),
            _make_metrics(confidence="deterministic", bypassed=True),
            _make_metrics(confidence="ambiguous", bypassed=False, survivor_count=3),
        ]
        report = build_lane_report(metrics)
        assert report.bypass_ratio == pytest.approx(2 / 3)

    def test_ambiguous_llm_lift(self):
        metrics = [
            _make_metrics(confidence="ambiguous", bypassed=False, correct=True, survivor_count=3),
            _make_metrics(confidence="ambiguous", bypassed=False, correct=True, survivor_count=3),
        ]
        report = build_lane_report(metrics, select_best_correct_rate=0.5)
        assert report.ambiguous_llm_lift == pytest.approx(0.5)

    def test_empty_metrics(self):
        report = build_lane_report([])
        assert report.deterministic_count == 0
        assert report.ambiguous_count == 0
        assert report.bypass_ratio == 0.0
